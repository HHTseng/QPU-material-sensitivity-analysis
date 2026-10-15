#!/usr/bin/env bash
set -Eeuo pipefail
# Each stage fixes x, sites i, realizations r and N_p primaries per (i,r).

mode=status
if (( $# > 0 )); then mode=$1; fi
if printenv MATERIAL_REPOSITORY >/dev/null; then repository=$(printenv MATERIAL_REPOSITORY)
else repository=$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd -P); fi
material_run="$repository/material_scan_data/full_scale_20261014_revised"
if (( $# > 1 )); then material_run=$2; fi
material_run=$(realpath -m -- "$material_run")
material_python=/home/htseng/.conda/envs/G4CMP/bin/python
if printenv MATERIAL_PYTHON >/dev/null; then material_python=$(printenv MATERIAL_PYTHON); fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-full-scale-mpl
source_dir="$material_run/source"

if [[ "$mode" == run ]]; then
    [[ -f "$material_run/source-ready.json" ]] || bash "$BASH_SOURCE" prepare "$material_run"
    export MATERIAL_REPOSITORY="$repository" MATERIAL_PYTHON="$material_python"
    cpu_list=$("$material_python" -c 'import json,sys;print(",".join(map(str,json.load(open(sys.argv[1]))["cpus"])))' "$material_run/resources.json")
    exec taskset -c "$cpu_list" bash "$source_dir/run_all.sh" execute "$material_run"
fi

if [[ "$mode" == stop ]]; then
    "$material_python" - "$material_run" <<'PY'
import json,os,signal,sys
from pathlib import Path
state=json.loads((Path(sys.argv[1])/'queue_status.json').read_text())
pid=state.get('driver_pid')
path=Path(f'/proc/{pid}/stat')
if pid and path.is_file():
    fields=path.read_text().split(') ',1)[1].split()
    if int(fields[19])==state.get('driver_starttime'): os.kill(pid,signal.SIGINT)
PY
    exit
fi

if [[ "$mode" == finish-stage ]]; then
    "$material_python" - "$material_run" <<'PY'
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]); state=json.loads((root/'queue_status.json').read_text())
pid=state['driver_pid']; fields=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()
if int(fields[19])!=state['driver_starttime']: raise ValueError('the recorded driver has already changed')
parent=int(fields[1]); created=int(Path(f'/proc/{parent}/stat').read_text().split(') ',1)[1].split()[19])
print('Waiting for completed transport:',state['stage'],flush=True)
while Path(f'/proc/{pid}').exists():
    try:
        if int(Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19])!=state['driver_starttime']: break
    except FileNotFoundError: break
    time.sleep(.2)
if Path(f'/proc/{parent}/stat').is_file() and int(Path(f'/proc/{parent}/stat').read_text().split(') ',1)[1].split()[19])==created:
    os.kill(parent,signal.SIGINT)
with (root/'queue.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    report=json.loads((root/'studies'/state['stage']/'summary.json').read_text())
    if not report.get('complete'): raise ValueError('transport stopped before completion; retained tasks can be resumed')
    subprocess.run(['bash',str(root/'source/run_all.sh'),'record',str(root),str(state['index'])],check=True)
    state.update(state='paused',reason='Completed study retained; remaining studies replaced by revised spatial validation.',updated=time.time())
    (root/'queue_status.json').write_text(json.dumps(state,indent=2)+'\n')
print('Old queue paused after verified preservation:',state['stage'],flush=True)
PY
    exit
fi

if [[ "$mode" == execute ]]; then
    cd "$source_dir"
    mkdir -p "$material_run/logs"
    exec 9>"$material_run/queue.lock"
    flock -n 9
    bash "$BASH_SOURCE" verify-source "$material_run"
    driver_pid=
    guardian_pid=
    trap 'if [[ -n "$driver_pid" ]]; then kill -TERM "$driver_pid" 2>/dev/null || true; wait "$driver_pid" || true; fi; if [[ -n "$guardian_pid" ]]; then kill "$guardian_pid" 2>/dev/null || true; fi; exit 130' INT TERM HUP
    count=$("$material_python" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))))' "$material_run/queue.json")
    for (( index=0; index<count; index++ )); do
        study=$(bash "$BASH_SOURCE" stage "$material_run" "$index")
        [[ "$study" != SKIP ]] || continue
        id=$(basename "$study" .yaml)
        output="$material_run/studies/$id"
        while :; do
            rm -f "$material_run/guard.json"
            "$material_python" "$source_dir/scan.py" run "$study" --output "$output" >"$material_run/logs/$id.log" 2>&1 &
            driver_pid=$!
            "$material_python" - "$driver_pid" "$material_run" "$index" <<'PY' &
import json,os,signal,sys,time
from pathlib import Path
import scan,records
pid=int(sys.argv[1]); root=Path(sys.argv[2]); index=int(sys.argv[3])
limits=json.loads((root/'resources.json').read_text())
try: created=int(Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19])
except FileNotFoundError: raise SystemExit(0)
records.write_json(root/'queue_status.json',{'state':'running','index':index,'stage':json.loads((root/'queue.json').read_text())[index]['id'],'driver_pid':pid,'driver_starttime':created,'updated':time.time()},overwrite=True)
while Path(f'/proc/{pid}').exists():
    try:
        if int(Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19])!=created: break
    except FileNotFoundError: break
    processes={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try:
            text=(path/'stat').read_text(); fields=text[text.rfind(')')+2:].split()
            processes[int(path.name)]=(int(fields[1]),int(fields[21])*os.sysconf('SC_PAGE_SIZE'))
        except (OSError,ValueError,IndexError): pass
    descendants={pid}
    while True:
        added={p for p,(parent,_) in processes.items() if parent in descendants}-descendants
        if not added: break
        descendants.update(added)
    rss=sum(processes[p][1] for p in descendants if p in processes)
    memory=scan.memory_headroom(); disk=os.statvfs(root)
    free=disk.f_bavail*disk.f_frsize
    reason='rss' if rss>limits['total_mem_gb']*1024**3 else 'memory_reserve' if memory['available_bytes'] is not None and memory['available_bytes']<limits['system_mem_reserve_gb']*1024**3 else 'disk' if free<limits['minimum_free_disk_gb']*1024**3 else None
    records.write_json(root/'resources-current.json',{'rss_bytes':rss,'free_disk_bytes':free,'memory':memory,'updated':time.time()},overwrite=True)
    if reason:
        records.write_json(root/'guard.json',{'reason':reason,'rss_bytes':rss,'free_disk_bytes':free,'updated':time.time()},overwrite=True)
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        break
    time.sleep(2)
PY
            guardian_pid=$!
            if wait "$driver_pid"; then code=0; else code=$?; fi
            driver_pid=
            kill "$guardian_pid" 2>/dev/null || true
            wait "$guardian_pid" 2>/dev/null || true
            guardian_pid=
            if (( code != 0 )) && [[ ! -f "$material_run/guard.json" ]] && rg -q 'host or cgroup memory reserve reached; restart after memory is available' "$material_run/logs/$id.log"; then
                "$material_python" -c 'import json,sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps({"reason":"memory_reserve","source":"transport monitor"})+"\n")' "$material_run/guard.json"
            fi
            if [[ -f "$material_run/guard.json" ]]; then
                bash "$BASH_SOURCE" pause "$material_run" "$index"
                reason=$("$material_python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["reason"])' "$material_run/guard.json")
                [[ "$reason" != rss ]] || exit 75
                bash "$BASH_SOURCE" wait "$material_run"
                continue
            fi
            if (( code != 0 )); then
                bash "$BASH_SOURCE" failed "$material_run" "$index"
                exit "$code"
            fi
            break
        done
        bash "$BASH_SOURCE" record "$material_run" "$index"
    done
    bash "$BASH_SOURCE" finish "$material_run"
    bash "$BASH_SOURCE" publish "$material_run"
    exit
fi

if [[ "$mode" == publish ]]; then
    cd "$repository"
    "$material_python" - "$material_run" <<'PY'
import json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); state=json.loads((root/'queue_status.json').read_text())
assert state['state']=='complete'
publication=json.loads((root/'publication.json').read_text())
assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==publication['branch']
assert subprocess.check_output(['git','remote','get-url','origin'],text=True).strip()==publication['remote']
files=publication['files']
subprocess.run(['git','add','--',*files],check=True)
if subprocess.run(['git','diff','--cached','--quiet','--',*files]).returncode:
    subprocess.run(['git','commit','--only','-m','Report fresh full-scale material studies','--',*files],check=True)
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
subprocess.run(['git','push','origin','HEAD:'+publication['branch']],check=True)
remote=subprocess.check_output(['git','ls-remote','origin','refs/heads/'+publication['branch']],text=True).split()[0]
assert remote==commit
(root/'published.json').write_text(json.dumps({'commit':commit,'remote_verified':True},indent=2)+'\n')
print('Published verified results:',commit)
PY
    exit
fi

"$material_python" - "$mode" "$repository" "$material_run" "$@" <<'PY'
import copy,gc,hashlib,itertools,json,math,os,resource,shlex,shutil,subprocess,sys,time
from pathlib import Path
mode,repo,run=sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3])
source=run/'source'
sys.path.insert(0,str(source if (run/'source-ready.json').is_file() else repo))
import numpy as np
import yaml
import analysis,physics,records,scan
cfg=records.load_document((source if (run/'source-ready.json').is_file() else repo)/'studies/full_scale/queue.yaml')
defaults=cfg['defaults']
queue=json.loads((run/'queue.json').read_text()) if (run/'queue.json').is_file() else []

def base_study(identifier,sampling,kind='confirmation'):
    raw=records.load_document((source if source.is_dir() else repo)/'studies/reference.yaml')
    raw.update(id=identifier,kind=kind,description='Fresh full-scale '+identifier)
    raw['fixed'].update(temperature_K=defaults['temperature_K'],position_half_span_mm=defaults['source_half_span_mm'],gun_energy_eV=defaults['gun_energy_eV'],setTopGap=defaults['junction_gap_eV'],min_e_phonons_eV=defaults['cutoff_eV'])
    raw['parameters']={'temperature':{'mode':'fixed','value':defaults['temperature_K']}}
    raw['constraints']={'topfilm_gap_min_eV':defaults['nb_gap_min_eV']}
    raw['analysis']={'objective':defaults['objective'],'bootstrap_draws':defaults['bootstrap_draws'],'tail_bootstrap_draws':1000,'store_blocks':False}
    raw['execution']={key:limits[key] for key in ('workers','total_mem_gb','per_task_mem_gb','system_mem_reserve_gb')}
    raw['execution'].update(timeout_s=cfg['execution']['timeout_s'],stall_timeout_s=cfg['execution']['stall_timeout_s'])
    raw['sampling']={'kind':'recorded-stratified','sites':sampling.get('design',sampling.get('sites',512)),'replicas':sampling['replicas'],'events_per_task':sampling['events_per_task'],'seed_base':defaults['seed_base'],'seed_bank':sampling.get('seed_bank',sampling.get('first_seed_bank',201)),'position_seed':defaults['position_seed']}
    if sampling.get('kind')=='sobol': raw['sampling']['kind']='sobol'
    else: raw['sampling']['design_file']=str(run/'designs'/sampling.get('design_file',f"stratified_{raw['sampling']['sites']}.json"))
    if (run/'installation.json').is_file(): raw['installation']=records.load_document(run/'installation.json')
    raw['candidates']=[{'name':'reference','values':{}}]
    return raw

def candidate(name):
    if name=='sic': return {'name':'SiC-derived-rates','values':cfg['sic']['values'],'realization':cfg['sic']['realization']}
    if name=='native-Si-Nb-Cu': return {'name':name,'materials':['Si','Nb','Cu']}
    if name=='c44-1gpa': return {'name':name,'values':{'sub_c44':1.}}
    return {'name':'reference','values':{}}

def selected_finalists():
    selected=[]
    for row in queue:
        if row['kind']!='search': continue
        report=records.load_document(run/'studies'/row['id']/'summary.json')
        if not report['complete'] or not report['search_counts']['target_reached']:
            raise ValueError('every optimizer comparison must reach its measured target before selection')
        point=min((p for p in report['results'] if p['status']=='complete'),key=lambda p:p['value'])
        selected.append({'name':row['id'],'values':point['parameters'],'selected_candidate_id':point['candidate_id'],'search_value':point['value']})
    return selected

def mean_interval(values):
    # y_k are independent position-layout means, including transport noise.
    from scipy.stats import t
    y=np.asarray(values,dtype=float)
    if len(y)<2 or not np.isfinite(y).all(): raise ValueError('at least two complete independent layouts required')
    mean=float(y.mean()); se=float(y.std(ddof=1)/math.sqrt(len(y)))
    radius=float(t.ppf(.975,len(y)-1)*se)
    return {'value':mean,'standard_error':se,'confidence_interval_95':[mean-radius,mean+radius],
            'relative_standard_error':se/abs(mean) if mean else None,'by_layout':y.tolist(),
            'n_layouts':len(y),'uncertainty_method':'Student-t interval across independent source-position layouts'}

def layout_summary(reports):
    # Equal allocation and W_h in every layout; average independent integrals.
    out=copy.deepcopy(reports[0]); names=[row['name'] for row in out['results']]
    if any([row['name'] for row in report['results']]!=names for report in reports):
        raise ValueError('independent layouts must contain the same complete candidates in order')
    for row in out['results']:
        measured=[next(r for r in report['results'] if r['name']==row['name']) for report in reports]
        estimate=mean_interval([r['value'] for r in measured])
        estimate['means_by_stratum']={h:float(np.mean([r['estimate']['means_by_stratum'][h] for r in measured])) for h in measured[0]['estimate']['means_by_stratum']}
        estimate['maximum_site_leverage']=max(r['estimate']['maximum_site_leverage'] for r in measured)
        row.update(value=estimate['value'],standard_error=estimate['standard_error'],estimate=estimate)
    reference=np.asarray(out['results'][0]['estimate']['by_layout']); pairs=[]
    for row in out['results'][1:]:
        values=np.asarray(row['estimate']['by_layout'])
        relative=mean_interval(values/reference-1) if np.all(reference>0) else None
        pairs.append({'candidate':row['name'],'reference':out['results'][0]['name'],
                      **mean_interval(values-reference),'relative_change':relative,
                      'relative_to_b':relative['value'] if relative else None,'units':row['units']})
    out.update(id='independent-layout-mean',comparisons=pairs,position_layouts=[r['id'] for r in reports])
    return out

def selected_spatial_sites():
    first=records.load_document(run/'studies/source-2653/summary.json')
    return 2653 if first['spatial_check']['passed'] else 3041

def restored_center(identifier):
    report=records.load_document(run/'studies'/identifier/'summary.json')
    receipt=verify_saved_archive(report); destination=run/'references'/identifier
    records.recover_archive(receipt['path'],destination,expected_archive_sha256=receipt['sha256'])
    return destination,report

def verify_saved_archive(report):
    receipt=report['archive']; archive=Path(receipt['path']); second=Path(receipt['independent_copy'])
    if receipt.get('summary_content_hash')!=records.content_hash({key:value for key,value in report.items() if key not in ('archive','raw_files_present')}):
        raise ValueError('retained scientific summary differs from its recorded checksum')
    records.verify_archive(archive,expected_archive_sha256=receipt['sha256'],include_manifest=False)
    if not receipt.get('independent_copy_verified') or records.sha256_file(second)!=receipt['sha256']:
        raise ValueError('independent retained archive is unavailable or differs')
    return receipt

def compact_study(output,report,receipt):
    # Two verified archives retain N[i,r,e] before working outputs are cleared.
    report.update(raw_files_present=False,archive=receipt)
    records.write_json(output/'archive.json',receipt)
    records.write_json(output/'summary.json',report,overwrite=True)
    if (output/'results.sqlite').is_file():
        with records.Results(output/'results.sqlite') as database: database.save_state('summary',report)
    for path in output.iterdir():
        if path.name in ('summary.json','resolved.json','build.json','archive.json') or path.name.startswith('source-comparison-'): continue
        if path.is_dir(): shutil.rmtree(path)
        else: path.unlink()

if mode=='status':
    for name in ('queue_status.json','resources-current.json','published.json'):
        if (run/name).is_file(): print(name,records.canonical_json(records.load_document(run/name)))
    raise SystemExit(0)

limits={'workers':94,'total_mem_gb':64.,'per_task_mem_gb':2.,'system_mem_reserve_gb':16.,'minimum_free_disk_gb':20.}
if (run/'resources.json').is_file(): limits=records.load_document(run/'resources.json')
resource.setrlimit(resource.RLIMIT_AS,(int(limits['total_mem_gb']*1024**3),resource.RLIM_INFINITY))
if mode=='verify-source':
    snapshot=records.load_document(run/'source-ready.json')
    actual={str(path.relative_to(source)):records.sha256_file(path) for path in sorted(source.rglob('*')) if path.is_file()}
    if actual!=snapshot['files_sha256']: raise ValueError('frozen source or physical inputs changed; retain this run and inspect the difference')
    print('Verified frozen source, executable, libraries, lattice maps and Geant4 data.')
    raise SystemExit(0)
if mode in ('check','prepare'):
    allowed=sorted(os.sched_getaffinity(0)); cap=len(allowed)//2
    if cap<1: raise ValueError('at least two available CPUs are required')
    physical={}
    for cpu in allowed:
        topology=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key=((topology/'physical_package_id').read_text().strip(),(topology/'core_id').read_text().strip()) if topology.is_dir() else ('0',str(cpu))
        physical.setdefault(key,[]).append(cpu)
    ordered=[group[0] for group in physical.values()]+[cpu for group in physical.values() for cpu in group[1:]]
    limits.update(cpus=ordered[:cap],cpu_cap=cap,workers=min(cfg['execution']['workers'],max(1,cap-2)))
    reference=scan.load_study(repo/'studies/reference.yaml')
    parameters={name:{'mode':'search','bounds':bounds} for name,bounds in cfg['search_bounds'].items()}
    parameters['temperature']={'mode':'fixed','value':defaults['temperature_K']}
    space=physics.search_space(parameters,table=reference['parameter_table']['parameters'])
    assert len(space['names'])==17
    native=base_study('native-check',{'kind':'sobol','sites':16,'replicas':2,'events_per_task':1},'materials')
    native['constraints']={}
    native['candidates']=[{'name':'/'.join(row),'materials':list(row)} for row in itertools.product(('Si','Ge','GaAs'),('Nb','Ta','Ti'),('Cu','Au'))]
    native=scan.load_study(native)
    for point in scan.candidate_table(native): physics.prepare_values(native,point)
    if mode=='check':
        print(records.canonical_json({'search_dimensions':17,'native_material_cases':18,'stages':len(cfg['studies']),'cpu_cap':cap,'workers':limits['workers'],'memory':scan.memory_headroom(),'source_region_mm':8,'temperature_K':defaults['temperature_K']}))
        raise SystemExit(0)
    run.mkdir(parents=True,exist_ok=True)
    if not (run/'source-ready.json').is_file():
        if source.exists(): raise ValueError('unfinished source snapshot requires inspection')
        environment,build=scan.prepare_environment(reference['installation'])
        source.mkdir()
        for name in ('scan.py','physics.py','analysis.py','records.py','search.py','run_all.sh','parameters.yaml'):
            shutil.copy2(repo/name,source/name)
        for name in ('legacy','studies'):
            shutil.copytree(repo/name,source/name,ignore=shutil.ignore_patterns('.env','*.env','__pycache__','*.pyc'))
        for name in ('bin','lib','geant4-data'): (source/name).mkdir()
        shutil.copy2(reference['installation']['executable'],source/'bin/Main')
        for path,expected in build['libraries'].items():
            target=source/'lib'/Path(path).name
            if target.exists() and records.sha256_file(target)!=expected: raise ValueError('shared library names collide')
            if not target.exists(): shutil.copy2(path,target)
        shutil.copytree(reference['installation']['lattice_data'],source/'CrystalMaps')
        exported={key:value for key,value in environment.items() if key.startswith('G4')}
        data_paths={}
        for key,value in build['data_paths'].items():
            old=Path(value)
            if 'DATA' not in key or not old.is_dir(): continue
            target=source/'CrystalMaps' if key=='G4LATTICEDATA' else source/'geant4-data'/key
            if not target.exists(): shutil.copytree(old,target)
            exported[key]=str(target); data_paths[key]=str(target)
        exported['LD_LIBRARY_PATH']=str(source/'lib')
        (source/'environment.sh').write_text('\n'.join('export '+key+'='+shlex.quote(value) for key,value in exported.items())+'\n')
        installation={'executable':str(source/'bin/Main'),'library_dir':str(source/'lib'),'lattice_data':str(source/'CrystalMaps'),'macro_template':str(source/'legacy/macros/sensitivity_template_beamOn1e6.mac'),'environment_scripts':[str(source/'environment.sh')],'data_paths':data_paths}
        records.write_json(run/'installation.json',installation)
        _,frozen=scan.prepare_environment(installation)
        assert frozen['executable_sha256']==build['executable_sha256']
        assert set(frozen['libraries'].values())==set(build['libraries'].values())
        inventory={str(path.relative_to(source)):records.sha256_file(path) for path in sorted(source.rglob('*')) if path.is_file()}
        records.write_json(run/'source-ready.json',{'original_build':build,'frozen_build':frozen,'files_sha256':inventory,'source_sha256':{name:inventory[name] for name in ('scan.py','physics.py','analysis.py','records.py','search.py','run_all.sh','parameters.yaml')},'created':time.time()})
        for path in source.rglob('*'):
            if path.is_file(): path.chmod(0o555 if path==source/'bin/Main' else 0o444)
    records.write_json(run/'resources.json',limits)
    fixed=reference['fixed']; fixed['position_half_span_mm']=defaults['source_half_span_mm']
    (run/'designs').mkdir(exist_ok=True)
    previous_root=repo/cfg['previous_run']
    previous_design=records.load_document(previous_root/'designs/stratified_2361.json')
    records.write_json(run/'designs/stratified_2361.json',previous_design)
    for n in defaults['design_counts']:
        sampling={'kind':'stratified','sites':n,'position_seed':defaults['position_seed'],
                  'area_samples':defaults['area_samples'],'area_seed':defaults['area_seed'],
                  'allocation':dict(zip(physics.REGIONS,defaults['allocations'][str(n)])),
                  'stratum_weights':previous_design['stratum_weights']}
        prior=previous_design if n==2653 else records.load_document(run/'designs/stratified_2653.json') if n==3041 else None
        design=physics.build_design(fixed,sampling,previous=prior)
        design['design_hash']=records.content_hash({k:v for k,v in design.items() if k!='design_hash'})
        records.write_json(run/'designs'/f'stratified_{n}.json',design)
        if n not in (2653,3041): continue
        for family,seeds in [('source-independent',defaults['independent_position_seeds']),('unused-stream-confirmation',defaults['confirmation_position_seeds'])]:
            for k,seed in enumerate(seeds,1):
                sampling['position_seed']=seed
                independent=physics.build_design(fixed,sampling)
                independent['design_hash']=records.content_hash({k:v for k,v in independent.items() if k!='design_hash'})
                if independent['stratum_weights']!=previous_design['stratum_weights']: raise ValueError('independent layouts must share area weights')
                records.write_json(run/'designs'/f'{family}-{k:02d}_{n}.json',independent)
    independent_root=Path(os.environ.get('MATERIAL_ARCHIVE_ROOT',cfg['execution']['independent_archive_root']))/run.name
    independent_root.mkdir(parents=True,exist_ok=True)
    if independent_root.stat().st_dev==run.stat().st_dev: raise ValueError('archive copies require a separate persistent filesystem')
    if shutil.disk_usage(independent_root).free<limits['minimum_free_disk_gb']*1024**3: raise ValueError('independent archive disk reserve unavailable')
    records.write_json(run/'archive-location.json',{'path':str(independent_root),'device':independent_root.stat().st_dev,'working_device':run.stat().st_dev})
    queue=[{'id':'pilot','kind':'pilot','sampling':{'design':128,'replicas':2,'events_per_task':31250,'seed_bank':1100}}]
    groups=[g for g in cfg['studies'] if g['kind'] in ('convergence','independent_positions','event_count_convergence')]+[g for g in cfg['studies'] if g['kind'] not in ('convergence','independent_positions','event_count_convergence')]
    for group in groups:
        kind=group['kind']
        if kind=='convergence':
            queue.extend({**group,'id':f"source-{n}",'sites':n,'bank':group['sampling']['first_seed_bank']+i} for i,n in enumerate(group['sites']))
        elif kind=='independent_positions':
            queue.extend({**group,'id':f"source-independent-{i+1:02d}",'position_seed':seed,'bank':group['sampling']['first_seed_bank']+i} for i,seed in enumerate(defaults['independent_position_seeds']))
            queue.append({'id':'source-validation','kind':'source_validation'})
        elif kind=='event_count_convergence':
            queue.extend({**group,'id':f"events-{n}",'events_per_task':n,'bank':group['sampling']['first_seed_bank']+i} for i,n in enumerate(group['events_per_task']))
        elif kind=='search_comparison':
            queue.extend({**group,'id':f"{method}-{seed}",'kind':'search','method':method,'seed':seed,'bank':group['sampling']['first_seed_bank']+i} for i,seed in enumerate(group['seeds']) for method in group['methods'])
        elif kind=='confirmation':
            queue.extend({**group,'id':f"unused-stream-confirmation-{i+1:02d}",'position_seed':seed,'bank':group['sampling']['first_seed_bank']+i} for i,seed in enumerate(defaults['confirmation_position_seeds']))
            queue.append({'id':'finalist-validation','kind':'finalist_validation'})
        elif kind in ('explicit_tolerance','model_ranges'):
            queue.append({**group,'id':group['center_study'],'centers_only':True})
            moves=group['candidate_limit']*len(group['parameters'])*len(group['physical_fraction'])*2 if kind=='explicit_tolerance' else 52
            queue.extend({**group,'id':group['id']+f'-{i//4+1:02d}','move_start':i,'move_count':4} for i in range(0,moves,4))
        else: queue.append(group)
    records.write_json(run/'queue.json',queue)
    records.write_json(run/'queue_status.json',{'state':'prepared','stages':len(queue),'updated':time.time()},overwrite=True)
    records.write_json(run/'publication.json',{'branch':subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip(),'remote':subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip(),'files':['README.md','docs/results/full_scale_summary.json','docs/results/full_scale_finalists.png','docs/results/full_scale_transport_morris.png','docs/results/full_scale_density_morris.png','docs/results/full_scale_methods.png','docs/results/full_scale_projection.png']})
    print(records.canonical_json({'prepared_studies':len(queue),'source':str(source),'resources':limits}))
    raise SystemExit(0)

index=int(sys.argv[6]) if mode in ('stage','record','failed','pause') else None
if mode=='stage':
    step=queue[index]; identifier=step['id']; output=run/'studies'/identifier
    if (output/'summary.json').is_file():
        old=records.load_document(output/'summary.json')
        if 'archive' not in old and (output/'archive.json').is_file():
            old['archive']=records.load_document(output/'archive.json'); old['raw_files_present']=False
        if old.get('complete') and old.get('archive',{}).get('verified'):
            receipt=verify_saved_archive(old)
            compact_study(output,old,receipt)
            print('SKIP'); raise SystemExit(0)
    if step['kind'] in ('source_validation','finalist_validation'):
        if (run/(identifier+'.json')).is_file():
            validation=records.load_document(run/(identifier+'.json'))
        else:
            family='source-independent' if step['kind']=='source_validation' else 'unused-stream-confirmation'
            reports=[records.load_document(run/'studies'/f'{family}-{k:02d}'/'summary.json') for k in range(1,5)]
            aggregate=layout_summary(reports); checks={}
            for row in aggregate['results']:
                estimate=row['estimate']; precision=estimate['relative_standard_error']
                checks[row['name']]={'relative_standard_error':precision,'confidence_interval_95':estimate['confidence_interval_95'],
                                     'passed':precision is not None and precision<=.02}
            validation={'passed':all(row['passed'] for row in checks.values()),'candidates':checks,'independent_layouts':aggregate}
            if step['kind']=='source_validation':
                from scipy.stats import t
                selected=records.load_document(run/'studies'/f'source-{selected_spatial_sites()}'/'summary.json')
                nested=selected['spatial_check']; original={row['name']:row for row in selected['results']}; agreement={}
                margin=next(g['check']['relative_limit'] for g in cfg['studies'] if g['kind']=='independent_positions')
                for row in aggregate['results']:
                    old=original[row['name']]; delta=row['value']-old['value']
                    se=math.hypot(row['standard_error'],old['standard_error'])
                    radius=float(t.ppf(.975,len(reports)-1)*se); interval=[delta-radius,delta+radius]
                    limit=margin*abs(row['value'])
                    agreement[row['name']]={'difference':delta,'standard_error':se,'confidence_interval_95':interval,
                        'passed':interval[0]>-limit and interval[1]<limit,
                        'regions':{h:{'independent_mean':value,'retained_mean':old['estimate']['means_by_stratum'][h],
                                       'difference':value-old['estimate']['means_by_stratum'][h]} for h,value in row['estimate']['means_by_stratum'].items()},
                        'uncertainty_method':'Independent layout variance plus retained hierarchical variance; conservative t critical value with three degrees of freedom. Area weights held fixed.'}
                validation.update(nested=nested,retained_layout_agreement=agreement,
                                  passed=validation['passed'] and nested['passed'] and all(a['passed'] for a in agreement.values()))
            records.write_json(run/(identifier+'.json'),validation)
        if not validation['passed']:
            records.write_json(run/'queue_status.json',{'state':'needs_more_positions','stage':identifier,
                               'reason':'Independent position precision or nested spatial interval failed; inspect region estimates before further transport.',
                               'updated':time.time()},overwrite=True)
            raise ValueError('source-position checks did not pass; retained results require spatial refinement')
        records.write_json(run/(identifier+'-skipped.json'),{'reason':'analysis-only spatial check completed','passed':True},overwrite=True)
        print('SKIP'); raise SystemExit(0)
    if identifier=='source-3041' and selected_spatial_sites()==2653:
        records.write_json(run/(identifier+'-skipped.json'),{'reason':'nested spatial intervals passed at 2653 sites'},overwrite=True)
        print('SKIP'); raise SystemExit(0)
    sampling=dict(step['sampling'])
    if sampling.get('design')==2653 and step['kind']!='convergence': sampling['design']=selected_spatial_sites()
    if 'sites' in step and step['kind']=='convergence': sampling['design']=step['sites']
    if 'position_seed' in step:
        sampling['design_file']=f"{identifier}_{sampling['design']}.json"
    if 'events_per_task' in step: sampling['events_per_task']=step['events_per_task']
    if 'bank' in step: sampling['seed_bank']=step['bank']
    raw=base_study(identifier,sampling)
    if 'position_seed' in step: raw['sampling']['position_seed']=step['position_seed']
    kind=step['kind']
    if kind=='temperature_comparison':
        raw['fixed']['temperature_K']=step['temperature']; raw['parameters']['temperature']['value']=step['temperature']
    elif kind in ('convergence','independent_positions','event_count_convergence'):
        raw['candidates']=[candidate(name) for name in step['candidates']]
    elif kind=='explicit_parameters':
        raw['candidates']=[{'name':f"C44-{v:g}GPa",'values':{step['parameter']:v}} for v in step['values_GPa']]
    elif kind=='morris':
        raw['kind']='sensitivity'; raw['analysis'].update(objective=step['objective'],diagnostics=False)
        raw['morris']={key:step[key] for key in ('parameters','trajectories','levels','seed')}
    elif kind=='materials':
        raw['kind']='materials'; raw['constraints']={}
        raw['candidates']=[{'name':'/'.join(row),'materials':list(row)} for row in itertools.product(step['substrates'],step['top_films'],step['bottom_films'])]
    elif kind=='search':
        raw['kind']='search'; raw.pop('candidates')
        raw['parameters'].update({name:{'mode':'search','bounds':bounds} for name,bounds in cfg['search_bounds'].items()})
        raw['analysis'].update(diagnostics=False,bootstrap_draws=256,store_search_blocks=False)
        raw['search']={'method':step['method'],'seed':step['seed'],'evaluations':step['measured_evaluations'],'measured_evaluations':True,'max_attempts':step['maximum_attempts'],'options':{'popsize':step['cma_population'],'n_init':step['gp_initial_points'],'n_restarts':3,'n_candidates':4096},'parallel_candidates':1}
    elif kind=='confirmation':
        raw['candidates']=[candidate(name) for name in step['anchors']]+[{key:value for key,value in point.items() if key in ('name','values')} for point in selected_finalists()]
    elif kind in ('explicit_tolerance','model_ranges'):
        finalists=selected_finalists()
        confirmation=records.load_document(run/'finalist-validation.json')['independent_layouts']
        measured={row['name']:row for row in confirmation['results']}
        best=min(finalists,key=lambda p:measured[p['name']]['value'])
        if kind=='explicit_tolerance':
            chosen=sorted(finalists,key=lambda p:measured[p['name']]['value'])[:step['candidate_limit']]
            centers=[{'name':point['name'],'values':point['values']} for point in chosen]; cases=[]
            for point in chosen:
                for name,fraction,sign in itertools.product(step['parameters'],step['physical_fraction'],(-1,1)):
                    value=point['values'][name]*(1+sign*fraction)
                    cases.append({'name':f"{point['name']}:{name}:{sign*fraction:+g}",'values':{**point['values'],name:value}})
        else:
            anchors=[candidate('reference'),candidate('sic'),{'name':'confirmed-best','values':best['values']}]; centers=copy.deepcopy(anchors); cases=[]
            sic=candidate('sic')
            for name,values in [('sub_scat',[cfg['sic']['values']['sub_scat']*f for f in cfg['sic']['isotope_factors']]),('sub_decay',cfg['sic']['decay_values_s4']),('sub_decayTT',cfg['sic']['tt_values'])]:
                cases.extend({**copy.deepcopy(sic),'name':f"{sic['name']}:{name}:{v:.6g}",'values':{**sic['values'],name:v}} for v in values)
            for scat,decay,tt in itertools.product([cfg['sic']['values']['sub_scat']*min(cfg['sic']['isotope_factors']),cfg['sic']['values']['sub_scat']*max(cfg['sic']['isotope_factors'])],[min(cfg['sic']['decay_values_s4']),max(cfg['sic']['decay_values_s4'])],[min(cfg['sic']['tt_values']),max(cfg['sic']['tt_values'])]):
                cases.append({**copy.deepcopy(sic),'name':f"{sic['name']}:joint:{scat:g}:{decay:g}:{tt:g}",'values':{**sic['values'],'sub_scat':scat,'sub_decay':decay,'sub_decayTT':tt}})
            for anchor in anchors:
                for name,factor in itertools.product(('topfilm_ph_lifetime','bot_ph_lifetime'),step['film_lifetime_factors']):
                    if factor==1: continue
                    values={**raw.get('candidates',[{}])[0].get('values',{}),**anchor.get('values',{})}
                    values.setdefault(name,scan.load_study(raw)['space']['baseline'][name])
                    cases.append({**copy.deepcopy(anchor),'name':f"{anchor['name']}:{name}:x{factor:g}",'values':{**values,name:values[name]*factor}})
                prepared=physics.prepare_values(scan.load_study(raw),anchor)
                for name,factor in itertools.product(('setTopAbs','setTopFilmAbs','setBotAbs'),step['interface_absorption_factors']):
                    if factor==1: continue
                    value=min(1.,max(0.,prepared['derived'][name]*factor))
                    cases.append({**copy.deepcopy(anchor),'name':f"{anchor['name']}:{name}:x{factor:g}",'macro':{'/main/detector_param/'+name:value},'tolerance':{'unclipped':prepared['derived'][name]*factor,'clipped':value!=prepared['derived'][name]*factor}})
        cases=centers if step.get('centers_only') else cases[step['move_start']:step['move_start']+step['move_count']]
        raw['analysis']['comparisons']=False
        kept=[]; rejected=[]; study=scan.load_study(raw); names=set()
        for point in cases:
            if point['name'] in names: continue
            names.add(point['name'])
            try: physics.prepare_values(study,point)
            except ValueError as exc:
                rejected.append({'name':point['name'],'reason':str(exc),'status':'physically_infeasible','value':None}); continue
            kept.append(point)
        raw['candidates']=kept; raw['provenance']={'center_study':step['center_study'],'infeasible_moves':rejected,'centers':[point['name'] for point in centers],'moves':[{'name':point['name'],**point.get('tolerance',{})} for point in kept if point['name'] not in {p['name'] for p in centers}],'selection':'fresh complete confirmation responses'}
    if kind in ('explicit_tolerance','model_ranges') and not kept:
        records.write_json(run/(identifier+'-skipped.json'),{'reason':'every declared move is physically infeasible','physical_moves':raw['provenance']},overwrite=True)
        print('SKIP'); raise SystemExit(0)
    path=run/'definitions'/f'{identifier}.yaml'; path.parent.mkdir(exist_ok=True)
    text=yaml.safe_dump(raw,sort_keys=False)
    if path.exists() and path.read_text()!=text: raise ValueError('prepared study changed; use a new run directory')
    path.write_text(text)
    study=scan.load_study(path)
    for point in scan.candidate_table(study): physics.prepare_values(study,point)
    records.write_json(run/'queue_status.json',{'state':'starting','stage':identifier,'index':index,'updated':time.time()},overwrite=True)
    print(path)
elif mode=='wait':
    while True:
        memory=scan.memory_headroom(); disk=os.statvfs(run)
        if disk.f_bavail*disk.f_frsize>=limits['minimum_free_disk_gb']*1024**3 and memory['available_bytes']>=limits['system_mem_reserve_gb']*1024**3: break
        time.sleep(30)
elif mode in ('pause','failed'):
    reason=records.load_document(run/'guard.json') if (run/'guard.json').is_file() else {'reason':'simulation did not finish; inspect its log and retained pending records'}
    records.write_json(run/'queue_status.json',{'state':'paused' if mode=='pause' else 'interrupted','index':index,'stage':queue[index]['id'],'reason':reason,'updated':time.time()},overwrite=True)
    print(records.canonical_json(reason))
elif mode=='record':
    step=queue[index]; output=run/'studies'/step['id']; report=records.load_document(output/'summary.json')
    if report.get('archive'):
        receipt=verify_saved_archive(report); compact_study(output,report,receipt)
        print(records.canonical_json({'stage':step['id'],'already_archived':True})); raise SystemExit(0)
    if not report['complete']: raise ValueError('incomplete studies cannot become confirmed results')
    if step['kind']=='convergence':
        # Shared outputs define the nested contrast; no deterministic replay assumed.
        n=2361 if step['sites']==2653 else 2653
        coarse=records.load_document(run/'designs'/f'stratified_{n}.json')
        coarse_design={**report['design'],'position_strata':coarse['stratum'],'sites_mm':coarse['sites_mm'],'design_hash':coarse['design_hash']}
        checks={}
        for row in report['results']:
            blocks=records.load_result(output,candidate_id=row['candidate_id'])['results'][0]['blocks']
            checks[row['name']]=analysis.nested_spatial_difference([b for b in blocks if b['position']<n],blocks,coarse_design,report['design'],
                                      bootstrap_draws=defaults['bootstrap_draws'],relative_limit=step['check']['relative_limit'])
            checks[row['name']]['maximum_site_leverage']=row['estimate']['maximum_site_leverage']
            checks[row['name']]['passed'] &= row['estimate']['maximum_site_leverage']<=step['check']['leverage_limit']
        report['spatial_check']={'passed':all(c['passed'] for c in checks.values()),'candidates':checks,
                                'interpretation':'Nested-layout contrasts only; independent position layouts must also pass.'}
        records.write_json(output/'summary.json',report,overwrite=True)
    elif step['kind']=='event_count_convergence':
        # More primaries reduce transport noise at fixed sites, not integration error.
        counts=next(group['events_per_task'] for group in cfg['studies'] if group['kind']=='event_count_convergence')
        ordinal=counts.index(step['events_per_task']); tests={}
        previous=records.load_document(run/'studies'/f'events-{counts[ordinal-1]}'/'summary.json') if ordinal else None
        old={row['name']:row for row in previous['results']} if previous else {}
        for row in report['results']:
            precision=analysis.transport_precision(row['estimate']['by_replica'])
            rse=precision['relative_standard_error']
            repeated=analysis.compare_repeated(row['estimate']['by_replica'],old[row['name']]['estimate']['by_replica'],relative_margin=step['check']['relative_limit']) if previous else None
            tests[row['name']]={'transport':precision,'area_standard_error':row['standard_error'],
                                'conditional_on_sites':repeated,
                                'passed':previous is not None and repeated['equivalent_within_margin'] and rse is not None and rse<=step['check']['relative_standard_error_limit']}
        report['event_count_check']={'passed':all(test['passed'] for test in tests.values()),'candidates':tests,
                                    'interpretation':'Independent transport banks at fixed sites. Position uncertainty is reported separately.'}
        records.write_json(output/'summary.json',report,overwrite=True)
    elif step['kind'] in ('explicit_tolerance','model_ranges') and not step.get('centers_only'):
        # dJ uses matched (i,r); each center is restored once and reused across batches.
        definition=records.load_document(run/'definitions'/f"{step['id']}.yaml")
        center_dir,center_report=restored_center(step['center_study'])
        if center_report['design']!=report['design'] or center_report['streams']!=report['streams']:
            raise ValueError('center and moves must have identical sites, primaries and random streams')
        centers={row['name']:row for row in center_report['results']}; pairs=[]
        for name,center in centers.items():
            block=records.load_result(center_dir,candidate_id=center['candidate_id'])['results'][0]['blocks']
            for row in report['results']:
                if not row['name'].startswith(name+':'): continue
                moved=records.load_result(output,candidate_id=row['candidate_id'])['results'][0]['blocks']
                pairs.append({'candidate':row['name'],'reference':name,'center_study':step['center_study'],
                              **analysis.paired_difference(moved,block,report['design'],bootstrap_draws=defaults['bootstrap_draws'],objective=report['objective'])})
        report['within_center_comparisons']=pairs; report['physical_moves']=definition['provenance']
        report['center_archive']={key:center_report['archive'][key] for key in ('path','sha256','independent_copy')}
        records.write_json(output/'summary.json',report,overwrite=True)
    elif step['kind']=='pilot':
        with records.Results(output/'results.sqlite',create=False) as database:
            sizes=[sum(path.stat().st_size for path in Path(row['result']['paths']['directory']).rglob('*') if path.is_file()) for row in database.tasks()]
        report['pilot_storage']={'task_bytes_median':float(np.median(sizes)),'task_bytes_p95':float(np.percentile(sizes,95)),'task_bytes_total':sum(sizes),'meaning':'one reference and 128-site stratified sampling; not a bound for all searched physical values'}
        records.write_json(output/'summary.json',report,overwrite=True)
    archives=run/'archives'; archives.mkdir(exist_ok=True); archive=archives/(step['id']+'.tar.gz')
    if archive.exists():
        receipt=records.verify_archive(archive,include_manifest=False)
    else:
        required=sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
        if shutil.disk_usage(archives).free<required+limits['minimum_free_disk_gb']*1024**3: raise ValueError('insufficient disk reserve to create a verified archive; retain raw files and free disk space before resuming')
        receipt=records.create_archive(output,None,archive,include_manifest=False)
    independent=Path(records.load_document(run/'archive-location.json')['path']); independent.mkdir(parents=True,exist_ok=True)
    target=independent/archive.name
    if not target.exists():
        if shutil.disk_usage(independent).free<archive.stat().st_size+limits['minimum_free_disk_gb']*1024**3: raise ValueError('insufficient independent-copy disk reserve; retain raw files and resume after freeing space')
        temporary=target.with_suffix('.partial')
        shutil.copy2(archive,temporary)
        with temporary.open('rb') as handle: os.fsync(handle.fileno())
        records.publish(temporary,target)
    if records.sha256_file(target)!=receipt['sha256']: raise ValueError('independent archive copy differs')
    assert archive.stat().st_dev!=target.stat().st_dev
    receipt['independent_copy']=str(target); receipt['independent_copy_verified']=True; receipt['persistent_copy_verified']=True
    receipt['summary_content_hash']=records.content_hash({key:value for key,value in report.items() if key not in ('archive','raw_files_present')})
    receipt['compressed_bytes']=archive.stat().st_size
    compact_study(output,report,receipt)
    records.write_json(run/'queue_status.json',{'state':'recorded','index':index,'stage':step['id'],'updated':time.time()},overwrite=True)
    print(records.canonical_json({'stage':step['id'],'compressed_archive_bytes':receipt['compressed_bytes'],'uncompressed_member_bytes':receipt['bytes'],'members':receipt['members']}))
elif mode=='finish':
    reports={}
    for step in queue:
        path=run/'studies'/step['id']/'summary.json'
        if path.exists(): reports[step['id']]=records.load_document(path)
        elif not (run/(step['id']+'-skipped.json')).is_file(): raise ValueError('a declared study has no final record')
    if any(not report['complete'] for report in reports.values()): raise ValueError('all declared measured studies must finish')
    for report in reports.values(): verify_saved_archive(report)
    confirmation=records.load_document(run/'finalist-validation.json')['independent_layouts']
    finalists=selected_finalists(); lookup={r['name']:r for r in confirmation['results']}
    methods={method:[lookup[p['name']]['value'] for p in finalists if p['name'].startswith(method+'-')] for method in ('bo_gp','cmaes')}
    comparison=analysis.compare_repeated(methods['bo_gp'],methods['cmaes'])
    comparison['interpretation']='Exploratory variation across three optimizer seeds, conditional on four shared confirmation layouts; shared measurement uncertainty is not propagated by this Welch interval.'
    layout_means={method:np.mean([lookup[p['name']]['estimate']['by_layout'] for p in finalists if p['name'].startswith(method+'-')],axis=0) for method in methods}
    comparison['position_layout_difference']=analysis.compare_repeated(layout_means['bo_gp'],layout_means['cmaes'],paired=True)
    comparison['position_layout_difference']['interpretation']='Paired across four independent layouts for these six fixed finalists; does not estimate variability from new optimization runs.'
    comparison['matched_seed_difference']=analysis.compare_repeated(methods['bo_gp'],methods['cmaes'],paired=True)
    reference=lookup['reference']
    result={'date':time.strftime('%Y-%m-%d'),'run_id':cfg['id'],'state':'complete','temperature_K':defaults['temperature_K'],'nb_gap_min_eV':defaults['nb_gap_min_eV'],'injection_area_mm2':64,'resources':limits,'source':records.load_document(run/'source-ready.json'),'studies':{},'method_comparison':comparison,'confirmed_finalists':[],'limitations':['SiC rates/dynamical fields are declared assumptions, not complete native measurements.','Interfaces are calibrated; factor studies test stated model uncertainty.','Junction-QP yield is not a calibrated logical-error probability.','Density sensitivity uses uniform Sobol sites and the legacy pooled nonlinear response; pooled standard error is not estimated.']}
    for identifier,report in reports.items():
        result['studies'][identifier]={key:report[key] for key in ('kind','objective','sites','replicas','events_per_task','events_total_per_candidate','complete','search_counts','spatial_check','event_count_check','center_archive','pilot_storage','sensitivity','comparisons','within_center_comparisons','physical_moves','streams','geometry','design') if key in report}
        result['studies'][identifier].update(candidates=len(report['results']),archive=report['archive'],results=[{key:row[key] for key in ('name','candidate_id','status','value','standard_error','units','parameters','estimate','historical','integrated_density','integrated_density_by_replica','inputs','trajectory','design_index','tolerance','reasons') if key in row} for row in report['results']])
    paired={row['candidate']:row for row in confirmation['comparisons']}
    for point in finalists:
        row=lookup[point['name']]
        result['confirmed_finalists'].append({'name':row['name'],'search_candidate_id':point['selected_candidate_id'],'confirmation_candidate_ids':[next(r['candidate_id'] for r in reports[f'unused-stream-confirmation-{k:02d}']['results'] if r['name']==row['name']) for k in range(1,5)],'value':row['value'],'standard_error':row['standard_error'],'relative_to_reference':paired[row['name']]['relative_to_b'],'paired_difference_from_reference':paired[row['name']],'parameters':row['parameters']})
    substrates=records.load_document(source/'legacy/catalog/substrates_raw.json')['accepted']
    candidates=[]; native=records.load_document(source/'legacy/stage4_material_pool.yaml')['g4cmp_substrates']
    for row in substrates:
        features={name:row.get(field) for name,field in cfg['projection']['substrate_map'].items()}
        phonons=native.get(row.get('formula'),{})
        if phonons.get('verified'): features.update({name:phonons.get(field) for name,field in cfg['projection']['native_phonon_map'].items()})
        candidates.append({'id':row.get('material_id',row.get('mp_id',row.get('id'))),'formula':row.get('formula'),'features':features,'property_source':'Materials Project density/elasticity calculations','phonon_source':phonons.get('source') if phonons.get('verified') else None,'missing_native_lattice':not phonons.get('verified',False)})
    definition=scan.load_study(run/'definitions/unused-stream-confirmation-01.yaml'); points={point['name']:point for point in scan.candidate_table(definition)}
    catalog=records.load_document(source/'legacy/material_catalog.yaml'); projections=[]
    for point in finalists:
        row=lookup[point['name']]; prepared=physics.prepare_values(definition,points[row['name']])
        target={**row['parameters'],'substrate_density_kg_m3':prepared['derived']['substrate_density_kg_m3']}
        projected={'name':row['name'],'confirmation_candidate_id':row['candidate_id'],'target':target,'projection':analysis.project_materials(target,candidates,cfg['projection']['feature_scales'],top=cfg['projection']['top'])}
        for layer,table,field_map in [('top_film','top_films',cfg['projection']['top_film_map']),('bottom_film','bottom_films',cfg['projection']['bottom_film_map'])]:
            films=[{'id':name,'features':{key:film.get(field) for key,field in field_map.items()},'property_source':film.get('provenance'),'enabled':film.get('enabled',False)} for name,film in catalog[table].items()]
            projected[layer]=analysis.project_materials(target,films,{key:'log' for key in field_map},top=cfg['projection']['top'])
        projections.append(projected)
    result['projections']=projections
    result['projection']={**projections[0],'interpretation':'Substrate density and elasticity are DFT calculations; verified native phonon fields and curated film fields are joined separately. Coverage is ranked before distance. Every confirmed target is retained in projections; the figure shows the first target.'}
    event_report=reports['events-'+str(next(group['events_per_task'][-1] for group in cfg['studies'] if group['kind']=='event_count_convergence'))]
    result['scientific_checks']={'source_spatial':records.load_document(run/'source-validation.json')['passed'],'finalist_spatial':records.load_document(run/'finalist-validation.json')['passed'],'event_count':event_report['event_count_check']['passed']}
    result['next_measurements']=['Obtain low-temperature SiC acoustic rates and native lattice fields.','Validate interface absorption against independent measurements.','Calibrate local QP density to the qubit observable.']
    if not all(result['scientific_checks'].values()): result['next_measurements'].insert(0,'Refine sites or primary counts for each failed convergence test before interpreting the corresponding ranking.')
    result['skipped_studies']={step['id']:records.load_document(run/(step['id']+'-skipped.json')) for step in queue if (run/(step['id']+'-skipped.json')).is_file()}
    result['position_validation']={name:records.load_document(run/(name+'.json')) for name in ('source-validation','finalist-validation')}
    result['previous_studies']={}
    for path in sorted((repo/cfg['previous_run']/'studies').glob('*/summary.json')):
        previous=records.load_document(path)
        if previous.get('archive'): verify_saved_archive(previous)
        result['previous_studies'][path.parent.name]=previous
    # Verify publication destination before changing tracked scientific reports.
    publication=records.load_document(run/'publication.json')
    if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
        raise ValueError('publication branch or remote changed; retained results were not published')
    public=repo/'docs/results'; public.mkdir(parents=True,exist_ok=True)
    records.write_json(public/'full_scale_summary.json',result,overwrite=True)
    analysis.plot_result(confirmation,public/'full_scale_finalists.png','summary')
    analysis.plot_result(reports['material-transport-morris'],public/'full_scale_transport_morris.png','sensitivity')
    analysis.plot_result(reports['density-morris'],public/'full_scale_density_morris.png','sensitivity')
    analysis.plot_result({'results':[{'name':p['name'],'value':lookup[p['name']]['value'],'standard_error':lookup[p['name']]['standard_error'],'units':reference['units']} for p in finalists]},public/'full_scale_methods.png','summary')
    analysis.plot_result(result['projection'],public/'full_scale_projection.png','projection')
    block=['<!-- full-scale-start -->','## Fresh full-scale measurements','',f"Finished {result['date']} at 20 mK with the Nb gap restricted to at least 1.5384 meV. All declared simulations and archive checks finished. Convergence checks: `{result['scientific_checks']}`.",'',f"GP and CMA-ES each obtained 120 successful measurements for each of three optimizer seeds. Finalists were confirmed on four independent layouts of {confirmation['sites']} spatial sites, each with {confirmation['replicas']} realizations on unused random streams. Algorithm starts differ.",'','| Confirmed target | Relative QP yield change | Paired standard error (percentage points) |','|---|---:|---:|']
    block += [f"| {row['name']} | {100*row['relative_to_reference']:+.2f}% | {100*row['paired_difference_from_reference']['relative_change']['standard_error']:.3g} |" for row in result['confirmed_finalists']]
    block += ['','[Numerical records and archive checksums](docs/results/full_scale_summary.json).','', '![Confirmed targets](docs/results/full_scale_finalists.png)', '', '![Transport sensitivity](docs/results/full_scale_transport_morris.png)', '', '![Density sensitivity](docs/results/full_scale_density_morris.png)', '', 'A property target is not a fabricated material. SiC phonon rates remain assumed, and the interface model remains calibrated. Spatial and event-count checks are reported separately; unresolved optimizer differences remain unresolved.','<!-- full-scale-end -->']
    readme=repo/'README.md'; text=readme.read_text(); start=text.index('<!-- full-scale-start -->'); end=text.index('<!-- full-scale-end -->')+len('<!-- full-scale-end -->')
    readme.write_text(text[:start]+'\n'.join(block)+text[end:])
    if (run/'references').exists(): shutil.rmtree(run/'references')
    records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'updated':time.time()},overwrite=True)
    print(records.canonical_json({'state':'complete','studies':len(reports),'method_comparison':comparison}))
else:
    raise ValueError('choose check, prepare, run, status, stop, stage, record, finish or publish')
PY
