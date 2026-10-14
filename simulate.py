"""Render physical inputs and run one witnessed Geant4 measurement."""

import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import threading
import time

import records


def prepare_environment(installation):
    scripts=[str(Path(p).expanduser().resolve()) for p in installation.get('environment_scripts',[])]
    if any(not Path(p).is_file() for p in scripts):
        raise ValueError('an installation environment script is missing')
    if scripts:
        command='set -e; set +u; for source_file in "$@"; do source "$source_file" >&2; done; env -0'
        completed=subprocess.run(['bash','--noprofile','--norc','-c',command,'scan-env',*scripts],capture_output=True,check=True)
        env=dict(item.split('=',1) for item in completed.stdout.decode().split('\0') if '=' in item)
    else:
        env=dict(os.environ)
    library=installation.get('library_dir')
    if library:
        env['LD_LIBRARY_PATH']=str(library)+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        env[key]='1'
    executable=Path(installation['executable'])
    if not executable.is_file() or not os.access(executable,os.X_OK):
        raise ValueError(f'executable unavailable: {executable}')
    libraries={}
    if os.name=='posix':
        output=subprocess.run(['ldd',str(executable)],env=env,capture_output=True,text=True,check=False).stdout
        if 'not found' in output:
            raise ValueError('a simulator shared library is unavailable')
        for path in re.findall(r'(/\S+)\s+\(',output):
            if Path(path).is_file(): libraries[path]=records.sha256_file(path)
    identity={'executable':str(executable.resolve()),'executable_sha256':records.sha256_file(executable),
              'libraries':libraries,'scripts':{p:records.sha256_file(p) for p in scripts},
              'template_sha256':records.sha256_file(installation['macro_template']),
              'data_paths':{k:v for k,v in env.items() if k.startswith('G4') and ('DATA' in k or 'LIB' in k)},
              'renderer_sha256':records.sha256_file(__file__)}
    return env,identity


def replace_commands(text, settings, *, require=True):
    lines=text.splitlines()
    for command,value in settings.items():
        matches=[i for i,line in enumerate(lines) if line.strip().split(' ',1)[0]==command]
        if not matches:
            matches=[i for i,line in enumerate(lines) if line.strip().lstrip('#').strip().split(' ',1)[0]==command]
        if not matches:
            if require: raise ValueError(f'missing required command {command}')
            lines.append(f'{command} {value}')
        else:
            lines[matches[0]]=f'{command} {value}'
            for i in matches[1:]: lines[i]='# '+lines[i]
    return '\n'.join(lines)+'\n'


def validate_macro(text):
    commands=[row.split() for row in text.splitlines() if row.strip().startswith('/')]
    if sum(row[0]=='/run/beamOn' for row in commands)!=1:
        raise ValueError('exactly one beamOn command is required')
    for tokens in commands:
        if tokens[0] in ('/control/shell','/control/execute','/g4cmp/HitsFile',
                         '/main/detector_param/setSubstrateName','/main/detector_param/setSubstrateG4Name',
                         '/main/gun/setGunType','/main/sensor/setHitType') or tokens[0].endswith('SourceMat'): continue
        for value in tokens[1:]:
            value=value.rstrip(',')
            if value and value[0] in '+-.0123456789':
                try: float(value)
                except ValueError as exc: raise ValueError(f'malformed macro number: {value}') from exc
    beam=next(i for i,row in enumerate(commands) if row[0]=='/run/beamOn')
    if beam==0 or commands[beam-1][0]!='/random/setSeeds' or beam+1==len(commands) or commands[beam+1][0]!='/control/shell':
        raise ValueError('random seeds and completion marker must surround beamOn')


def render_inputs(prepared, task, installation, directory):
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=False)
    derived=prepared['derived']; point=prepared['values']; fixed=prepared['fixed']
    top,bottom=prepared['top_film'],prepared['bottom_film']
    lattice_dir=directory/'CrystalMaps'/derived['lattice_map_name']; lattice_dir.mkdir(parents=True)
    text=Path(prepared['source_config']).read_text()
    settings={'vsound':f"{derived['vsound_m_s']:.12g} m/s",'vtrans':f"{derived['vtrans_m_s']:.12g} m/s"}
    if derived['config_mode']=='pseudo_si_base':
        for key,name,unit in [('cubic','sub_lattice_a','Ang'),('stiffness 1 1','sub_c11','GPa'),('stiffness 1 2','sub_c12','GPa'),
                             ('stiffness 4 4','sub_c44','GPa'),('scat','sub_scat','s3'),('decay','sub_decay','s4'),('decayTT','sub_decayTT','')]:
            settings[key]=f'{point[name]:.12g} {unit}'.strip()
    for key,(value,unit) in prepared.get('config',{}).items(): settings[key]=f'{value:.12g} {unit}'.strip()
    for key,value in settings.items():
        parts=text.splitlines(); indexes=[i for i,row in enumerate(parts) if row.strip().startswith(key+' ')]
        if len(indexes)>1: raise ValueError(f'duplicate lattice field {key}')
        if indexes: parts[indexes[0]]=f'{key} {value}'
        else: parts.append(f'{key} {value}')
        text='\n'.join(parts)+'\n'
    (lattice_dir/'config.txt').write_text(text)
    output=directory/'hits.csv'; witness=directory/'complete'; macro=directory/'run.mac'; log=directory/'run.log'
    commands={'/main/detector_param/setSubstrateG4Name':derived['g4_material_name'],
              '/main/detector_param/setSubstrateName':derived['lattice_map_name'],
              '/main/gun/setEnergy':f"{fixed['gun_energy_eV']} eV",'/g4cmp/minEPhonons':f"{fixed['min_e_phonons_eV']} eV",
              '/main/detector_param/setMiller':' '.join(map(str,derived.get('miller',point.get('miller',[0,0,1])))),
              '/main/detector_param/setLatticeDeg':point.get('lattice_deg',45.),
              '/g4cmp/temperature':f"{point.get('temperature',fixed.get('temperature_K',0.))} K"}
    if fixed.get('gun_type','phonon_Caustic')!='phonon_Caustic':
        raise ValueError('this phonon study requires gun_type=phonon_Caustic')
    for key in ('setTopAbs','setTopFilmAbs','setBotAbs'): commands['/main/detector_param/'+key]=derived[key]
    for key in ('setTopVSound','setTopGap','setTopQPLim','setTopPhLifetime','setTopPhLifetimeSlope',
                'setWallAbs','setTopPSpecProb','setTopFilmPSpecProb','setBotPSpecProb','setWallPSpecProb'):
        commands['/main/detector_param/'+key]=fixed[key]
    for key,unit in (('setTopThickness_um','um'),('setSubWidth_mm',''),('setSubHeight_mm',''),('setSubThickness_um','')):
        commands['/main/detector_param/'+key.rsplit('_',1)[0]]=f'{fixed[key]} {unit}'.strip()
    for prefix,film in (('setTopFilm',top),('setBot',bottom)):
        for suffix,key in [('SourceMat','g4_material'),('VSound','vsound_km_s'),('Gap','gap_eV'),('QPLim','qp_limit'),('PhLifetime','ph_lifetime_ns')]:
            commands['/main/detector_param/'+prefix+suffix]=film[key]
        commands['/main/detector_param/'+prefix+'Thickness']=f"{film['thickness_um']} um"
    commands['/main/detector_param/setTopFilmPhLifetimeSlope']=top.get('ph_lifetime_slope',.29)
    commands['/main/detector_param/setBotGapThres']=bottom['gap_threshold_eV']
    for key in ('chargeBounces','phononBounces'): commands['/g4cmp/'+key]=fixed[key]
    for key in ('clearance_mm','eTrappingMFP_mm','hTrappingMFP_mm'):
        commands['/g4cmp/'+key.rsplit('_',1)[0]]=f'{fixed[key]} mm'
    for suffix,key in [('Width','electrode_width_um'),('Height','electrode_height_um'),('Island','electrode_island_um'),('IslandSpacing','electrode_island_spacing_um')]:
        commands['/main/electrode_param/set'+suffix]=str(fixed[key])+(' um' if suffix.startswith('Island') else '')
    for suffix,key in [('XLocations','electrode_x_mm'),('YLocations','electrode_y_mm')]:
        commands['/main/electrode_param/set'+suffix]=', '.join(f'{v:.6f}' for v in fixed[key])
    commands.update(prepared.get('macro',{}))
    commands.update({'/g4cmp/HitsFile':str(output),'/main/gun/setPosition':' '.join(map(str,task['site_mm']))+' mm'})
    text=replace_commands(Path(installation['macro_template']).read_text(),commands)
    block=f"/random/setSeeds {task['seeds'][0]} {task['seeds'][1]}\n/run/beamOn {task['events']}\n/control/shell touch {shlex.quote(str(witness))}"
    text=replace_commands(text,{'/run/beamOn':str(task['events'])})
    text=text.replace('/run/beamOn '+str(task['events']),block)
    validate_macro(text); macro.write_text(text)
    return {'directory':str(directory),'macro':str(macro),'output':str(output),'witness':str(witness),'log':str(log),
            'lattice_root':str(directory/'CrystalMaps'),'expected_material':derived['g4_material_name'],
            'expected_density_kg_m3':derived['substrate_density_kg_m3'],
            'density_probe':derived.get('density_probe',False),
            'input_sha256':{'macro':records.sha256_file(macro),'lattice':records.sha256_file(lattice_dir/'config.txt')}}


def process_stats(pid):
    if not Path('/proc/self/stat').exists(): return None
    try:
        if int(Path('/proc/self/stat').read_text().split(' ',1)[0])!=os.getpid(): return None
        queue=[pid]; ticks=0; memory=0
        while queue:
            current=queue.pop()
            fields=Path(f'/proc/{current}/stat').read_text().rsplit(')',1)[1].split()
            ticks+=int(fields[11])+int(fields[12]); memory+=int(fields[21])*os.sysconf('SC_PAGE_SIZE')
            child=Path(f'/proc/{current}/task/{current}/children')
            if child.exists(): queue.extend(map(int,child.read_text().split()))
        return ticks,memory
    except (OSError,ValueError,IndexError): return None


def stop_process(process):
    if process.poll() is not None: return
    try: os.killpg(process.pid,signal.SIGKILL)
    except (ProcessLookupError,PermissionError): process.kill()


def monitor_processes(active,lock,limits):
    now=time.monotonic()
    with lock:
        rows=list(active.values())
        total=0
        for row in rows:
            stats=process_stats(row['process'].pid); size=sum(Path(row[k]).stat().st_size if Path(row[k]).exists() else 0 for k in ('log','output'))
            if stats:
                total+=stats[1]; row['peak_rss_bytes']=max(row.get('peak_rss_bytes',0),stats[1])
                progress=(stats[0],size)
                if progress!=row.get('progress'): row.update(progress=progress,last_change=now)
            reason=None
            if limits.get('timeout_s') and now-row['started']>limits['timeout_s']: reason='timeout'
            elif stats and limits.get('stall_timeout_s') and now-row.get('last_change',now)>limits['stall_timeout_s']: reason='stalled'
            elif stats and stats[1]>limits.get('per_task_mem_gb',float('inf'))*1024**3: reason='memory_limit'
            if reason:
                row['stopped_reason']=reason; stop_process(row['process'])
        if total>limits.get('total_mem_gb',float('inf'))*1024**3:
            for row in sorted(rows,key=lambda r:r.get('peak_rss_bytes',0),reverse=True):
                row['stopped_reason']='total_memory_limit'; stop_process(row['process']); break


def verify_output(paths, returncode):
    if not Path(paths['witness']).is_file(): raise ValueError('missing post-beamOn completion marker')
    if returncode not in (0,139,-signal.SIGSEGV): raise ValueError(f'simulator exit status {returncode}')
    path=Path(paths['output'])
    if not path.is_file() or not path.stat().st_size: raise ValueError('missing or zero-byte hit file')
    with path.open() as handle:
        header=handle.readline()
    if not all(name in header for name in ('Energy Deposited [eV]','End X [m]','End Y [m]','End Z [m]','Final Time [ns]')):
        raise ValueError('hit file lacks required columns')
    text=Path(paths['log']).read_text(errors='replace')
    match=re.search(r'Material:\s+(\S+).*?density:\s+([\d.eE+-]+)',text)
    if not match: raise ValueError('missing runtime material and density')
    if float(match[2])<=0 or match[1]!=paths['expected_material'] or (not paths.get('density_probe') and abs(float(match[2])*1000/paths['expected_density_kg_m3']-1)>.03):
        raise ValueError('runtime material or density differs from physical input')
    return {'output':str(path),'sha256':records.sha256_file(path),'bytes':path.stat().st_size,
            'runtime_material':match[1],'runtime_density_kg_m3':float(match[2])*1000,
            'teardown_fault':returncode!=0}


def run_task(task, prepared, installation, environment, directory, active=None, lock=None):
    paths=render_inputs(prepared,task,installation,directory)
    env=dict(environment); env['G4LATTICEDATA']=paths['lattice_root']
    active={} if active is None else active; lock=lock or threading.Lock()
    started=time.monotonic(); row={**paths,'started':started,'last_change':started}
    with open(paths['log'],'xb') as log:
        process=subprocess.Popen([installation['executable'],paths['macro']],cwd=paths['directory'],env=env,
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        row['process']=process
        with lock: active[task['task_id']]=row
        try: returncode=process.wait()
        finally:
            with lock: active.pop(task['task_id'],None)
    elapsed=time.monotonic()-started
    if row.get('stopped_reason'):
        return {'status':'timeout' if row['stopped_reason'] in ('timeout','stalled') else 'failed','reason':row['stopped_reason'],
                'elapsed_s':elapsed,'paths':paths,'returncode':returncode}
    try: result=verify_output(paths,returncode)
    except (ValueError,OSError) as exc:
        return {'status':'invalid','reason':str(exc),'elapsed_s':elapsed,'paths':paths,'returncode':returncode}
    return {'status':'complete','elapsed_s':elapsed,'returncode':returncode,'peak_rss_bytes':row.get('peak_rss_bytes'),
            'paths':paths,**result}


def normalized_commands(path):
    output=[]
    for row in Path(path).read_text().splitlines():
        row=row.split('#',1)[0].strip()
        if not row or row.startswith('/control/shell') or row.startswith('/g4cmp/HitsFile'): continue
        tokens=row.split(); values=[]
        for token in tokens:
            try: values.append(float(token.rstrip(',')))
            except ValueError: values.append(token)
        output.append(values)
    return output


def probe_density(material, study, output):
    """Read a named Geant4 density; this diagnostic does not model that compound."""
    from concurrent.futures import ThreadPoolExecutor
    import inputs
    if not re.fullmatch(r'G4_[A-Za-z0-9_]+',material):
        raise ValueError('expected one named Geant4 material')
    root=Path(output).resolve(); root.mkdir(parents=True,exist_ok=True)
    prepared=inputs.prepare_values(study)
    prepared['derived'].update(g4_material_name=material,density_probe=True)
    environment,build=prepare_environment(study['installation'])
    task={'task_id':'density-probe','events':1,'seeds':[41,42],'site_mm':study['design']['sites_mm'][0]}
    active={}; lock=threading.Lock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run_task,task,prepared,study['installation'],environment,root/'attempt',active,lock)
        while not future.done():
            monitor_processes(active,lock,{'timeout_s':60,'stall_timeout_s':30,'per_task_mem_gb':2,'total_mem_gb':2})
            time.sleep(.1)
        result=future.result()
    if result['status']!='complete':
        raise RuntimeError('density probe failed: '+result.get('reason','unknown'))
    report={'material':material,'density_kg_m3':result['runtime_density_kg_m3'],'build':build,
            'log':result['paths']['log'],'scope':'Density measurement only; the Si lattice is a diagnostic placeholder.'}
    records.write_json(root/'density.json',report)
    return report
