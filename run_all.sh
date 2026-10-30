#!/usr/bin/env bash
set -Eeuo pipefail
# Each stage fixes x, sites i, realizations r and N_p primaries per (i,r).

mode=status
if (( $# > 0 )); then mode=$1; fi
if printenv MATERIAL_REPOSITORY >/dev/null; then repository=$(printenv MATERIAL_REPOSITORY)
else repository=$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd -P); fi
material_run="$repository/material_scan_data/optimization_20261016_fixed"
if (( $# > 1 )); then material_run=$2; fi
material_run=$(realpath -m -- "$material_run")
material_python=/home/htseng/.conda/envs/G4CMP/bin/python
if printenv MATERIAL_PYTHON >/dev/null; then material_python=$(printenv MATERIAL_PYTHON); fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-full-scale-mpl
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}${no_proxy:+$no_proxy,}127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"
source_dir="$material_run/source"

if [[ "$mode" == run ]]; then
    [[ -f "$material_run/source-ready.json" ]] || bash "$BASH_SOURCE" prepare "$material_run"
    export MATERIAL_REPOSITORY="$repository" MATERIAL_PYTHON="$material_python"
    if [[ -f "$material_run/dependency.json" ]]; then
        parent_run=$("$material_python" - "$material_run" <<'PY'
import json,sys,time
from pathlib import Path
root=Path(sys.argv[1]); parent=json.loads((root/'dependency.json').read_text())['run']
(root/'queue_status.json').write_text(json.dumps({'state':'waiting_for_reference_queue','reference_run':parent,'updated':time.time()})+'\n')
print(parent)
PY
        )
        while :; do
            exec 8>>"$parent_run/queue.lock"
            flock 8
            if bash "$source_dir/run_all.sh" check-reference "$material_run"; then break
            else code=$?; [[ "$code" == 75 ]] || exit "$code"; fi
            flock -u 8
            exec 8>&-
            sleep 30
        done
    fi
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
    for pass in 1 2; do
    indices=$("$material_python" - "$material_run" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
for index,step in enumerate(json.loads((root/'queue.json').read_text())):
    path=root/'studies'/step['id']/'summary.json'
    report=json.loads(path.read_text()) if path.is_file() else {}
    if not (report.get('complete') or report.get('terminated_search')) or not report.get('archive',{}).get('verified'):
        print(index)
PY
    )
    for index in $indices; do
        if study=$(bash "$BASH_SOURCE" stage "$material_run" "$index"); then :
        else
            code=$?
            if (( code == 75 )) && bash "$BASH_SOURCE" defer-ai "$material_run" "$index"; then continue; fi
            exit "$code"
        fi
        [[ "$study" != SKIP ]] || continue
        id=$(basename "$study" .yaml)
        output="$material_run/studies/$id"
        while :; do
            rm -f "$material_run/guard.json"
            scan_started_ns=$("$material_python" -c 'import time;print(time.time_ns())')
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
    service=root/'ollama-service.json'
    if service.is_file():
        record=json.loads(service.read_text()); service_pid=record.get('pid')
        try:
            if int(Path(f'/proc/{service_pid}/stat').read_text().split(') ',1)[1].split()[19])==int(record.get('starttime',-1)): descendants.add(service_pid)
        except (FileNotFoundError,ValueError,IndexError): pass
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
                if ! bash "$BASH_SOURCE" verify-ai "$material_run" "$index"; then
                    if bash "$BASH_SOURCE" defer-ai "$material_run" "$index"; then continue 2; fi
                    exit 75
                fi
                if "$material_python" - "$material_run" "$index" "$output" "$scan_started_ns" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); step=json.loads((root/'queue.json').read_text())[int(sys.argv[2])]
path=Path(sys.argv[3])/'summary.json'
report=json.loads(path.read_text()) if path.is_file() else {}
limited=report.get('incomplete_reason')=='elapsed limit or maximum attempts reached before the measured target'
fresh=path.is_file() and path.stat().st_mtime_ns>=int(sys.argv[4])
raise SystemExit(0 if fresh and step['kind'] in ('optimization_search','native_search') and not report.get('complete') and report.get('search_counts') and limited else 1)
PY
                then break; fi
                bash "$BASH_SOURCE" failed "$material_run" "$index"
                exit "$code"
            fi
            break
        done
        bash "$BASH_SOURCE" record "$material_run" "$index"
    done
    [[ -f "$material_run/ai-paused.json" ]] || break
    done
    bash "$BASH_SOURCE" finish "$material_run"
    state=$("$material_python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["state"])' "$material_run/queue_status.json")
    [[ "$state" != awaiting_review ]] || exit 0
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
subprocess.run(['bash',str(root/'source/run_all.sh'),'verify-ai',str(root)],check=True)
publication=json.loads((root/'publication.json').read_text())
assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()==publication['branch']
assert subprocess.check_output(['git','remote','get-url','origin'],text=True).strip()==publication['remote']
files=publication['files']
if any(Path(name).is_absolute() or '..' in Path(name).parts or not
       (name=='README.md' or name.startswith('docs/results/') or name.startswith('figures/') and name.endswith('.png')) for name in files):
    raise ValueError('publication paths must be declared public scientific outputs')
if any(Path(name).stat().st_size >= 100*1024**2 for name in files):
    raise ValueError('public report exceeds 100 MiB; keep per-site blocks in verified archives')
subprocess.run(['git','add','--force','--',*files],check=True)
if subprocess.run(['git','diff','--cached','--quiet','--',*files]).returncode:
    subprocess.run(['git','commit','--only','-m',publication.get('commit_message','Report fresh full-scale material studies'),'--',*files],check=True)
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
default_config='studies/optimization/queue.yaml' if run.name.startswith('optimization_') else 'studies/full_scale/queue.yaml'
config_path=Path(os.environ.get('MATERIAL_QUEUE_CONFIG',default_config))
if (run/'queue-config.json').is_file(): config_path=Path(records.load_document(run/'queue-config.json')['relative_path'])
if not config_path.is_absolute(): config_path=(source if (run/'source-ready.json').is_file() else repo)/config_path
cfg=records.load_document(config_path)
optimization=cfg.get('schema')=='material-scan-optimization-1'
native_search=cfg.get('schema')=='material-scan-native-optimization-1'
spatial_verify=cfg.get('schema')=='material-scan-spatial-verification-1'
focused=native_search or spatial_verify or cfg.get('schema')=='material-scan-focused-1'
refinement={}
if optimization and cfg.get('refinement_file'):
    refinement=records.load_document((source if (run/'source-ready.json').is_file() else repo)/cfg['refinement_file'])
    cfg['original_methods']=list(cfg['methods'])
    extra=refinement['additional_methods']
    if set(extra)&set(cfg['methods']) or len(set(extra))!=len(extra): raise ValueError('additional methods must have distinct new names')
    cfg['methods']+=extra
    cfg['method_settings'].update(refinement['method_settings'])
defaults=cfg['defaults']
max_sites=int(defaults.get('max_sites',1024))
initial_checks=bool(cfg['execution'].get('stop_after_initial_checks',False))
queue=json.loads((run/'queue.json').read_text()) if (run/'queue.json').is_file() else []

def base_study(identifier,sampling,kind='confirmation'):
    raw=records.load_document((source if source.is_dir() else repo)/'studies/reference.yaml')
    raw.update(id=identifier,kind=kind,description=identifier+' simulation')
    raw['fixed'].update(temperature_K=defaults['temperature_K'],position_half_span_mm=defaults['source_half_span_mm'],gun_energy_eV=defaults['gun_energy_eV'],setTopGap=defaults['junction_gap_eV'],min_e_phonons_eV=defaults['cutoff_eV'])
    raw['parameters']={'temperature':{'mode':'fixed','value':defaults['temperature_K']}}
    raw['constraints']={'topfilm_gap_min_eV':defaults['nb_gap_min_eV']}
    raw['analysis']={'objective':defaults['objective'],'bootstrap_draws':defaults['bootstrap_draws'],'tail_bootstrap_draws':1000,'store_blocks':False}
    raw['execution']={key:limits[key] for key in ('workers','total_mem_gb','per_task_mem_gb','system_mem_reserve_gb')}
    raw['execution'].update(timeout_s=cfg['execution']['timeout_s'],stall_timeout_s=cfg['execution']['stall_timeout_s'])
    raw['sampling']={'kind':'recorded-stratified','sites':sampling.get('design',sampling.get('sites',512)),'replicas':sampling['replicas'],'events_per_task':sampling['events_per_task'],'seed_base':defaults['seed_base'],'seed_bank':sampling.get('seed_bank',sampling.get('first_seed_bank',201)),'position_seed':defaults['position_seed']}
    if raw['sampling']['sites']>max_sites: raise ValueError(f'study exceeds the {max_sites}-site limit')
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

def mean_interval(values,replicates='layouts'):
    # y_k are independent position-layout means, including transport noise.
    from scipy.stats import t
    y=np.asarray(values,dtype=float)
    if len(y)<2 or not np.isfinite(y).all(): raise ValueError('at least two complete independent layouts required')
    mean=float(y.mean()); se=float(y.std(ddof=1)/math.sqrt(len(y)))
    radius=float(t.ppf(.975,len(y)-1)*se)
    return {'value':mean,'standard_error':se,'confidence_interval_95':[mean-radius,mean+radius],
            'relative_standard_error':se/abs(mean) if mean else None,('by_layout' if replicates=='layouts' else 'by_seed'):y.tolist(),
            ('n_layouts' if replicates=='layouts' else 'n_seeds'):len(y),'uncertainty_method':'Student-t interval across independent '+replicates}

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
        if all(r.get('transport_standard_error') is not None for r in measured):
            row['transport_standard_error']=math.sqrt(sum(r['transport_standard_error']**2 for r in measured))/len(measured)
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
    sizes=next(group['sites'] for group in cfg['studies'] if group['kind']=='convergence')
    if not initial_checks:
        for n in sizes:
            path=run/'studies'/f'source-{n}'/'summary.json'
            if path.is_file() and records.load_document(path).get('spatial_check',{}).get('passed'):
                return n
    return sizes[-1]

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

def require_ai_evidence(step,report):
    if step['kind']!='optimization_search' or not step.get('method','').startswith('llm_'): return
    ai=report.get('ai',{}); stats=ai.get('stats',{})
    bench=records.load_document(run/'gpu_benchmark.json') if (run/'gpu_benchmark.json').is_file() else {}
    definition=run/'definitions'/(step['id']+'.yaml')
    options=records.load_document(definition).get('search',{}).get('options',{}) if definition.is_file() else {}
    reason=None
    if not ai.get('required') or not ai.get('valid'):
        reason=report.get('incomplete_reason') or ai.get('error') or 'AI search has no valid inference evidence; old BO fallback results cannot be used as AI results'
    elif ai.get('model')!=bench.get('chosen_model') or ai.get('digest')!=bench.get('digest') or ai.get('model')!=options.get('model') or ai.get('digest')!=options.get('model_digest'):
        reason='AI result model identity differs from its frozen inputs'
    elif stats.get('calls',0)<1 or stats.get('accepted',0)<1 or not ai.get('records'):
        reason='AI search has no successful inference and physically valid proposals'
    elif step['method']=='llm_tools' and stats.get('tools',0)<1:
        reason='AI tools search has no executed numerical or source-reading tool'
    elif not any('error' not in row for row in ai['records']) or any(row.get('model')!=ai['model'] or row.get('digest')!=ai['digest'] for row in ai['records'] if 'error' not in row):
        reason='AI inference records contain a different model identity'
    elif not isinstance(ai.get('selected_llm_points'),int) or ai['selected_llm_points']<0:
        reason='AI result is missing its measured LLM-selection count'
    if reason:
        records.write_json(run/'queue_status.json',{'state':'paused','stage':step['id'],'reason':reason,
            'previous_inference':{'calls':stats.get('calls',0),'accepted':stats.get('accepted',0),'tools':stats.get('tools',0),
                                  'measured_llm_points':ai.get('measured_llm_points',0)},'updated':time.time()},overwrite=True)
        print(reason,file=sys.stderr)
        raise SystemExit(75)

def ai_preflight(step,options):
    # Check the current service and GPU allocation, including after model unload.
    import urllib.request
    try:
        service=records.load_document(run/'ollama-service.json')
        pid=int(service['pid']); fields=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()
        if int(fields[19])!=int(service['starttime']): raise ValueError('owned Ollama process changed')
        if records.sha256_file(Path(f'/proc/{pid}/exe'))!=service['runtime_binary_sha256']:
            raise ValueError('Ollama runtime differs from its recorded identity')
        if not set(os.sched_getaffinity(pid))<=set(limits['llm_cpus']): raise ValueError('Ollama CPU affinity exceeds its allocation')
        host=options['host'].rstrip('/')
        if host.rstrip('/')!=service.get('host',service.get('endpoint','')).rstrip('/'): raise ValueError('Ollama endpoint differs from its recorded process')
        check=records.load_document(run/'gpu_tool_check.json')
        if not check.get('passed') or check.get('model')!=options['model'] or check.get('digest')!=options['model_digest']:
            raise ValueError('current AI proposal/tool check has not passed for the chosen model')
        for name in ('search.py','physics.py','parameters.yaml'):
            identity=check.get('source_identity',{}).get(name,{})
            if identity.get('frozen_sha256')!=records.sha256_file(source/name): raise ValueError('AI proposal/tool check predates the frozen source: '+name)
        hardware=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.free','--format=csv,noheader,nounits'],text=True)
        cards={}
        for line in hardware.splitlines():
            index,uuid,name,free=[part.strip() for part in line.split(',')]
            cards[uuid]={'index':int(index),'name':name,'free_MiB':float(free)}
        allowed=set(service['gpu_uuids']); reserved=set(service.get('reserved_gpu_indices',[6,7]))
        if not 0<len(allowed)<=min(6,int(cfg['execution'].get('max_gpus',6))) or not allowed<=cards.keys(): raise ValueError('invalid visible GPU allocation')
        if any(cards[uuid]['index'] in reserved or cards[uuid]['name']!='NVIDIA RTX A6000' for uuid in allowed): raise ValueError('reserved or unsupported GPU is visible to Ollama')
        environment=dict(row.split('=',1) for row in Path(f'/proc/{pid}/environ').read_bytes().decode().split('\0') if '=' in row)
        if set(environment.get('CUDA_VISIBLE_DEVICES','').split(','))!=allowed or environment.get('OLLAMA_VULKAN','').lower()!='false':
            raise ValueError('live inference backend does not exclude the reserved GPUs')
        if not 0<int(options.get('num_thread',0))<=len(limits['llm_cpus']): raise ValueError('LLM thread count exceeds its CPU allocation')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(host+'/api/tags',timeout=5) as response: tags={row['name']:row for row in json.load(response)['models']}
        if tags.get(options['model'],{}).get('digest')!=options['model_digest']: raise ValueError('Ollama tag or weights changed')
        request=urllib.request.Request(host+'/api/generate',data=json.dumps({'model':options['model'],'prompt':'','stream':False,'keep_alive':'5m',
            'options':{key:options[key] for key in ('temperature','num_ctx','num_predict','num_thread') if key in options}}).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=180) as response: loaded=json.load(response)
        if loaded.get('error') or not loaded.get('done'): raise ValueError('Ollama model loading did not finish')
        with opener.open(host+'/api/ps',timeout=5) as response: resident={row.get('name',row.get('model')):row for row in json.load(response)['models']}
        model=resident.get(options['model'],{})
        if model.get('digest')!=options['model_digest'] or model.get('size_vram',0)<.98*max(1,model.get('size',0)):
            raise ValueError('chosen model is not fully resident on GPUs; CPU fallback is not allowed')
        processes={}
        for path in Path('/proc').iterdir():
            if not path.name.isdigit(): continue
            try: processes[int(path.name)]=int((path/'stat').read_text().split(') ',1)[1].split()[1])
            except (OSError,ValueError,IndexError): pass
        owned={pid}
        while True:
            added={p for p,parent in processes.items() if parent in owned}-owned
            if not added: break
            owned.update(added)
        compute=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_gpu_memory','--format=csv,noheader,nounits'],text=True)
        devices=[]
        for line in compute.splitlines():
            uuid,process,memory=[part.strip() for part in line.split(',')]
            if int(process) in owned: devices.append({'uuid':uuid,'pid':int(process),'memory_MiB':float(memory)})
        used={row['uuid'] for row in devices}
        if not used or not used<=allowed: raise ValueError('actual Ollama CUDA devices differ from the allowed GPUs')
        free=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,memory.free','--format=csv,noheader,nounits'],text=True)
        headroom={row.split(',')[0].strip():float(row.split(',')[1]) for row in free.splitlines()}
        if any(headroom[uuid]<1024*float(cfg['execution'].get('gpu_memory_reserve_gb',2)) for uuid in used): raise ValueError('GPU free-memory reserve reached')
        record={'passed':True,'model':options['model'],'digest':options['model_digest'],'service_pid':pid,'devices':devices,'resident':model,
                'source_identity':check['source_identity'],'gpu_free_MiB':headroom,'updated':time.time()}
    except Exception as exc:
        record={'passed':False,'reason':str(exc),'updated':time.time()}
        records.write_json(run/'preflight'/(step['id']+'.json'),record,overwrite=True)
        records.write_json(run/'queue_status.json',{'state':'paused','stage':step['id'],'reason':'AI service check failed: '+str(exc),'updated':time.time()},overwrite=True)
        raise SystemExit(75)
    records.write_json(run/'preflight'/(step['id']+'.json'),record,overwrite=True)

if mode=='status':
    for name in ('queue_status.json','resources-current.json','published.json'):
        if (run/name).is_file(): print(name,records.canonical_json(records.load_document(run/name)))
    raise SystemExit(0)

limits={'workers':94,'total_mem_gb':64.,'per_task_mem_gb':2.,'system_mem_reserve_gb':16.,'minimum_free_disk_gb':20.}
if (run/'resources.json').is_file(): limits=records.load_document(run/'resources.json')
resource.setrlimit(resource.RLIMIT_AS,(int(limits['total_mem_gb']*1024**3),resource.RLIM_INFINITY))
if mode=='check-reference':
    parent=Path(records.load_document(run/'dependency.json')['run'])
    old=records.load_document(parent/'source-ready.json'); new=records.load_document(run/'source-ready.json')
    for name in ('scan.py','physics.py','analysis.py','records.py','parameters.yaml'):
        if old['source_sha256'][name]!=new['source_sha256'][name]: raise ValueError('reference physical evaluation changed: '+name)
    immutable=lambda name:name.startswith(('bin/','lib/','CrystalMaps/','geant4-data/','legacy/'))
    if {k:v for k,v in old['files_sha256'].items() if immutable(k)}!={k:v for k,v in new['files_sha256'].items() if immutable(k)}:
        raise ValueError('reference executable, libraries or physical records changed')
    for step in queue:
        path=parent/'studies'/step['reference']/'summary.json'
        if not path.is_file() or not records.load_document(path).get('archive',{}).get('verified'):
            records.write_json(run/'queue_status.json',{'state':'waiting_for_reference_measurements','reference':step['reference'],'updated':time.time()},overwrite=True)
            raise SystemExit(75)
        report=records.load_document(path)
        if not report.get('complete'): raise ValueError('reference measurement is incomplete')
        receipt=report['archive']
        if not receipt.get('independent_copy_verified') or any(not Path(receipt[key]).is_file() for key in ('path','independent_copy')):
            raise ValueError('reference needs two retained archive copies')
        if receipt.get('summary_content_hash')!=records.content_hash({k:v for k,v in report.items() if k not in ('archive','raw_files_present')}):
            raise ValueError('reference summary differs from its saved checksum')
    print('Reference queue released; physical identities and completed measurements agree.')
    raise SystemExit(0)
if mode=='verify-source':
    snapshot=records.load_document(run/'source-ready.json')
    actual={str(path.relative_to(source)):records.sha256_file(path) for path in sorted(source.rglob('*')) if path.is_file()}
    if actual!=snapshot['files_sha256']: raise ValueError('frozen source or physical inputs changed; retain this run and inspect the difference')
    print('Verified frozen source, executable, libraries, lattice maps and Geant4 data.')
    raise SystemExit(0)
if mode=='reuse':
    previous=Path(sys.argv[-1]).resolve()
    if previous==run.resolve(): raise ValueError('reuse requires a different, preserved run')
    old_source=records.load_document(previous/'source-ready.json')
    new_source=records.load_document(run/'source-ready.json')
    for name in ('scan.py','physics.py','analysis.py','records.py','parameters.yaml'):
        if old_source['source_sha256'][name]!=new_source['source_sha256'][name]:
            raise ValueError('physical evaluation changed; cannot reuse measurements: '+name)
    immutable=lambda name: name.startswith(('bin/','lib/','CrystalMaps/','geant4-data/','legacy/')) or name in ('studies/reference.yaml',cfg['calibration_file'])
    old_inputs={name:digest for name,digest in old_source['files_sha256'].items() if immutable(name)}
    new_inputs={name:digest for name,digest in new_source['files_sha256'].items() if immutable(name)}
    if old_inputs!=new_inputs: raise ValueError('transport executable, libraries, maps or physical input records changed')
    for n in defaults['design_counts']:
        if records.sha256_file(previous/'designs'/f'stratified_{n}.json')!=records.sha256_file(run/'designs'/f'stratified_{n}.json'):
            raise ValueError('source positions or weights changed')
    old_queue={step['id']:step for step in json.loads((previous/'queue.json').read_text())}
    copied=[]; omitted=[]
    for step in queue:
        identifier=step['id']; path=previous/'studies'/identifier/'summary.json'
        ai=step.get('method','').startswith('llm_') or identifier.startswith('drift-llm_')
        if ai or old_queue.get(identifier)!=step or not path.is_file():
            omitted.append(identifier); continue
        report=records.load_document(path)
        if not report.get('complete') or not report.get('archive',{}).get('verified'):
            omitted.append(identifier); continue
        receipt=verify_saved_archive(report)
        destination=run/'studies'/identifier; destination.mkdir(parents=True,exist_ok=True)
        for name in ('summary.json','resolved.json','build.json','archive.json'):
            original=path.parent/name
            if original.is_file():
                target=destination/name
                if target.exists() and records.sha256_file(target)!=records.sha256_file(original):
                    raise ValueError('reuse would replace different existing evidence: '+str(target))
                if not target.exists(): shutil.copy2(original,target)
        definition=previous/'definitions'/(identifier+'.yaml')
        (run/'definitions').mkdir(exist_ok=True)
        if definition.is_file(): shutil.copy2(definition,run/'definitions'/definition.name)
        copied.append({'id':identifier,'summary':str(path),'sha256':records.sha256_file(path),
                       'archive':receipt['path'],'independent_copy':receipt['independent_copy']})
        print('Verified retained measurements:',identifier,flush=True)
    for name in ('calibration-background.json','initial-observations','gpu_benchmark.json','ollama-service.json'):
        original=previous/name
        if original.is_dir(): shutil.copytree(original,run/name,dirs_exist_ok=True)
        elif original.is_file(): shutil.copy2(original,run/name)
    records.write_json(run/'reuse.json',{'previous_run':str(previous),'retained':copied,'not_reused':omitted,
        'ai_protocol':'All AI stages repeat from their shared starts under protocol 4; earlier partial and complete AI records remain untouched.',
        'physical_code_unchanged':True,'position_files_unchanged':True,'created':time.time()},overwrite=True)
    raise SystemExit(0)
if mode in ('check','prepare'):
    requested=list(defaults['design_counts'])
    for group in cfg['studies']:
        sampling=group.get('sampling',{})
        requested.extend(sampling[key] for key in ('sites','design') if key in sampling)
        if group['kind']=='convergence': requested.extend(group['sites'])
    if max_sites<1 or any(not isinstance(n,int) or isinstance(n,bool) or not 0<n<=max_sites for n in requested):
        raise ValueError(f'every source design must contain 1 to {max_sites} sites')
    allowed=sorted(os.sched_getaffinity(0)); cap=len(allowed)//2
    if cap<1: raise ValueError('at least two available CPUs are required')
    physical={}
    for cpu in allowed:
        topology=Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key=((topology/'physical_package_id').read_text().strip(),(topology/'core_id').read_text().strip()) if topology.is_dir() else ('0',str(cpu))
        physical.setdefault(key,[]).append(cpu)
    ordered=[group[0] for group in physical.values()]+[cpu for group in physical.values() for cpu in group[1:]]
    reserved=int(cfg['execution'].get('llm_cpu_count',0)) if optimization or focused else 0
    if cap<reserved+3: raise ValueError('insufficient CPUs for the declared LLM and transport allocation')
    if optimization and not 1<=int(cfg['execution'].get('max_gpus',6))<=6:
        raise ValueError('at most six GPUs are available to this study')
    limits.update(cpus=ordered[reserved:cap],llm_cpus=ordered[:reserved],all_cpus=ordered[:cap],cpu_cap=cap,workers=min(cfg['execution']['workers'],max(1,cap-reserved-2)))
    reference=scan.load_study(repo/'studies/reference.yaml')
    parameters={name:{'mode':'fixed','value':row['default']} for name,row in reference['parameter_table']['parameters'].items()} if optimization else {}
    parameters.update({name:{'mode':'search','bounds':bounds} for name,bounds in cfg['search_bounds'].items()})
    parameters['temperature']={'mode':'fixed','value':defaults['temperature_K']}
    space=physics.search_space(parameters,table=reference['parameter_table']['parameters'])
    assert len(space['names'])==(6 if optimization else 17)
    native=base_study('native-check',{'kind':'sobol','sites':16,'replicas':2,'events_per_task':1},'materials')
    native['constraints']={}
    native['candidates']=[{'name':'/'.join(row),'materials':list(row)} for row in itertools.product(('Si','Ge','GaAs'),('Nb','Ta','Ti'),('Cu','Au'))]
    native=scan.load_study(native)
    for point in scan.candidate_table(native): physics.prepare_values(native,point)
    if focused:
        checked=scan.load_study({**base_study('focused-check',{'kind':'sobol','sites':16,'replicas':2,'events_per_task':1}),
                                'candidates':cfg['candidates']})
        for point in scan.candidate_table(checked): physics.prepare_values(checked,point)
        names={point['name'] for point in cfg['candidates']}
        pairs=cfg['comparison']['pairs']
        if not pairs or any(pair[key] not in names for pair in pairs for key in ('candidate','reference')):
            raise ValueError('every declared comparison must refer to a fixed candidate')
        if not 0<cfg['comparison']['family_error']<1 or not 0<cfg['comparison']['meaningful_reduction']<1:
            raise ValueError('comparison probabilities and reduction must lie between zero and one')
    if optimization:
        calibration=records.load_document((source if (run/'source-ready.json').is_file() else repo)/cfg['calibration_file'])
        if len(calibration['candidates'])!=12: raise ValueError('calibration requires twelve declared full-input cases')
        validation=scan.load_study(base_study('calibration-check',{'kind':'sobol','sites':16,'replicas':2,'events_per_task':1}))
        for point in calibration['candidates']: physics.prepare_values(validation,point)
    if mode=='check':
        stages=3+len(cfg['pilot_methods'])+len(cfg['search_seeds'])*(1+2*len(cfg['methods']))+len(defaults['confirmation_position_seeds'])+len(refinement.get('fixed_studies',[])) if optimization else len(cfg['studies'])
        print(records.canonical_json({'search_dimensions':len(cfg['orientation_bounds']) if native_search else len(space['names']),'native_material_cases':18,'stages':stages,'max_sites':max_sites,'stop_after_initial_checks':initial_checks,'cpu_cap':cap,'workers':limits['workers'],'memory':scan.memory_headroom(),'source_region_mm':8,'temperature_K':defaults['temperature_K']}))
        raise SystemExit(0)
    run.mkdir(parents=True,exist_ok=True)
    relative_config=str(config_path.relative_to(repo if not (run/'source-ready.json').is_file() else source))
    records.write_json(run/'queue-config.json',{'relative_path':relative_config,'sha256':records.sha256_file(config_path)})
    if not (run/'source-ready.json').is_file():
        if source.exists(): raise ValueError('unfinished source snapshot requires inspection')
        environment,build=scan.prepare_environment(reference['installation'])
        source.mkdir()
        for name in ('scan.py','physics.py','analysis.py','records.py','search.py','run_all.sh','parameters.yaml'):
            shutil.copy2(repo/name,source/name)
        (source/'figures').mkdir()
        shutil.copy2(repo/'figures/plot.py',source/'figures/plot.py')
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
        records.write_json(run/'source-ready.json',{'original_build':build,'frozen_build':frozen,'files_sha256':inventory,'source_sha256':{name:inventory[name] for name in ('scan.py','physics.py','analysis.py','records.py','search.py','run_all.sh','parameters.yaml','figures/plot.py')},'created':time.time()})
        for path in source.rglob('*'):
            if path.is_file(): path.chmod(0o555 if path==source/'bin/Main' else 0o444)
    records.write_json(run/'resources.json',limits)
    fixed=reference['fixed']; fixed['position_half_span_mm']=defaults['source_half_span_mm']
    (run/'designs').mkdir(exist_ok=True)
    base_file=Path(defaults['base_design_file'])
    if not base_file.is_absolute(): base_file=repo/base_file
    previous_design=records.load_document(base_file)
    base_n=len(previous_design['sites_mm'])
    if base_n>max_sites: raise ValueError('the recorded base design exceeds the source-site limit')
    if previous_design.get('design_hash')!=records.content_hash({k:v for k,v in previous_design.items() if k!='design_hash'}):
        raise ValueError('the recorded source design checksum differs')
    records.write_json(run/'base-design.json',{'path':str(base_file),'file_sha256':records.sha256_file(base_file),'design_hash':previous_design['design_hash'],'sites':base_n})
    convergence_sizes=defaults['design_counts'] if optimization or focused else next(group['sites'] for group in cfg['studies'] if group['kind']=='convergence')
    for n in defaults['design_counts']:
        sampling={'kind':'stratified','sites':n,'position_seed':defaults['position_seed'],
                  'area_samples':defaults['area_samples'],'area_seed':defaults['area_seed'],
                  'allocation':dict(zip(physics.REGIONS,defaults['allocations'][str(n)])),
                  'stratum_weights':previous_design['stratum_weights']}
        fresh=defaults.get('fresh_designs',False)
        prior=previous_design if n>base_n and not fresh else None
        design=copy.deepcopy(previous_design) if n==base_n and not fresh else physics.build_design(fixed,sampling,previous=prior)
        if design['stratum_counts']!=sampling['allocation']: raise ValueError('the declared allocation differs from its recorded source design')
        design['design_hash']=records.content_hash({k:v for k,v in design.items() if k!='design_hash'})
        records.write_json(run/'designs'/f'stratified_{n}.json',design)
        if n not in convergence_sizes or n==base_n: continue
        for family,seeds in [('source-independent',defaults['independent_position_seeds']),('unused-stream-confirmation',defaults['confirmation_position_seeds'])]:
            for k,seed in enumerate(seeds,1):
                sampling['position_seed']=seed
                independent=physics.build_design(fixed,sampling)
                independent['design_hash']=records.content_hash({k:v for k,v in independent.items() if k!='design_hash'})
                if independent['stratum_weights']!=previous_design['stratum_weights']: raise ValueError('independent layouts must share area weights')
                records.write_json(run/'designs'/f'{family}-{k:02d}_{n}.json',independent)
    if spatial_verify:
        parent=(repo/cfg['reference_run']).resolve()
        records.write_json(run/'dependency.json',{'run':str(parent),'reference_ids':[s['reference'] for s in cfg['studies']]})
        for step in cfg['studies']:
            previous=records.load_document(parent/'designs'/step['parent_design'])
            sampling={'kind':'stratified','sites':2048,'position_seed':step['position_seed'],
                      'allocation':dict(zip(physics.REGIONS,defaults['allocations']['2048'])),
                      'stratum_weights':previous['stratum_weights']}
            design=physics.build_design(fixed,sampling,previous=previous)
            if design['sites_mm'][:1024]!=previous['sites_mm'] or design['stratum_weights']!=previous['stratum_weights']:
                raise ValueError('verification must retain all reference positions and area weights')
            design['design_hash']=records.content_hash({k:v for k,v in design.items() if k!='design_hash'})
            records.write_json(run/'designs'/step['sampling']['design_file'],design)
    independent_root=Path(os.environ.get('MATERIAL_ARCHIVE_ROOT',cfg['execution']['independent_archive_root']))/run.name
    independent_root.mkdir(parents=True,exist_ok=True)
    if independent_root.stat().st_dev==run.stat().st_dev: raise ValueError('archive copies require a separate persistent filesystem')
    if shutil.disk_usage(independent_root).free<limits['minimum_free_disk_gb']*1024**3: raise ValueError('independent archive disk reserve unavailable')
    records.write_json(run/'archive-location.json',{'path':str(independent_root),'device':independent_root.stat().st_dev,'working_device':run.stat().st_dev})
    queue=[] if optimization or focused else [{'id':'pilot','kind':'pilot','sampling':{'design':128,'replicas':2,'events_per_task':31250,'seed_bank':defaults.get('pilot_seed_bank',2100)}}]
    if optimization:
        # One initial table per seed is measured once and imported into every arm.
        calibration=records.load_document(source/cfg['calibration_file'])
        if len(calibration['candidates'])!=12: raise ValueError('calibration requires twelve declared full-input cases')
        sampling={'design':512,'replicas':4,'events_per_task':31250}
        queue.extend([
            {'id':'calibration','kind':'optimization_calibration','sampling':sampling,'bank':3100},
            {'id':'calibration-resolution','kind':'optimization_resolution','sampling':{**sampling,'design':1024},'bank':3100},
            {'id':'pilot-initial','kind':'optimization_initial','count':12,'seed':41,'sampling':{**sampling,'design':128},'bank':3110},
        ])
        for k,method in enumerate(cfg['pilot_methods']):
            queue.append({'id':'pilot-'+method,'kind':'optimization_search','method':method,'initial':'pilot-initial','seed':41,'count':8,'sampling':{**sampling,'design':128},'bank':3120+k})
        for k,seed in enumerate(cfg['search_seeds']):
            initial=f'initial-{seed}'
            queue.append({'id':initial,'kind':'optimization_initial','count':24,'seed':seed,'sampling':sampling,'bank':3200+100*k})
            methods=cfg.get('original_methods',cfg['methods'])
            methods=methods[k:]+methods[:k]
            for j,method in enumerate(methods):
                identifier=f'{method}-{seed}'
                queue.append({'id':identifier,'kind':'optimization_search','method':method,'initial':initial,'seed':seed,'count':32,'sampling':sampling,'bank':3210+100*k+j})
                queue.append({'id':'drift-'+identifier,'kind':'optimization_drift','sampling':sampling,'bank':3250+100*k+j})
        for k,seed in enumerate(cfg['search_seeds']):
            for j,method in enumerate(refinement.get('additional_methods',[])):
                identifier=f'{method}-{seed}'
                queue.append({'id':identifier,'kind':'optimization_search','method':method,'initial':f'initial-{seed}',
                              'seed':seed,'count':32,'sampling':sampling,'bank':3800+10*k+j})
                queue.append({'id':'drift-'+identifier,'kind':'optimization_drift','sampling':sampling,'bank':3850+10*k+j})
        queue.extend({**group,'kind':'optimization_fixed'} for group in refinement.get('fixed_studies',[]))
        for k,seed in enumerate(defaults['confirmation_position_seeds'],1):
            queue.append({'id':f'optimization-confirmation-{k:02d}','kind':'optimization_confirmation',
                          'sampling':{'design':1024,'design_file':f'unused-stream-confirmation-{k:02d}_1024.json','replicas':8,'events_per_task':62500},'bank':3600+k,'position_seed':seed})
        targets=len(cfg['methods'])*len(cfg['search_seeds'])+len(cfg['search_seeds'])+len(cfg['confirmation_anchors'])+len(refinement.get('fixed_studies',[]))
        added=106240000000+len(refinement.get('additional_methods',[]))*len(cfg['search_seeds'])*33*64000000
        added+=sum(len(group['candidates'])*group['sampling']['design']*group['sampling']['replicas']*group['sampling']['events_per_task'] for group in refinement.get('fixed_studies',[]))
        added+=(targets-27)*4*1024*8*62500
        records.write_json(run/'planned-computation.json',{'primaries_upper_bound':added,'new_measurements_screening':len(cfg['search_seeds'])*(24+len(cfg['methods'])*32),'confirmation_targets_upper_bound':targets,
            'interpretation':'Failed attempts add transport. Identical confirmation vectors are measured once. Model timing is measured separately.'})
    groups=[g for g in cfg['studies'] if g['kind'] in ('convergence','independent_positions','event_count_convergence')]+[g for g in cfg['studies'] if g['kind'] not in ('convergence','independent_positions','event_count_convergence')]
    for group in groups:
        kind=group['kind']
        if initial_checks and kind not in ('convergence','independent_positions','event_count_convergence'): continue
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
    if focused and not native_search:
        primaries=sum(step['sampling']['design']*step['sampling']['replicas']*step['sampling']['events_per_task']*len(cfg['candidates']) for step in queue)
        records.write_json(run/'planned-computation.json',{'primaries':primaries,'layouts':len(queue),
            'candidates':len(cfg['candidates']),'comparisons':cfg['comparison'],
            'interpretation':'All fixed candidates use matched positions and streams within each independent layout.'})
    if native_search:
        counts={'focused':len(cfg['candidates']),'native_initial':cfg['initial_count'],'native_search':cfg['search_count'],
                'native_confirmation':len(cfg['candidates'])+len(cfg['materials'])*len(cfg['search_seeds'])*(1+len(cfg['methods']))}
        work={kind:sum(step['sampling']['design']*step['sampling']['replicas']*step['sampling']['events_per_task']*counts[kind]
                      for step in queue if step['kind']==kind) for kind in counts}
        records.write_json(run/'planned-computation.json',{'primaries_upper_bound':sum(work.values()),'by_kind':work,
            'confirmation_targets_upper_bound':counts['native_confirmation'],'tasks_upper_bound':sum(work.values())//31250})
    records.write_json(run/'queue_status.json',{'state':'prepared','stages':len(queue),'updated':time.time()},overwrite=True)
    files=['README.md','docs/results/optimization_summary.json','docs/results/optimization_summary.md','figures/optimization_results.png'] if optimization else ['README.md','docs/results/full_scale_summary.json','figures/full_scale_finalists.png','figures/full_scale_transport_morris.png','figures/full_scale_density_morris.png','figures/full_scale_methods.png','figures/full_scale_projection.png']
    if focused:
        files=['README.md','docs/results/orientation_materials.json','docs/results/orientation_materials.md','figures/orientation_materials.png']
    if native_search:
        files=['README.md','docs/results/native_optimization.json','docs/results/native_optimization.md','figures/native_optimization.png']
    if spatial_verify:
        files=['README.md','docs/results/spatial_2048.json','docs/results/spatial_2048.md','figures/spatial_2048.png']
    message='Report limited 2048-site verification' if spatial_verify else 'Report native material orientation optimization' if native_search else 'Report matched rotation and native-material comparison' if focused else 'Report confirmed optimization comparisons' if optimization else 'Report fresh full-scale material studies'
    records.write_json(run/'publication.json',{'branch':subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip(),'remote':subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip(),'files':files,'commit_message':message})
    print(records.canonical_json({'prepared_studies':len(queue),'source':str(source),'resources':limits}))
    raise SystemExit(0)

index=int(sys.argv[6]) if mode in ('stage','record','failed','pause','check-ai','defer-ai') or mode=='verify-ai' and len(sys.argv)>6 else None
if mode=='defer-ai':
    step=queue[index]
    if step['kind']!='optimization_search' or not step.get('method','').startswith('llm_'): raise SystemExit(1)
    path=run/'studies'/step['id']/'summary.json'
    report=records.load_document(path) if path.is_file() else {}
    state=records.load_document(run/'queue_status.json')
    paused=records.load_document(run/'ai-paused.json') if (run/'ai-paused.json').is_file() else {}
    paused[step['id']]={'reason':report.get('incomplete_reason') or state.get('reason'),
        'stats':report.get('ai',{}).get('stats',{}),'measured':report.get('search_counts',{}).get('measured',0),'updated':time.time()}
    records.write_json(run/'ai-paused.json',paused,overwrite=True)
    print('AI stage retained for retry; independent CPU studies continue:',step['id'],file=sys.stderr)
    raise SystemExit(0)
if mode=='verify-ai':
    for step in ([queue[index]] if index is not None else queue):
        if step['kind']=='optimization_search' and step.get('method','').startswith('llm_'):
            path=run/'studies'/step['id']/'summary.json'
            require_ai_evidence(step,records.load_document(path) if path.is_file() else {})
    raise SystemExit(0)
if mode=='check-ai':
    step=queue[index]
    if step['kind']!='optimization_search' or not step.get('method','').startswith('llm_'): raise ValueError('choose an AI search stage')
    definition=records.load_document(run/'definitions'/(step['id']+'.yaml'))
    ai_preflight(step,definition['search']['options'])
    print('Verified current Ollama model, frozen tool check and GPU allocation.')
    raise SystemExit(0)
if mode=='stage':
    step=queue[index]; identifier=step['id']; output=run/'studies'/identifier
    if step['kind']=='optimization_confirmation':
        pending=[]
        for entry in queue:
            if entry['kind']=='optimization_confirmation': continue
            path=run/'studies'/entry['id']/'summary.json'
            report=records.load_document(path) if path.is_file() else {}
            if not (report.get('complete') or report.get('terminated_search')): pending.append(entry['id'])
        if pending:
            records.write_json(run/'confirmation-waiting.json',{'pending':pending,'updated':time.time()},overwrite=True)
            print('SKIP'); raise SystemExit(0)
    if step['kind']=='optimization_drift':
        parent=identifier.removeprefix('drift-')
        if parent.startswith('llm_'):
            path=run/'studies'/parent/'summary.json'
            report=records.load_document(path) if path.is_file() else {}
            if not (report.get('complete') or report.get('terminated_search')) or not report.get('ai',{}).get('valid'):
                print('SKIP'); raise SystemExit(0)
    if (output/'summary.json').is_file():
        old=records.load_document(output/'summary.json')
        if 'archive' not in old and (output/'archive.json').is_file():
            old['archive']=records.load_document(output/'archive.json'); old['raw_files_present']=False
        if (old.get('complete') or old.get('terminated_search')) and old.get('archive',{}).get('verified'):
            require_ai_evidence(step,old)
            receipt=verify_saved_archive(old)
            compact_study(output,old,receipt)
            print('SKIP'); raise SystemExit(0)
    if optimization:
        kind=step['kind']; sampling={**step['sampling'],'seed_bank':step['bank']}
        raw=base_study(identifier,sampling)
        reference=scan.load_study(source/'studies/reference.yaml')
        raw['analysis'].update(comparisons=False,diagnostics=False,bootstrap_draws=256)
        raw['sampling'].update(independent_candidate_streams=kind!='optimization_confirmation',stream_group=identifier)
        calibration=records.load_document(source/cfg['calibration_file'])
        if kind in ('optimization_calibration','optimization_resolution'):
            names=set(cfg['resolution_cases']) if kind=='optimization_resolution' else None
            raw['candidates']=[point for point in calibration['candidates'] if names is None or point['name'] in names]
            if len(raw['candidates'])!=(4 if names else 12): raise ValueError('declared calibration cases are absent')
        elif kind=='optimization_drift':
            raw['candidates']=[{'name':'reference','values':{}}]
        elif kind=='optimization_fixed':
            if step['background']=='calibration':
                background=records.load_document(run/'calibration-background.json')['candidate']
                raw['realization']=background.get('realization',{})
                raw['parameters'].update({name:{'value':value} for name,value in background['values'].items() if name in reference['parameter_table']['parameters']})
            raw['candidates']=copy.deepcopy(step['candidates'])
            raw['sampling']['independent_candidate_streams']=False
            raw['analysis'].update(comparisons=True,diagnostics=True)
            raw['provenance']={'question':step.get('question',identifier),'background':step['background'],'selection_uses_confirmation':False}
        elif kind in ('optimization_initial','optimization_search'):
            background=records.load_document(run/'calibration-background.json')['candidate']
            raw['realization']=background.get('realization',{})
            raw['parameters'].update({name:{'mode':'fixed','value':value} for name,value in background['values'].items() if name in reference['parameter_table']['parameters']})
            raw['parameters'].update({name:{'mode':'search','bounds':bounds,'value':background['values'][name]} for name,bounds in cfg['search_bounds'].items()})
            if kind=='optimization_initial':
                from scipy.stats import qmc
                study=scan.load_study(raw); points=[]; seen=set()
                boundary={**study['space']['baseline'],'sub_c44':cfg['search_bounds']['sub_c44'][0]}
                draws=[study['space']['baseline'],boundary]
                draws.extend(physics.from_unit(u,study['space']) for u in qmc.Sobol(d=6,scramble=True,seed=step['seed']).random_base2(10))
                for values in draws:
                    point={'name':f'common-{len(points):02d}','values':values}
                    try: physics.prepare_values(study,point)
                    except ValueError: continue
                    checksum=records.content_hash(values)
                    if checksum in seen: continue
                    seen.add(checksum); points.append(point)
                    if len(points)==step['count']: break
                if len(points)!=step['count']: raise ValueError('insufficient distinct physically valid common starts')
                raw['candidates']=points
            else:
                settings=cfg['method_settings'][step['method']]
                raw['kind']='search'; raw.pop('candidates')
                options={**cfg['optimizer_options'],**settings.get('options',{})}
                options.update(pool_seed=step['seed'],source_records=calibration.get('source_records',{}),
                               units={name:reference['parameter_table']['parameters'][name]['unit'] for name in cfg['search_bounds']})
                if step['method'].startswith('llm_'):
                    while True:
                        path=run/'gpu_benchmark.json'
                        bench=records.load_document(path) if path.is_file() else {}
                        tool_path=run/'gpu_tool_check.json'
                        tool_check=records.load_document(tool_path) if tool_path.is_file() else {}
                        if bench.get('complete') and (not bench.get('passed') or tool_check): break
                        records.write_json(run/'queue_status.json',{'state':'waiting_for_gpu_checks','stage':identifier,'index':index,'updated':time.time()},overwrite=True)
                        time.sleep(15)
                    if not bench.get('passed') or not bench.get('gpu_verified') or not bench.get('digest') or not tool_check.get('passed'):
                        records.write_json(run/'queue_status.json',{'state':'paused','stage':identifier,'index':index,'reason':'GPU model and proposal/tool checks must pass before AI transport','updated':time.time()},overwrite=True)
                        raise SystemExit(75)
                    options.update(bench.get('options',{}),model=bench['chosen_model'],host=bench['host'],model_digest=bench['digest'])
                    options.update({key:cfg['optimizer_options'][key] for key in ('num_ctx','num_predict','num_thread','temperature','timeout') if key in cfg['optimizer_options']})
                    if 'think' in bench: options['think']=bench['think']
                    options['agent_tools']=step['method']=='llm_tools'
                    options['require_ai']=True
                    options['require_gpu']=True
                    ai_preflight(step,options)
                raw['search']={'method':settings['method'],'seed':step['seed'],'evaluations':step['count'],
                    'measured_evaluations':True,'max_attempts':3*step['count'],'max_elapsed_s':cfg['search_max_elapsed_s'],
                    'conditional_transport_noise':True,'record_runtime':True,
                    'initial_observations_file':str(run/'initial-observations'/(step['initial']+'.json')),
                    'options':options,'parallel_candidates':1}
                raw['analysis']['store_search_blocks']=False
                raw['provenance']={'method_id':step['method'],'initial_study':step['initial'],'background':str(run/'calibration-background.json')}
        elif kind=='optimization_confirmation':
            for entry in queue:
                if entry['kind']=='optimization_search' and entry.get('method','').startswith('llm_'):
                    require_ai_evidence(entry,records.load_document(run/'studies'/entry['id']/'summary.json'))
            selection=run/'selected-leaders.json'
            if not selection.is_file():
                points=[point for point in calibration['candidates'] if point['name'] in cfg['confirmation_anchors']]
                for entry in queue:
                    if entry['kind'] not in ('optimization_initial','optimization_search','optimization_fixed') or entry['id'].startswith('pilot-'): continue
                    report=records.load_document(run/'studies'/entry['id']/'summary.json')
                    measured=[point for point in [*report.get('initial_results',[]),*report['results']] if point.get('status','complete')=='complete']
                    if not measured: continue
                    point=min(measured,key=lambda row:row['value'])
                    definition=records.load_document(run/'definitions'/(entry['id']+'.yaml'))
                    original=next((p for p in definition.get('candidates',[]) if p['name']==point.get('name')), {})
                    points.append({**{key:original[key] for key in ('materials',) if key in original},
                                   'name':entry['id'],'values':point.get('parameters',point.get('values')),'realization':definition.get('realization',{}),
                                   'selection':{'candidate_id':point['candidate_id'],'search_value':point['value'],'search_target_reached':report.get('search_counts',{}).get('target_reached')}})
                distinct={}; aliases={}
                study=scan.load_study({**raw,'candidates':points})
                for point in points:
                    prepared=physics.prepare_values(study,point)
                    key=records.content_hash({k:prepared[k] for k in ('values','derived','fixed','top_film','bottom_film')})
                    aliases.setdefault(key,[]).append(point['name']); distinct.setdefault(key,point)
                records.write_json(selection,{'candidates':list(distinct.values()),'aliases':aliases,'created':time.time(),'selection_uses_confirmation':False})
            raw['candidates']=[{key:value for key,value in point.items() if key!='selection'} for point in records.load_document(selection)['candidates']]
            raw['sampling']['position_seed']=step['position_seed']
            raw['analysis'].update(comparisons=True,diagnostics=True,bootstrap_draws=defaults['bootstrap_draws'])
        else: raise ValueError('unknown optimization study')
    elif native_search and step['kind'] in ('native_initial','native_search'):
        raw=base_study(identifier,{**step['sampling'],'seed_bank':step['bank']})
        linked={**raw,'candidates':[{'materials':[step['material'],'Nb','Cu']}]}
        study=scan.load_study(linked); baseline=physics.prepare_values(study,linked['candidates'][0])['values']
        raw['realization']={'mode':'native_g4cmp','material':step['material']}
        raw['parameters']={name:{'mode':'fixed','value':value} for name,value in baseline.items()}
        raw['parameters'].update({name:{'mode':'search','value':baseline[name],'bounds':bounds} for name,bounds in cfg['orientation_bounds'].items()})
        raw['sampling']['independent_candidate_streams']=True
        raw['analysis'].update(comparisons=False,diagnostics=False,bootstrap_draws=256)
        if step['kind']=='native_initial':
            from scipy.stats import qmc
            study=scan.load_study(raw); points=[]
            for miller,angle in itertools.product(([0,0,1],[1,1,0],[1,1,1]),(0.,45.)):
                polar,azimuth=physics.miller_to_sphere(miller)
                points.append({'name':f'initial-{len(points):02d}','values':{**baseline,
                    'orientation_polar_cos':polar,'orientation_azimuth_turns':azimuth,'lattice_deg':angle}})
            for u in qmc.Sobol(d=3,scramble=True,seed=step['seed']).random_base2(4):
                points.append({'name':f'initial-{len(points):02d}','values':physics.from_unit(u,study['space'])})
                if len(points)==step['count']: break
            raw['candidates']=points
        else:
            raw.update(kind='search'); raw.pop('candidates')
            initial=records.load_document(run/'initial-observations'/(step['initial']+'.json'))['observations']
            raw['search']={'method':cfg['methods'][step['method']],'seed':step['seed'],'evaluations':step['count'],
                'measured_evaluations':True,'max_attempts':3*step['count'],'max_elapsed_s':43200,
                'initial_observations':initial,'parallel_candidates':1,'options':cfg['optimizer_options']}
    elif native_search and step['kind']=='native_confirmation':
        raw=base_study(identifier,{**step['sampling'],'seed_bank':step['bank']})
        raw['sampling'].update(position_seed=step['position_seed'],independent_candidate_streams=False)
        selected=run/'native-selected.json'
        if not selected.exists():
            points=copy.deepcopy(cfg['candidates'])
            for entry in queue:
                if entry['kind'] not in ('native_initial','native_search'): continue
                report=records.load_document(run/'studies'/entry['id']/'summary.json')
                if not (report.get('complete') or report.get('terminated_search')): raise ValueError('screening must finish before confirmation')
                complete=[p for p in [*report.get('initial_results',[]),*report['results']] if p.get('status','complete')=='complete']
                best=min(complete,key=lambda row:row['value'])
                points.append({'name':entry['id'],'materials':[entry['material'],'Nb','Cu'],'values':best.get('parameters',best.get('values'))})
            study=scan.load_study({**raw,'candidates':points}); distinct={}; aliases={}
            for point in points:
                prepared=physics.prepare_values(study,point)
                key=records.content_hash({k:prepared[k] for k in ('values','derived','fixed','top_film','bottom_film')})
                aliases.setdefault(key,[]).append(point['name']);distinct.setdefault(key,point)
            records.write_json(selected,{'candidates':list(distinct.values()),'aliases':aliases,'selection_uses_confirmation':False})
        raw['candidates']=records.load_document(selected)['candidates']
        raw['analysis'].update(comparisons=False,diagnostics=True)
    elif focused and step['kind']=='focused':
        raw=base_study(identifier,{**step['sampling'],'seed_bank':step['bank']})
        raw['sampling'].update(position_seed=step['position_seed'],independent_candidate_streams=False)
        raw['candidates']=copy.deepcopy(cfg['candidates'])
        raw['analysis'].update(comparisons=False,diagnostics=True)
        raw['provenance']=cfg['question']
    else:
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
            if not validation['passed'] and not initial_checks:
                records.write_json(run/'queue_status.json',{'state':'needs_more_positions','stage':identifier,
                                   'reason':'Independent position precision or nested spatial interval failed; inspect region estimates before further transport.',
                                   'updated':time.time()},overwrite=True)
                raise ValueError('source-position checks did not pass; retained results require spatial refinement')
            records.write_json(run/(identifier+'-skipped.json'),{'reason':'analysis-only spatial check completed','passed':validation['passed']},overwrite=True)
            print('SKIP'); raise SystemExit(0)
        if step['kind']=='convergence' and not initial_checks and step['sites']>selected_spatial_sites():
            records.write_json(run/(identifier+'-skipped.json'),{'reason':'nested spatial intervals passed at a smaller declared design'},overwrite=True)
            print('SKIP'); raise SystemExit(0)
        sampling=dict(step['sampling'])
        selected_design=next(group['sites'][-1] for group in cfg['studies'] if group['kind']=='convergence')
        if sampling.get('design')==selected_design and step['kind']!='convergence': sampling['design']=selected_spatial_sites()
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
    if len(study['design']['sites_mm'])!=raw['sampling']['sites'] or len(study['design']['sites_mm'])>max_sites:
        raise ValueError('recorded source design differs from its declared count or exceeds the site limit')
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
    require_ai_evidence(step,report)
    if report.get('archive'):
        receipt=verify_saved_archive(report); compact_study(output,report,receipt)
        print(records.canonical_json({'stage':step['id'],'already_archived':True})); raise SystemExit(0)
    if not report['complete']:
        if step['kind'] not in ('optimization_search','native_search') or not report.get('search_counts'):
            raise ValueError('incomplete studies cannot become confirmed results')
        report['terminated_search']=True
        report['interpretation']='Declared search stopped without its complete response target; missing responses remain absent.'
        records.write_json(output/'summary.json',report,overwrite=True)
    if optimization and step['kind']=='optimization_calibration':
        declared=records.load_document(source/cfg['calibration_file'])
        originals={point['name']:point for point in declared['candidates']}
        eligible=[row for row in report['results'] if row['status']=='complete' and row['name'] in cfg['background_cases'] and
                  all(lo<=row['parameters'][name]<=hi for name,(lo,hi) in cfg['search_bounds'].items())]
        if not eligible: raise ValueError('no complete calibration point lies in the declared search domain')
        best=min(eligible,key=lambda row:row['value'])
        records.write_json(run/'calibration-background.json',{'candidate':{'name':best['name'],'values':best['parameters'],
            'realization':originals[best['name']].get('realization',{})},'calibration_value':best['value'],'candidate_id':best['candidate_id'],
            'selection':'Smallest fresh response among predefined full-input cases inside the six-dimensional domain. Calibration is not optimizer gain.'})
    elif step['kind'] in ('optimization_initial','native_initial'):
        observations=[]
        for row in report['results']:
            observations.append({'values':row['parameters'],'value':row['value'],'standard_error':row['standard_error'],
                'transport_standard_error':row['transport_standard_error'],'elapsed_s':row['elapsed_s'],
                'candidate_id':row['candidate_id'],'source':str(output/'summary.json'),'objective':report['objective']})
        (run/'initial-observations').mkdir(exist_ok=True)
        records.write_json(run/'initial-observations'/(step['id']+'.json'),{'objective':report['objective'],
            'design_hash':report['design']['design_hash'],'events_per_task':report['events_per_task'],
            'replicas':report['replicas'],'observations':observations})
    elif (native_search or spatial_verify) and step['kind']=='focused':
        # The balanced half uses the first n_h/2 positions in each region, fixed before transport.
        for row in report['results']:
            blocks=records.load_result(output,candidate_id=row['candidate_id'])['results'][0]['blocks']
            counts=analysis.prepare_counts(blocks,report['design']); coarse=0.
            for _,positions,weight in counts['groups']:
                if len(positions)%2: raise ValueError('balanced half needs even source counts in every region')
                coarse+=weight*float(counts['weighted'][positions[:len(positions)//2]].mean())
            row['balanced_half_value']=coarse
        records.write_json(output/'summary.json',report,overwrite=True)
    elif optimization and step['kind']=='optimization_resolution':
        coarse=records.load_document(run/'designs/stratified_512.json')
        coarse_design={**report['design'],'position_strata':coarse['stratum'],'sites_mm':coarse['sites_mm'],'design_hash':coarse['design_hash']}
        checks={}
        for row in report['results']:
            blocks=records.load_result(output,candidate_id=row['candidate_id'])['results'][0]['blocks']
            checks[row['name']]=analysis.nested_spatial_difference([b for b in blocks if b['position']<512],blocks,coarse_design,report['design'],
                bootstrap_draws=defaults['bootstrap_draws'],relative_limit=.05)
        report['resolution_check']={'candidates':checks,'passed':all(row['passed'] for row in checks.values()),
            'interpretation':'Fixed screening layout is an approximation; its failure remains a limitation. Independent 1024-site confirmation tests selected leaders.'}
        records.write_json(output/'summary.json',report,overwrite=True)
    elif step['kind']=='convergence':
        # Shared outputs define the nested contrast; no deterministic replay assumed.
        group=next(group for group in cfg['studies'] if group['kind']=='convergence')
        smaller=[n for n in [group.get('base_design',group['sites'][0]),*group['sites']] if n<step['sites']]
        if not smaller:
            report['spatial_check']={'baseline':True,'passed':None,'candidates':{},
                                    'interpretation':'Initial source design; no smaller nested comparison is claimed.'}
        else:
            n=max(smaller); coarse=records.load_document(run/'designs'/f'stratified_{n}.json')
            coarse_design={**report['design'],'position_strata':coarse['stratum'],'sites_mm':coarse['sites_mm'],'design_hash':coarse['design_hash']}
            checks={}
            for row in report['results']:
                blocks=records.load_result(output,candidate_id=row['candidate_id'])['results'][0]['blocks']
                checks[row['name']]=analysis.nested_spatial_difference([b for b in blocks if b['position']<n],blocks,coarse_design,report['design'],
                                          bootstrap_draws=defaults['bootstrap_draws'],relative_limit=step['check']['relative_limit'])
                checks[row['name']]['maximum_site_leverage']=row['estimate']['maximum_site_leverage']
                checks[row['name']]['passed'] &= row['estimate']['maximum_site_leverage']<=step['check']['leverage_limit']
            report['spatial_check']={'baseline':False,'coarse_sites':n,'fine_sites':step['sites'],
                                    'passed':all(c['passed'] for c in checks.values()),'candidates':checks,
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
    paused=run/'ai-paused.json'
    if paused.is_file():
        pending=records.load_document(paused); pending.pop(step['id'],None)
        if pending: records.write_json(paused,pending,overwrite=True)
        else: paused.unlink()
    records.write_json(run/'queue_status.json',{'state':'recorded','index':index,'stage':step['id'],'updated':time.time()},overwrite=True)
    print(records.canonical_json({'stage':step['id'],'compressed_archive_bytes':receipt['compressed_bytes'],'uncompressed_member_bytes':receipt['bytes'],'members':receipt['members']}))
elif mode=='finish':
    reports={}
    for step in queue:
        path=run/'studies'/step['id']/'summary.json'
        if path.exists():
            reports[step['id']]=records.load_document(path)
            require_ai_evidence(step,reports[step['id']])
        elif not (run/(step['id']+'-skipped.json')).is_file():
            records.write_json(run/'queue_status.json',{'state':'paused','stage':step['id'],
                'reason':'Unfinished studies remain; independent completed measurements are preserved. Inspect ai-paused.json and logs.',
                'updated':time.time()},overwrite=True)
            raise SystemExit(75)
    if any(not report['complete'] and not ((optimization or native_search) and report.get('terminated_search')) for report in reports.values()):
        raise ValueError('all declared measured studies must finish or have an explicitly terminated search record')
    for report in reports.values(): verify_saved_archive(report)
    if spatial_verify:
        parent=Path(records.load_document(run/'dependency.json')['run']); prior=[]
        for step in queue:
            old=records.load_document(parent/'studies'/step['reference']/'summary.json');verify_saved_archive(old)
            new=reports[step['id']]
            if new['design']['sites_mm'][:1024]!=old['design']['sites_mm'] or new['design']['stratum_weights']!=old['design']['stratum_weights']:
                raise ValueError('nested positions or area weights changed')
            if new['events_total_per_candidate']!=old['events_total_per_candidate'] or 2*new['events_per_task']!=old['events_per_task']:
                raise ValueError('verification requires equal total primaries and half the per-position primaries')
            if new['streams']!=old['streams'] or new['geometry']!=old['geometry']:
                raise ValueError('reference streams or physical geometry changed')
            for a,b in zip(new['results'],old['results']):
                if a['name']!=b['name'] or a['parameters']!=b['parameters']: raise ValueError('reference material properties changed')
            prior.append(old)
        aggregate=layout_summary([reports[step['id']] for step in queue]); reference=layout_summary(prior)
        contrasts=[]; confidence=1-cfg['comparison']['family_error']/len(cfg['candidates'])
        for row in aggregate['results']:
            name=row['name'];fine=np.asarray(row['estimate']['by_layout'])
            half=np.array([next(r['balanced_half_value'] for r in report['results'] if r['name']==name) for report in reports.values()])
            baseline=np.asarray(next(r['estimate']['by_layout'] for r in reference['results'] if r['name']==name))
            spatial=analysis.compare_repeated(fine,half,paired=True,confidence=confidence)
            relative=analysis.compare_repeated(fine/half,np.ones(len(half)),paired=True,confidence=confidence) if np.all(half>0) else None
            fixed=analysis.compare_repeated(fine/baseline,np.ones(len(baseline)),paired=True) if np.all(baseline>0) else None
            transport=analysis.compare_repeated(half/baseline,np.ones(len(baseline)),paired=True) if np.all(baseline>0) else None
            contrasts.append({'candidate':name,'spatial_difference':spatial,'spatial_relative_change':relative,
                'equivalent_within_5_percent':relative['equivalent_within_margin'] if relative else None,
                'fixed_primary_relative_change':fixed,'half_primary_count_relative_change':transport,
                'half_1024_by_layout':half.tolist(),'parent_1024_by_layout':baseline.tolist()})
        result={'id':cfg['id'],'state':'complete','date':time.strftime('%Y-%m-%d'),'objective':defaults['objective'],
                'question':cfg['question'],'results':aggregate['results'],'reference_results':reference['results'],
                'contrasts':contrasts,'simultaneous_confidence':confidence,'studies':reports,
                'reference_studies':prior,'source':records.load_document(run/'source-ready.json'),
                'resources':limits,'limitations':cfg['limitations']}
        publication=records.load_document(run/'publication.json')
        if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
            raise ValueError('publication branch or remote changed; retained results were not published')
        public=repo/'docs/results';public.mkdir(parents=True,exist_ok=True)
        records.write_json(public/'spatial_2048.json',records.public_summary(result),overwrite=True)
        plotted=[{'name':r['candidate'],'value':r['spatial_difference']['difference'],'standard_error':r['spatial_difference']['standard_error']}
                 for r in contrasts]
        analysis.plot_result({'results':plotted,'yield_label':'2048 minus nested 1024; weighted junction QPs / injected eV',
            'title':'Four matched layouts; equal per-position primaries; bars show standard errors'},repo/'figures/spatial_2048.png','summary')
        lines=['# Limited 2048-site verification','',
               'Six primary spatial contrasts use paired layout ratios with Bonferroni family error 0.05. The nested 1024 subset and full 2048 use the same per-position primaries.',
               '', '| Candidate | Spatial response change | Simultaneous interval | Equivalent within 5% |',
               '|---|---:|---:|---|']
        for row in contrasts:
            estimate=row['spatial_relative_change']
            if estimate:
                lo,hi=estimate['confidence_interval']
                lines.append(f"| {row['candidate']} | {100*estimate['difference']:+.2f}% | [{100*lo:+.2f}%, {100*hi:+.2f}%] | {row['equivalent_within_5_percent']} |")
            else: lines.append(f"| {row['candidate']} | undefined for zero reference | undefined | undefined |")
        lines+=['','Fixed-total-primary and half-primary-count comparisons are descriptive 95% intervals in the numerical record.',
                '',*['- '+text for text in cfg['limitations']],'','[Measurements, reference data and archives](spatial_2048.json).','']
        (public/'spatial_2048.md').write_text('\n'.join(lines))
        block='\n'.join(['<!-- spatial-start -->','## Limited 2,048-site verification','',
            'Finished '+result['date']+'. Four nested layouts verified six native material/rotation cases at equal total primary count.',
            '',*lines[4:6+len(contrasts)],'','![Spatial resolution contrasts](figures/spatial_2048.png)','',
            '[Spatial and transport comparisons, uncertainty and archives](docs/results/spatial_2048.md).','<!-- spatial-end -->'])
        readme=repo/'README.md';text=readme.read_text();start=text.index('<!-- spatial-start -->');end=text.index('<!-- spatial-end -->')+len('<!-- spatial-end -->')
        readme.write_text(text[:start]+block+text[end:])
        records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'updated':time.time()},overwrite=True)
        print(records.canonical_json({'state':'complete','studies':len(reports),'verified_cases':len(contrasts)}))
        raise SystemExit(0)
    if native_search:
        benchmarks=[reports[entry['id']] for entry in queue if entry['kind']=='focused']
        confirmed=layout_summary([reports[entry['id']] for entry in queue if entry['kind']=='native_confirmation'])
        bench=layout_summary(benchmarks); selection=records.load_document(run/'native-selected.json')
        aliases={name:names[0] for names in selection['aliases'].values() for name in names}
        values={name:next(row['estimate']['by_layout'] for row in confirmed['results'] if row['name']==target) for name,target in aliases.items()}
        comparisons={}; benchmark_pairs=[]; subset_checks={}
        for pair in cfg['comparison']['pairs']:
            a=next(row['estimate']['by_layout'] for row in bench['results'] if row['name']==pair['candidate'])
            b=next(row['estimate']['by_layout'] for row in bench['results'] if row['name']==pair['reference'])
            benchmark_pairs.append({**pair,'relative_change':analysis.compare_repeated(np.asarray(a)/b,np.ones(len(b)),paired=True,
                confidence=1-cfg['comparison']['family_error']/len(cfg['comparison']['pairs']))})
        for row in bench['results']:
            name=row['name']; fine=np.asarray(row['estimate']['by_layout'])
            half=np.array([next(r['balanced_half_value'] for r in report['results'] if r['name']==name) for report in benchmarks])
            subset_checks[name]=analysis.compare_repeated(half/fine,np.ones(len(fine)),paired=True,
                confidence=1-.05/len(cfg['candidates']))
        for material in cfg['materials']:
            methods={}; per_seed={}
            for method in cfg['methods']:
                rows=[]
                for seed in cfg['search_seeds']:
                    name=f'{method}-{material}-{seed}'; start=f'native-initial-{material}-{seed}'
                    a=np.asarray(values[name]); b=np.asarray(values[start]); report=reports[name]
                    gain=1-float(a.mean()/b.mean()) if b.mean()>0 else None
                    rows.append({'seed':seed,'value':float(a.mean()),'gain_from_initial':gain,'counts':report['search_counts'],
                        'paired_layout_difference':analysis.compare_repeated(a,b,paired=True),'timing':report['timing']})
                methods[method]={'by_seed':rows,'mean_value':float(np.mean([r['value'] for r in rows]))}
                per_seed[method]=[r['value'] for r in rows]
            keep=[i for i in range(len(cfg['search_seeds'])) if all(methods[m]['by_seed'][i]['counts']['target_reached'] for m in cfg['methods'])]
            paired=analysis.compare_repeated([per_seed['bo'][i] for i in keep],[per_seed['sobol'][i] for i in keep],paired=True,
                confidence=1-.05/len(cfg['materials'])) if len(keep)>=2 else {'status':'insufficient complete matched search seeds'}
            comparisons[material]={'methods':methods,'bo_minus_sobol':paired,'complete_matched_seeds':[cfg['search_seeds'][i] for i in keep]}
        result={'id':cfg['id'],'state':'complete','date':time.strftime('%Y-%m-%d'),'objective':defaults['objective'],
                'benchmarks':bench,'benchmark_contrasts':benchmark_pairs,'balanced_half_checks':subset_checks,
                'confirmation':confirmed,'method_comparison':comparisons,'selection':selection,'studies':reports,
                'source':records.load_document(run/'source-ready.json'),'resources':limits,'limitations':cfg['limitations']}
        publication=records.load_document(run/'publication.json')
        if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
            raise ValueError('publication branch or remote changed; retained results were not published')
        public=repo/'docs/results';public.mkdir(parents=True,exist_ok=True)
        records.write_json(public/'native_optimization.json',records.public_summary(result),overwrite=True)
        plotted=[{'name':material+': '+method,'value':data['mean_value'],
                  'standard_error':float(np.std([r['value'] for r in data['by_seed']],ddof=1)/math.sqrt(len(data['by_seed'])))}
                 for material,comparison in comparisons.items() for method,data in comparison['methods'].items()]
        analysis.plot_result({'results':plotted,'objective':defaults['objective'],'title':'Confirmed native orientation search; errors across search seeds'},
                             repo/'figures/native_optimization.png','summary')
        lines=['# Native-material orientation search','','| Material | Method | Confirmed J | Mean added reduction |','|---|---|---:|---:|']
        for material,comparison in comparisons.items():
            for method,data in comparison['methods'].items():
                gains=[r['gain_from_initial'] for r in data['by_seed']]
                gain=f'{100*np.mean(gains):.2f}%' if all(g is not None for g in gains) else 'undefined for zero reference'
                lines.append(f"| {material} | {method} | {data['mean_value']:.7g} | {gain} |")
        lines+=['',*['- '+text for text in cfg['limitations']],'','[Layouts, uncertainty, subset checks and archives](native_optimization.json).','']
        (public/'native_optimization.md').write_text('\n'.join(lines))
        block='\n'.join(['<!-- native-start -->','## Native-material orientation optimization','',
            'Finished '+result['date']+'. Density, elasticity and phonon rates remained coupled to each native material.',
            '',*lines[2:5+len(plotted)],'','![Confirmed orientation search](figures/native_optimization.png)','',
            '[Measured results and scientific limits](docs/results/native_optimization.md).','<!-- native-end -->'])
        readme=repo/'README.md';text=readme.read_text();start=text.index('<!-- native-start -->');end=text.index('<!-- native-end -->')+len('<!-- native-end -->')
        readme.write_text(text[:start]+block+text[end:])
        records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'updated':time.time()},overwrite=True)
        print(records.canonical_json({'state':'complete','studies':len(reports),'confirmed_targets':len(confirmed['results'])}))
        raise SystemExit(0)
    if focused:
        # Layout means are the independent observations; all comparisons were fixed before transport.
        aggregate=layout_summary([reports[step['id']] for step in queue])
        values={row['name']:np.asarray(row['estimate']['by_layout']) for row in aggregate['results']}
        contrasts=[]; confidence=1-cfg['comparison']['family_error']/len(cfg['comparison']['pairs'])
        for pair in cfg['comparison']['pairs']:
            a,b=values[pair['candidate']],values[pair['reference']]
            if not np.all(b>0): raise ValueError('relative comparisons require positive reference responses')
            delta=analysis.compare_repeated(a,b,paired=True,confidence=confidence)
            relative=analysis.compare_repeated(a/b,np.ones(len(b)),paired=True,confidence=confidence)
            contrasts.append({**pair,'difference':delta,'relative_change':relative,
                'meaningful_reduction_confirmed':relative['confidence_interval'][1]<-cfg['comparison']['meaningful_reduction']})
        result={'id':cfg['id'],'state':'complete','date':time.strftime('%Y-%m-%d'),'objective':defaults['objective'],
                'question':cfg['question'],'comparison':cfg['comparison'],'simultaneous_confidence':confidence,
                'results':aggregate['results'],'contrasts':contrasts,'studies':reports,'resources':limits,
                'source':records.load_document(run/'source-ready.json'),'limitations':cfg['limitations']}
        publication=records.load_document(run/'publication.json')
        if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
            raise ValueError('publication branch or remote changed; retained results were not published')
        public=repo/'docs/results'; public.mkdir(parents=True,exist_ok=True)
        records.write_json(public/'orientation_materials.json',result,overwrite=True)
        plotted=[{'name':row['candidate']+' / '+row['reference'],
                  'value':row['relative_change']['difference'],'standard_error':row['relative_change']['standard_error']}
                 for row in contrasts]
        analysis.plot_result({'results':plotted,'yield_label':'fractional response change; negative means lower',
                              'title':'Eight independent layouts; bars show standard errors'},repo/'figures/orientation_materials.png','summary')
        lines=['# Matched rotation and native-material comparison','',
               'Three comparisons were fixed before transport. Intervals use paired layout means and Bonferroni family error 0.05.',
               '', '| Candidate / reference | Response change | Simultaneous interval | At least 5% reduction established |',
               '|---|---:|---:|---|']
        for row in contrasts:
            estimate=row['relative_change']; lo,hi=estimate['confidence_interval']
            lines.append(f"| {row['candidate']} / {row['reference']} | {100*estimate['difference']:+.2f}% | [{100*lo:+.2f}%, {100*hi:+.2f}%] | {row['meaningful_reduction_confirmed']} |")
        lines+=['',*['- '+text for text in cfg['limitations']],'','[Measured values, layouts and archives](orientation_materials.json).','']
        (public/'orientation_materials.md').write_text('\n'.join(lines))
        block=['<!-- focused-start -->','## Matched rotation and native-material comparison','',
               'Finished '+result['date']+'. Eight unused 1,024-position layouts compare 0° with 45° at identical properties, and Ge/Nb/Cu and GaAs/Nb/Cu with Si/Nb/Cu.',
               '', '\n'.join(lines[4:6+len(contrasts)]), '',
               '![Matched rotation and native-material responses](figures/orientation_materials.png)', '',
               '[Paired intervals, physical inputs and limits](docs/results/orientation_materials.md).','<!-- focused-end -->']
        readme=repo/'README.md'; text=readme.read_text()
        start=text.index('<!-- focused-start -->'); end=text.index('<!-- focused-end -->')+len('<!-- focused-end -->')
        readme.write_text(text[:start]+'\n'.join(block)+text[end:])
        records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'updated':time.time()},overwrite=True)
        print(records.canonical_json({'state':'complete','studies':len(reports),'contrasts':len(contrasts)}))
        raise SystemExit(0)
    if optimization:
        selection=records.load_document(run/'selected-leaders.json')
        confirmed=[reports[f'optimization-confirmation-{k:02d}'] for k in range(1,5)]
        aggregate=layout_summary(confirmed)
        alias={name:point['name'] for key,names in selection['aliases'].items()
               for point in selection['candidates'] if point['name']==names[0] for name in names}
        lookup={row['name']:row for row in aggregate['results']}
        values={name:lookup[target]['estimate']['by_layout'] for name,target in alias.items()}
        comparisons={}
        for method in cfg['methods']:
            by_seed=[]
            for seed in cfg['search_seeds']:
                measured=np.asarray(values[f'{method}-{seed}'])
                initial=np.asarray(values[f'initial-{seed}']); sobol=np.asarray(values[f'sobol-{seed}'])
                by_seed.append({'seed':seed,'value':float(measured.mean()),'position_uncertainty':mean_interval(measured),
                    'gain_from_initial':float(1-measured.mean()/initial.mean()) if initial.mean()>0 else None,
                    'gain_from_sobol':float(1-measured.mean()/sobol.mean()) if sobol.mean()>0 else None,
                    'by_layout':measured.tolist(),'initial_by_layout':initial.tolist(),'sobol_by_layout':sobol.tolist(),
                    'paired_layout_difference_from_initial':analysis.compare_repeated(measured,initial,paired=True),
                    'paired_layout_difference_from_sobol':analysis.compare_repeated(measured,sobol,paired=True)})
            comparisons[method]={'by_seed':by_seed,'mean_value':float(np.mean([row['value'] for row in by_seed])),
                'search_seed_variation':mean_interval([row['value'] for row in by_seed],replicates='search seeds'),
                'gain_from_initial':mean_interval([row['gain_from_initial'] for row in by_seed],replicates='search seeds') if all(row['gain_from_initial'] is not None for row in by_seed) else None,
                'gain_from_sobol':mean_interval([row['gain_from_sobol'] for row in by_seed],replicates='search seeds') if all(row['gain_from_sobol'] is not None for row in by_seed) else None}
        contrasts={}
        pairs=[('bo_improved','bo'),('llm_proposals','bo_improved'),('llm_tools','llm_proposals')]
        pairs.extend((words[0],words[2]) for text in refinement.get('scientific_limits',{}).get('primary_contrasts',[]) if len(words:=text.split())==3)
        for a,b in pairs:
            contrasts[a+' versus '+b]=analysis.compare_repeated([row['value'] for row in comparisons[a]['by_seed']],
                [row['value'] for row in comparisons[b]['by_seed']],paired=True)
        result={'run_id':cfg['id'],'date':time.strftime('%Y-%m-%d'),'state':'complete','objective':defaults['objective'],
            'resources':limits,'background':records.load_document(run/'calibration-background.json'),
            'gpu_benchmark':records.load_document(run/'gpu_benchmark.json'),'selection':selection,
            'saved_output_analysis':records.load_document(run/'optimization_spatial_subsets.json') if (run/'optimization_spatial_subsets.json').is_file() else None,
            'method_comparison':comparisons,'primary_contrasts':contrasts,'confirmation':aggregate,'studies':reports,
            'limitations':['Three independent search seeds give exploratory evidence; position layouts are not extra optimizer repetitions.',
                'Source designs remain at most 1024 sites; this cap does not prove spatial convergence.',
                'Confirmed properties are model targets, not identified new materials. Calibrated interfaces require independent measurements.',
                'Incomplete search arms and failed attempts remain in the comparison; absent responses are never zero.',
                'Across-seed intervals condition on the four measured layouts; paired layout intervals separately describe position uncertainty. Neither supplies an exact combined superiority test.']}
        publication=records.load_document(run/'publication.json')
        if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
            raise ValueError('publication branch or remote changed; retained results were not published')
        public=repo/'docs/results'; public.mkdir(parents=True,exist_ok=True)
        records.write_json(public/'optimization_summary.json',result,overwrite=True)
        plotted=[{'name':method,'value':row['mean_value'],'standard_error':row['search_seed_variation']['standard_error'],'units':'QPs/injected-eV'} for method,row in comparisons.items()]
        analysis.plot_result({'objective':defaults['objective'],'results':plotted,'title':'Independent confirmation; mean of three search seeds'},repo/'figures/optimization_results.png','summary')
        lines=['# Optimization comparison','',
            '| Method | Confirmed QPs/injected-eV | Additional reduction from initial best | Additional reduction from Sobol |',
            '|---|---:|---:|---:|']
        for method,row in comparisons.items():
            gains=[point['gain_from_initial'] for point in row['by_seed']]
            advantages=[point['gain_from_sobol'] for point in row['by_seed']]
            gain=f'{100*np.mean(gains):.3g}%' if all(value is not None for value in gains) else 'undefined: zero initial response'
            advantage=f'{100*np.mean(advantages):.3g}%' if all(value is not None for value in advantages) else 'undefined: zero Sobol response'
            lines.append(f"| {method} | {row['mean_value']:.6g} | {gain} | {advantage} |")
        lines += ['', 'Each gain uses independent confirmation of that seed’s leader and common initial winner. Shared positions and streams are retained across targets.',
                  '',*[f'- {text}' for text in result['limitations']], '', '[Numerical results, per-seed values and verified archives](optimization_summary.json).','']
        (public/'optimization_summary.md').write_text('\n'.join(lines))
        block=['<!-- optimization-start -->','## Optimization comparison','',
            f"The {len(cfg['methods'])}-method comparison finished on {result['date']}. Three search seeds used common measured starts; selected points were confirmed on four unused 1024-site layouts.",
            '', '![Confirmed optimization responses](figures/optimization_results.png)', '',
            '[Additional gains, uncertainties, failed attempts and model identities](docs/results/optimization_summary.md).','<!-- optimization-end -->']
        readme=repo/'README.md'; text=readme.read_text()
        if '<!-- optimization-start -->' in text:
            start=text.index('<!-- optimization-start -->'); end=text.index('<!-- optimization-end -->')+len('<!-- optimization-end -->')
            text=text[:start]+'\n'.join(block)+text[end:]
        else: text+='\n\n'+'\n'.join(block)+'\n'
        readme.write_text(text)
        records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'incomplete_searches':[name for name,report in reports.items() if not report['complete']], 'updated':time.time()},overwrite=True)
        print(records.canonical_json({'state':'complete','studies':len(reports),'confirmed_targets':len(aggregate['results'])}))
        raise SystemExit(0)
    if initial_checks:
        validation=records.load_document(run/'source-validation.json')
        aggregate=validation['independent_layouts']
        event_counts=next(group['events_per_task'] for group in cfg['studies'] if group['kind']=='event_count_convergence')
        final_events=reports['events-'+str(event_counts[-1])]
        checks={'source_spatial':validation['passed'],'event_count':final_events['event_count_check']['passed']}
        result={'date':time.strftime('%Y-%m-%d'),'run_id':cfg['id'],'state':'awaiting_review',
                'max_sites':max_sites,'temperature_K':defaults['temperature_K'],'objective':defaults['objective'],
                'resources':limits,'source':records.load_document(run/'source-ready.json'),
                'base_design':records.load_document(run/'base-design.json'),'scientific_checks':checks,
                'position_validation':validation,'studies':reports,
                'limitations':['The site limit is a chosen stopping point, not evidence of spatial convergence.',
                    'Nested contrasts and four independent layouts assess the tested source designs; area weights remain fixed.',
                    'Event-count precision is conditional on fixed source positions.',
                    'SiC-derived rates are model assumptions; no material ranking or optimizer result is produced.'],
                'next_steps':['Review regional changes, independent-layout errors and primary-count errors before choosing further simulations.',
                              'Keep any subsequent source designs at or below the declared site limit unless explicitly changed.']}
        publication=records.load_document(run/'publication.json')
        if subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()!=publication['branch'] or subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip()!=publication['remote']:
            raise ValueError('publication branch or remote changed; retained results were not published')
        public=repo/'docs/results'; public.mkdir(parents=True,exist_ok=True)
        records.write_json(public/'initial_checks_summary.json',result,overwrite=True)
        plotted=[]
        for identifier,report in reports.items():
            if not identifier.startswith('source-') or identifier.startswith('source-independent-'): continue
            plotted.extend({**row,'name':identifier+': '+row['name']} for row in report['results'])
        plotted.extend({**row,'name':'four independent layouts: '+row['name']} for row in aggregate['results'])
        analysis.plot_result({'objective':defaults['objective'],'results':plotted,
                              'title':'Source-position checks; error bars are standard errors'},repo/'figures/initial_source_checks.png','summary')
        lines=['# Initial source and primary-count checks','',
               f"Maximum source sites: {max_sites}. State: awaiting review. All {len(reports)} measured studies and two-copy archive checks finished.",'',
               f"Spatial criteria passed: {checks['source_spatial']}. Final primary-count criteria passed: {checks['event_count']}.",'',
               '| Independent-layout mean | QPs / injected eV | Standard error | Relative standard error |',
               '|---|---:|---:|---:|']
        lines += [f"| {row['name']} | {row['value']:.6g} | {row['standard_error']:.3g} | {100*row['estimate']['relative_standard_error']:.3g}% |" for row in aggregate['results']]
        lines += ['','Four position-layout means give the reported Student-t interval and standard error. Fixed-layout event-count uncertainty is stored separately.',
                  '',*[f'- {text}' for text in result['limitations']],
                  '', '[Numerical results and archive checksums](initial_checks_summary.json).','']
        (public/'initial_checks_summary.md').write_text('\n'.join(lines))
        block=['<!-- initial-checks-start -->','## Source and primary-count checks','',
               f"The {max_sites}-site diagnostic queue finished on {result['date']} and is waiting for review. Spatial criteria passed: `{checks['source_spatial']}`; final primary-count criteria passed: `{checks['event_count']}`. No optimization or larger source design starts automatically.",'',
               'The figure compares measured source layouts. Error bars are standard errors; the four-layout means include variation between independent source-position layouts. A chosen site limit does not establish convergence.',
               '', '![Initial source-position checks](figures/initial_source_checks.png)', '',
               '[Measured results, uncertainty definitions and archive checks](docs/results/initial_checks_summary.md).','<!-- initial-checks-end -->']
        readme=repo/'README.md'; text=readme.read_text()
        if '<!-- initial-checks-start -->' in text:
            start=text.index('<!-- initial-checks-start -->'); end=text.index('<!-- initial-checks-end -->')+len('<!-- initial-checks-end -->')
            text=text[:start]+'\n'.join(block)+text[end:]
        else: text+='\n\n'+'\n'.join(block)+'\n'
        readme.write_text(text)
        records.write_json(run/'queue_status.json',{'state':'awaiting_review','studies':len(reports),
                           'max_sites':max_sites,'scientific_checks':checks,'updated':time.time()},overwrite=True)
        print(records.canonical_json({'state':'awaiting_review','studies':len(reports),'scientific_checks':checks}))
        raise SystemExit(0)
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
    figure_dir=repo/'figures'; figure_dir.mkdir(parents=True,exist_ok=True)
    records.write_json(public/'full_scale_summary.json',result,overwrite=True)
    analysis.plot_result(confirmation,figure_dir/'full_scale_finalists.png','summary')
    analysis.plot_result(reports['material-transport-morris'],figure_dir/'full_scale_transport_morris.png','sensitivity')
    analysis.plot_result(reports['density-morris'],figure_dir/'full_scale_density_morris.png','sensitivity')
    analysis.plot_result({'results':[{'name':p['name'],'value':lookup[p['name']]['value'],'standard_error':lookup[p['name']]['standard_error'],'units':reference['units']} for p in finalists]},figure_dir/'full_scale_methods.png','summary')
    analysis.plot_result(result['projection'],figure_dir/'full_scale_projection.png','projection')
    block=['<!-- full-scale-start -->','## Fresh full-scale measurements','',f"Finished {result['date']} at 20 mK with the Nb gap restricted to at least 1.5384 meV. All declared simulations and archive checks finished. Convergence checks: `{result['scientific_checks']}`.",'',f"GP and CMA-ES each obtained 120 successful measurements for each of three optimizer seeds. Finalists were confirmed on four independent layouts of {confirmation['sites']} spatial sites, each with {confirmation['replicas']} realizations on unused random streams. Algorithm starts differ.",'','| Confirmed target | Relative QP yield change | Paired standard error (percentage points) |','|---|---:|---:|']
    block += [f"| {row['name']} | {100*row['relative_to_reference']:+.2f}% | {100*row['paired_difference_from_reference']['relative_change']['standard_error']:.3g} |" for row in result['confirmed_finalists']]
    block += ['','[Numerical records and archive checksums](docs/results/full_scale_summary.json).','', '![Confirmed targets](figures/full_scale_finalists.png)', '', '![Transport sensitivity](figures/full_scale_transport_morris.png)', '', '![Density sensitivity](figures/full_scale_density_morris.png)', '', 'A property target is not a fabricated material. SiC phonon rates remain assumed, and the interface model remains calibrated. Spatial and event-count checks are reported separately; unresolved optimizer differences remain unresolved.','<!-- full-scale-end -->']
    readme=repo/'README.md'; text=readme.read_text(); start=text.index('<!-- full-scale-start -->'); end=text.index('<!-- full-scale-end -->')+len('<!-- full-scale-end -->')
    readme.write_text(text[:start]+'\n'.join(block)+text[end:])
    if (run/'references').exists(): shutil.rmtree(run/'references')
    records.write_json(run/'queue_status.json',{'state':'complete','studies':len(reports),'updated':time.time()},overwrite=True)
    print(records.canonical_json({'state':'complete','studies':len(reports),'method_comparison':comparison}))
else:
    raise ValueError('choose check, prepare, run, status, stop, stage, record, finish or publish')
PY
