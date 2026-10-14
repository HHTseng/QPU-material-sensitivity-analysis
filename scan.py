"""Material studies: prepare, simulate, count quasiparticles, compare and record."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import inspect
import itertools
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np

import analysis
import inputs
import physics
import records
import search
import simulate


def analysis_design(study):
    design=study['design']; sampling=study['sampling']
    return {'position_strata':design['stratum'],'stratum_weights':design['stratum_weights'],
            'replica_ids':list(range(sampling['replicas'])),'events_per_task':sampling['events_per_task'],
            'gun_energy_eV':study['fixed']['gun_energy_eV'],'electrode_weights':design['electrode_weights'],
            'design_hash':design['design_hash'],'injection_law':design.get('injection_law','uniform_surface')}


def score_output(result, prepared, task, *, time_resolved=False):
    fixed=prepared['fixed']; totals=np.zeros(len(fixed['electrode_x_mm'])); n_hits=0; matrix=None
    surface=fixed['setSubThickness_um']*1e-6/2
    gap=fixed['setTopGap']
    for energy,x,y,z,arrival in records.read_hits(result['output']):
        count=physics.qp_counts(energy,x,y,z,arrival,gap,fixed['electrode_x_mm'],fixed['electrode_y_mm'],surface,time_resolved=time_resolved)
        if time_resolved:
            times,qps=count
            matrix=qps if matrix is None else matrix+qps
            totals+=qps.sum(axis=1)
        else: totals+=count
        n_hits+=len(energy)
    block={'position':task['position'],'replica':task['replica'],'events':task['events'],'total_qps':float(totals.sum()),
           'per_electrode_qps':totals.tolist(),'n_hits':n_hits}
    if time_resolved:
        if matrix is None:
            times=np.linspace(0,300000,1001); matrix=np.zeros((len(totals),1001))
        destination=Path(result['output']).with_name('qps.npz')
        np.savez_compressed(destination,time_ns=times,qps=matrix)
        block['time_data']=str(destination)
        block['time_data_sha256']=records.sha256_file(destination)
    block['score_hash']=records.content_hash({'physics':records.sha256_file(physics.__file__),
                                              'calculation':inspect.getsource(score_output)})
    return {**result,**block}


def evaluate(study, candidate, results, context):
    return evaluate_points(study,[candidate],results,context)[0]


def evaluate_points(study, candidates, results, context):
    started=time.perf_counter(); prepared_jobs=[]; futures={}; rows=[]
    design=analysis_design(study)
    needs_time=study['analysis'].get('time_resolved',False) or study['kind']=='sensitivity' or study['analysis']['objective']=='integrated_density'
    pool=ThreadPoolExecutor(max_workers=study['execution']['workers'])
    try:
        for candidate in candidates:
            prepared=inputs.prepare_values(study,candidate)
            prepared.update({key:candidate[key] for key in ('macro','config','ode','trajectory') if key in candidate})
            tasks=inputs.task_plan(study,prepared,context['build'])
            identifier=records.content_hash({'tasks':[row['task_id'] for row in tasks],'ode':prepared.get('ode',{})})
            declared={'experiment_id':study['id'],
                      'simulation_hash':records.content_hash({'fixed':study['fixed'],'sampling':study['sampling'],'realization':study.get('realization'),'build':context['build']}),
                      'analysis_hash':records.content_hash({'options':study['analysis'],'design':design}),
                      'search_hash':records.content_hash(study.get('search',{}))}
            results.plan(declared,identifier,prepared,tasks)
            old=results.tasks(identifier)
            for row in old:
                if row['status']=='complete' and row.get('result',{}).get('output'):
                    if not Path(row['result']['output']).is_file() or records.sha256_file(row['result']['output'])!=row['result'].get('sha256'):
                        results.invalidate(row['task_id'],'saved output differs from its recorded checksum')
                    else:
                        score_hash=records.content_hash({'physics':records.sha256_file(physics.__file__),
                                                        'calculation':inspect.getsource(score_output)})
                        time_file=row['result'].get('time_data')
                        invalid_time=needs_time and (not time_file or not Path(time_file).is_file() or records.sha256_file(time_file)!=row['result'].get('time_data_sha256'))
                        if row['result'].get('score_hash')!=score_hash or invalid_time:
                            updated=score_output(row['result'],prepared,row['input'],time_resolved=needs_time)
                            results.rescore(row['task_id'],updated)
            complete={row['task_id'] for row in results.tasks(identifier) if row['status']=='complete'}
            prepared_jobs.append({'candidate':candidate,'prepared':prepared,'tasks':tasks,'candidate_id':identifier,'cached':len(complete)})
        todo=[]
        for job in prepared_jobs:
            completed={row['task_id'] for row in results.tasks(job['candidate_id']) if row['status']=='complete'}
            todo.append([task for task in job['tasks'] if task['task_id'] not in completed])
        shared=set(); exhausted=False
        queue=((job,task) for group in itertools.zip_longest(*todo) for job,task in zip(prepared_jobs,group) if task is not None)
        while futures or not exhausted:
            while not exhausted and len(futures)<2*study['execution']['workers']:
                try: job,task=next(queue)
                except StopIteration:
                    exhausted=True; break
                if task['task_id'] in shared: continue
                shared.add(task['task_id']); token=results.start(task['task_id'])
                path=context['output']/'tasks'/task['task_id']/(token+'.partial')
                future=pool.submit(simulate.run_task,task,job['prepared'],study['installation'],context['environment'],path,context['active'],context['lock'])
                futures[future]=(job,task,token)
            if not futures: break
            done,_=wait(futures,timeout=.25,return_when=FIRST_COMPLETED)
            simulate.monitor_processes(context['active'],context['lock'],study['execution'])
            for future in done:
                job,task,token=futures.pop(future)
                try:
                    result=future.result()
                    if result['status']=='complete':
                        result=score_output(result,job['prepared'],task,time_resolved=needs_time)
                        temporary=Path(result['paths']['directory']); published=temporary.with_suffix('')
                        records.publish_directory(temporary,published)
                        for key,value in result['paths'].items():
                            if isinstance(value,str) and value.startswith(str(temporary)):
                                result['paths'][key]=str(published)+value[len(str(temporary)):]
                        for key in ('output','time_data'):
                            if key in result:
                                result[key]=str(published)+result[key][len(str(temporary)):]
                    results.finish(task['task_id'],token,result['status'],result)
                except (OSError,ValueError,RuntimeError) as exc:
                    results.finish(task['task_id'],token,'invalid',{'reason':str(exc)})
        for job in prepared_jobs:
            task_rows=results.tasks(job['candidate_id'])
            row={'name':job['candidate'].get('name',job['candidate_id'][:12]),'candidate_id':job['candidate_id'],
                 'parameters':job['prepared']['values'],'cached_tasks':job['cached'],'tasks':len(task_rows),
                 'completed_tasks':sum(r['status']=='complete' for r in task_rows)}
            row.update({key:job['candidate'][key] for key in ('inputs','trajectory','design_index','tolerance') if key in job['candidate']})
            if not results.completed(job['candidate_id']):
                row.update(status='incomplete',value=None,standard_error=None,
                           reasons=[{'task_id':r['task_id'],'state':r['status'],'reason':(r.get('result') or {}).get('reason')} for r in task_rows if r['status']!='complete'])
            else:
                blocks=[r['result'] for r in task_rows]; row['blocks']=blocks
                options=study['analysis']; count=int(options.get('bootstrap_draws',1000))
                estimate=analysis.estimate(blocks,design,bootstrap_draws=count,tail_bootstrap_draws=int(options.get('tail_bootstrap_draws',500)))
                historical=analysis.historical_summary(blocks,design)
                objective=options.get('objective','device_junction_qps_per_eV')
                if objective in ('total_qps_per_primary','total_QPs_per_primary','junction_qps_per_primary'):
                    value=historical['total_qps_per_primary']; se=historical.get('standard_error_per_primary',historical.get('standard_error'))
                    if se is None and historical.get('replica_standard_error') is not None:
                        se=historical['replica_standard_error']/(len(design['position_strata'])*design['events_per_task'])
                    units='summed_junction_QPs_per_primary'
                elif objective in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy'):
                    value,se,units=estimate['value'],estimate['standard_error'],estimate['units']
                elif objective=='integrated_density':
                    value,se,units=historical['value'],None,'MHz us'
                else: raise ValueError(f'unsupported objective {objective}')
                row.update(status='complete',value=float(value),standard_error=se,units=units,estimate=estimate,historical=historical)
                if needs_time:
                    row.update(density_response(blocks,job['prepared']))
                    if objective=='integrated_density':
                        row.update(value=row['integrated_density'],standard_error=None,units='MHz us',
                                   uncertainty_note='Replica responses are supplied; pooled nonlinear ODE uncertainty is not estimated.')
                results.observation(job['candidate_id'],{k:v for k,v in row.items() if k not in ('name','cached_tasks','blocks')},
                                    analysis={'options':options,'design':design,'code':records.sha256_file(analysis.__file__)},
                                    kind='physical_zero' if row['value']==0 else 'measured')
            rows.append(row)
    finally:
        with context['lock']:
            for active in list(context['active'].values()): simulate.stop_process(active['process'])
        pool.shutdown(wait=True,cancel_futures=True)
    context['timing']['evaluation_s']=context['timing'].get('evaluation_s',0)+time.perf_counter()-started
    return rows


def density_response(blocks, prepared):
    """Retained pooled QP-density calculation; individual realizations are diagnostics."""
    table=inputs.load_document(inputs.ROOT/'parameters.yaml')['morris']['QPDE_params']
    ode={row[0]:row[1] for row in table}; ode.update(prepared.get('ode',{}))
    fixed=prepared['fixed']; arrays=[]
    for block in blocks:
        with np.load(block['time_data']) as data: arrays.append(np.array(data['qps']))
    nsites=len({row['position'] for row in blocks}); replicas=sorted({row['replica'] for row in blocks})
    outcomes=[]
    for group in [list(range(len(blocks)))]+[[i for i,row in enumerate(blocks) if row['replica']==r] for r in replicas]:
        qps=sum((arrays[i] for i in group),np.zeros_like(arrays[0])); events=sum(blocks[i]['events'] for i in group)
        t,dg=physics.calculate_xqps(np.linspace(0,300000,1001),qps,fixed['setTopGap']/4.135667696e-15,
                                   ode['f_01'],ode['r'],ode['s'],ode['I_ph'],ode['pt'],ode['n_cooper'],
                                   fixed['electrode_height_um'],fixed['electrode_width_um'],fixed['setTopThickness_um'],events)
        outcomes.append(float(np.trapezoid(dg,t,axis=1).sum()))
    return {'integrated_density':outcomes[0],'integrated_density_by_replica':outcomes[1:],
            'density_parameters':ode,'density_sites':nsites}


def analyse_results(study, rows):
    complete=[row for row in rows if row['status']=='complete']
    objective=study['analysis']['objective']; design=analysis_design(study); comparisons=[]
    reference=rows[0] if rows and rows[0]['status']=='complete' else None
    if reference is not None and study['kind']!='sensitivity':
        for candidate in complete:
            if candidate is reference: continue
            if objective=='integrated_density':
                delta=candidate['value']-reference['value']
                paired={'value':delta,'standard_error':None,'relative_to_b':delta/reference['value'] if reference['value'] else None,
                        'z':None,'units':candidate.get('units'),'uncertainty_status':'aggregate nonlinear density evolution has no measured paired uncertainty'}
            else:
                paired=analysis.paired_difference(candidate['blocks'],reference['blocks'],design,objective=objective,
                                                   bootstrap_draws=int(study['analysis'].get('bootstrap_draws',1000)))
            comparisons.append({'candidate':candidate['name'],'reference':reference['name'],**paired})
    geometry={key:value for key,value in study['fixed'].items() if key.startswith(('electrode_','setSub'))}
    report={'id':study['id'],'kind':study['kind'],'objective':objective,'events_total_per_candidate':study['events_total'],
            'sites':len(study['design']['sites_mm']),'replicas':study['sampling']['replicas'],
            'events_per_task':study['sampling']['events_per_task'],'design':design,'results':rows,'comparisons':comparisons,
            'geometry':geometry,'sites_mm':study['design']['sites_mm'],
            'streams':{k:study['sampling'][k] for k in ('seed_base','seed_bank','position_seed')},
            'complete':bool(rows) and len(complete)==len(rows),'limitations':['Calibrated interface model.','Property targets need measured-material validation.',
                                                            'QP yield is not a logical-error probability.']}
    if study['kind']=='sensitivity':
        recorded=study.get('morris_design') and all(all(key in row for key in ('inputs','trajectory','design_index')) for row in rows)
        candidates=sorted(rows,key=lambda row:row['design_index']) if recorded else inputs.candidate_table(study)
        lookup={row['name']:row for row in rows}
        morris=study['morris_design']; problem=morris['problem']; names=problem['names']
        grouped={}
        for candidate in candidates: grouped.setdefault(candidate['trajectory'],[]).append(candidate)
        missing=[trajectory for trajectory,points in grouped.items()
                 if len(points)!=len(names)+1 or any(lookup.get(p['name'],{}).get('status')!='complete' for p in points)]
        if missing:
            report['sensitivity']={'status':'incomplete','missing_trajectories':missing,'declared_trajectories':len(grouped),
                                   'reason':'missing outcomes cannot be removed from Morris trajectories'}
        else:
            matrix=[[candidate['inputs'][name] for name in names] for candidate in candidates]
            response=[lookup[candidate['name']]['integrated_density'] if objective=='integrated_density' else lookup[candidate['name']]['value'] for candidate in candidates]
            method=study['analysis'].get('sensitivity_method','morris')
            result=analysis.sensitivity(matrix,response,names,problem=problem,method=method,
                                        log_outcome=study['analysis'].get('log_outcome',method=='pearson'),
                                        seed=morris['seed'],num_levels=morris['levels'])
            report['sensitivity']={**result,'status':'complete','response':objective,'morris_design':morris}
    if study['kind']=='tolerance':
        if reference is None:
            report['tolerance']={'locally_converged':False,'reason':'reference measurement is incomplete'}
        else:
            moves={}; paired={}; compared={row['candidate']:row for row in comparisons}
            for row in rows[1:]:
                name,side=row['name'].rsplit('_',1)
                label=name+(':minus' if side=='-1' else ':plus')
                moves[label]={'value':row.get('value')}
                if row['name'] in compared: paired[label]=compared[row['name']]
            for name in study.get('tolerance',{}):
                moves.setdefault(name+':minus',{'value':None}); moves.setdefault(name+':plus',{'value':None})
            varied={label.rsplit(':',1)[0] for label in moves}
            coordinates=inputs.to_unit(reference['parameters'],study['space']) if varied<=set(study['space']['names']) else None
            report['tolerance']=analysis.tolerance_summary(reference,moves,paired=paired,coordinates=coordinates,names=study['space']['names'])
            report['tolerance']['untested_moves']=study.get('tolerance_skipped',[])
    return report


def run_study(study_or_path, output, *, execute=True):
    start=time.perf_counter(); declared=inputs.load_document(study_or_path)
    if declared.get('kind')=='convergence' or declared.get('steps'):
        return run_series(study_or_path,output,execute=execute)
    study=inputs.load_study(study_or_path)
    if study.get('build',{}).get('mode')=='historical-unresolved':
        raise ValueError('historical reconstruction is read-only; use a new explicit study for simulation')
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    candidates=inputs.candidate_table(study) if study['kind']!='search' else None
    study.pop('resolved_hash',None); study['resolved_hash']=records.content_hash(study)
    records.write_json(output/'resolved.json',study)
    if not execute:
        for candidate in candidates if candidates is not None else [{'values':study['space']['baseline']}]:
            inputs.prepare_values(study,candidate)
        return {'id':study['id'],'events_total':study['events_total'],'sites':len(study['design']['sites_mm']),
                'replicas':study['sampling']['replicas'],'candidates':candidates,'executed':False}
    env,build=simulate.prepare_environment(study['installation'])
    records.write_json(output/'build.json',build)
    context={'environment':env,'build':build,'active':{},'lock':threading.Lock(),'output':output,'timing':{'preparation_s':time.perf_counter()-start}}
    rows=[]
    with records.Results(output/'results.sqlite') as results:
        results.save_state('output_root',str(output))
        if study['kind']=='search':
            options=study.get('search',{}); method=options.get('method','bo_gp'); target=int(options.get('evaluations',96))
            if target<1 or not study['space']['names']: raise ValueError('search needs positive evaluations and varied properties')
            opt={**options.get('options',{}),'floor':.5/study['events_total']}
            progress=results.state('search_progress')
            saved=progress['optimizer'] if progress is not None else results.state('optimizer')
            state=search.load_state(saved) if saved else search.new_state(method,seed=int(options.get('seed',41)),options=opt)
            rows=progress['rows'] if progress is not None else results.state('search_rows',[])
            if len(state['Y'])+len(state['rejected'])!=len(rows):
                raise RuntimeError('saved optimizer and measured search rows are inconsistent')
            attempts=0
            while len(rows)<target:
                remaining=target-len(rows)
                count=min(remaining,int(opt.get('popsize',12))) if method=='cmaes' else min(remaining,int(options.get('parallel_candidates',1)))
                points=list(state.get('pending',[])) or search.propose(method,state,study['space'],count=count,options=opt)
                if not points: raise RuntimeError('optimizer has no proposal while no simulations are pending')
                results.save_state('search_progress',{'optimizer':search.save_state(state),'rows':rows})
                batch=evaluate_points(study,[{'name':f'{method}_{len(rows)+i:04d}','values':point} for i,point in enumerate(points)],results,context)
                for point,row in zip(points,batch):
                    source=state.get('proposals',{}).get(search.point_key(point,study['space']),{})
                    row['proposal']=source
                    search.update(method,state,point,row['value'],se=row.get('standard_error'),
                                  rejected_reason=None if row['status']=='complete' else str(row.get('reasons')))
                    rows.append(row)
                for previous in rows:
                    key=search.point_key(previous['parameters'],study['space'])
                    previous['proposal']['used_in_optimizer_update']=bool(state['used'].get(key,False))
                results.save_state('search_progress',{'optimizer':search.save_state(state),'rows':rows})
                attempts+=1
                if attempts>target*10: raise RuntimeError('search could not complete its declared evaluations')
        else:
            rows=evaluate_points(study,candidates,results,context)
        report=analyse_results(study,rows)
        report.update(build=build,timing={**context['timing'],'total_s':time.perf_counter()-start})
        records.write_json(output/'summary.json',report,overwrite=True)
        results.save_state('summary',report)
    return report


def read_analysis(path, output=None, settings=None):
    root=Path(path)
    study=inputs.load_study(root/'resolved.json')
    if settings:
        study['analysis'].update(inputs.load_document(settings))
    with records.Results(root/'results.sqlite',create=False) as result:
        saved=result.state('summary')
        if saved is None: raise ValueError('result has no recorded summary')
        old_root=result.state('output_root')
        declarations={row['candidate_id']:row['input'] for row in result.candidates()}
        for row in saved['results']:
            if row['status']!='complete': continue
            blocks=[]
            for stored in row['blocks']:
                block=copy.deepcopy(stored)
                origin=Path(old_root) if old_root else Path(str(block['output']).split('/tasks/',1)[0])
                for field in ('output','time_data'):
                    if field in block:
                        block[field]=str(root/Path(block[field]).relative_to(origin))
                if not Path(block['output']).is_file() or records.sha256_file(block['output'])!=block['sha256']:
                    raise ValueError('saved measurements no longer match their raw files')
                scored=score_output(block,declarations[row['candidate_id']],block)
                blocks.append(scored)
            row['blocks']=blocks; design=analysis_design(study)
            row['estimate']=analysis.estimate(blocks,design,bootstrap_draws=int(study['analysis'].get('bootstrap_draws',1000)))
            row['historical']=analysis.historical_summary(blocks,design)
            objective=study['analysis']['objective']
            if objective in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy'):
                row.update(value=row['estimate']['value'],standard_error=row['estimate']['standard_error'],units=row['estimate']['units'])
            elif objective=='integrated_density':
                if any(not Path(block.get('time_data','')).is_file() or records.sha256_file(block['time_data'])!=block.get('time_data_sha256') for block in blocks):
                    raise ValueError('time data need regeneration from raw hits before density analysis')
                row.update(density_response(blocks,declarations[row['candidate_id']]))
                row.update(value=row['integrated_density'],standard_error=None,units='MHz us')
            else:
                row.update(value=row['historical']['value'],standard_error=row['historical']['standard_error'],units='summed_junction_QPs_per_primary')
        report=analyse_results(study,saved['results'])
    if output: records.write_json(output,report)
    return report


def compare_runs(first, second, output=None, margin=.05):
    a=json.loads((Path(first)/'summary.json').read_text()); b=json.loads((Path(second)/'summary.json').read_text())
    if a['objective']!=b['objective'] or a['design']!=b['design']:
        raise ValueError('comparison requires the same objective and site/realization design')
    for key in ('geometry','sites_mm'):
        if key in a or key in b:
            if a.get(key)!=b.get(key): raise ValueError(f'comparison differs in physical {key}')
    if not np.isfinite(margin) or margin<=0: raise ValueError('relative equivalence margin must be positive and finite')
    rows=[]
    by_name={row['name']:row for row in b['results']}
    if set(by_name)!={row['name'] for row in a['results']}: raise ValueError('comparison requires matching candidate sets')
    for row in a['results']:
        other=by_name.get(row['name'])
        if row['status']!='complete' or not other or other['status']!='complete':
            raise ValueError('comparison requires complete matched candidates')
        if row['parameters']!=other['parameters']:
            raise ValueError('comparison material properties differ')
        if a['objective']=='integrated_density':
            rows.append({'name':row['name'],'difference':row['value']-other['value'],
                         'equivalent_within_margin':False,'uncertainty_status':'per-realization density evolution is required to measure uncertainty'})
            continue
        paired=analysis.paired_difference(row['blocks'],other['blocks'],a['design'],objective=a['objective'])
        ra=analysis.realization_estimates(row['blocks'],a['design'],a['objective'])
        rb=analysis.realization_estimates(other['blocks'],b['design'],b['objective'])
        matched=analysis.compare_repeated(ra,rb,relative_margin=margin,paired=True) if min(len(ra),len(rb))>=2 else {'status':'fewer than two realizations','equivalent_within_margin':False}
        repeated=analysis.compare_repeated(ra,rb,relative_margin=margin) if min(len(ra),len(rb))>=2 else {'status':'fewer than two realizations'}
        repeated['interpretation']='Welch interval ignores pairing; a diagnostic check, not a strict independent-sample experiment when random streams are shared.'
        rows.append({'name':row['name'],'paired':paired,'paired_realization_check':matched,'realization_check':repeated,
                     'equivalent_within_margin':matched['equivalent_within_margin']})
    report={'comparison':'selected response with matched sites and realizations','relative_equivalence_margin':margin,
            'objective':a['objective'],'same_random_streams':a.get('streams')==b.get('streams'),'results':rows}
    if output: records.write_json(output,report)
    return report


def run_series(study_or_path, output, execute=True):
    """Run declared studies in order; refine sites without changing earlier streams."""
    from scipy.stats import qmc

    source = Path(study_or_path).resolve() if not isinstance(study_or_path, dict) else inputs.ROOT / 'study.yaml'
    raw = inputs.load_document(study_or_path)
    explicit_temperature = 'temperature_K' in raw.get('fixed', {})
    if 'resolved_hash' in raw:
        temperature = raw.get('parameters', {}).get('temperature')
        explicit_temperature = isinstance(temperature, (int, float)) or isinstance(temperature, dict) and temperature.get('mode') == 'fixed'
        raw = inputs.load_study(raw)
    internal = ('space', 'design', 'events_total', 'resolved_hash', 'morris_design', 'tolerance_skipped')
    raw = {key: copy.deepcopy(value) for key, value in raw.items() if key not in internal}
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    identifier = raw.get('id', source.stem)
    steps = raw.get('steps')
    levels = raw.get('levels')
    if steps and levels:
        raise ValueError('choose explicit study steps or refinement levels')
    if not steps and not levels:
        raise ValueError('a study series needs nonempty steps or levels')
    jobs, signatures, reports = [], [], []
    if steps:
        if not isinstance(steps, list):
            raise ValueError('study steps must be a list')
        for index, step in enumerate(steps):
            if isinstance(step, str):
                path = (source.parent / step).resolve()
                if path == source:
                    raise ValueError('a study cannot include itself as a step')
                document = inputs.load_document(path)
                child = inputs.load_study(path)
            elif isinstance(step, dict):
                document = copy.deepcopy(step)
                for key in ('materials_file',):
                    if key in document:
                        document[key] = str((source.parent / Path(document[key]).expanduser()).resolve())
                install = document.get('installation', {})
                for key in ('executable', 'lattice_data', 'macro_template', 'library_dir'):
                    if key in install:
                        install[key] = str((source.parent / Path(install[key]).expanduser()).resolve())
                if 'environment_scripts' in install:
                    install['environment_scripts'] = [str((source.parent / Path(p).expanduser()).resolve())
                                                      for p in install['environment_scripts']]
                if 'design_file' in document.get('sampling', {}):
                    document['sampling']['design_file'] = str((source.parent / Path(document['sampling']['design_file']).expanduser()).resolve())
                child = inputs.load_study(document)
            else:
                raise ValueError('each step must be a study path or mapping')
            fixed_temperature = 'temperature_K' in document.get('fixed', {})
            if 'resolved_hash' in document:
                temperature = document.get('parameters', {}).get('temperature')
                fixed_temperature = isinstance(temperature, (int, float)) or isinstance(temperature, dict) and temperature.get('mode') == 'fixed'
            child = {key: copy.deepcopy(value) for key, value in child.items() if key not in internal}
            if not fixed_temperature:
                child['fixed'].pop('temperature_K', None)
            child['id'] = f"{identifier}-step-{index + 1:03d}-{child.get('id', 'study')}"
            jobs.append(child)
    else:
        if raw.get('kind') != 'convergence' or not isinstance(levels, list):
            raise ValueError('refinement levels require kind convergence and a list')
        template = copy.deepcopy(raw); template.pop('levels', None); template.pop('steps', None)
        template['kind'] = 'confirmation'
        if not explicit_temperature:
            template.get('fixed', {}).pop('temperature_K', None)
        for key in ('materials_file',):
            if key in template:
                template[key] = str((source.parent / Path(template[key]).expanduser()).resolve())
        installation = template.get('installation', {})
        for key in ('executable', 'lattice_data', 'macro_template', 'library_dir'):
            if key in installation:
                installation[key] = str((source.parent / Path(installation[key]).expanduser()).resolve())
        if 'environment_scripts' in installation:
            installation['environment_scripts'] = [str((source.parent / Path(p).expanduser()).resolve())
                                                     for p in installation['environment_scripts']]
        sampling = template.setdefault('sampling', {})
        if 'design_file' in sampling:
            sampling['design_file'] = str((source.parent / Path(sampling['design_file']).expanduser()).resolve())
        previous = {'sites': sampling.get('sites', 16), 'replicas': sampling.get('replicas', 4),
                    'events_per_task': template.get('event_counts', {}).get('events_per_task', sampling.get('events_per_task', 31250))}
        normalized = []
        for level in levels:
            if not isinstance(level, dict) or set(level) - set(previous):
                raise ValueError('each level may specify sites, replicas and events_per_task')
            current = {**previous, **level}
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in current.values()):
                raise ValueError('refinement counts must be positive integers')
            if normalized and (current['sites'] < previous['sites'] or current['replicas'] < previous['replicas']):
                raise ValueError('site and replica refinement must retain earlier positions and realizations')
            normalized.append(current); previous = current
        sampling.update(normalized[0])
        if 'event_counts' in template:
            template['event_counts'].pop('events_total', None)
            template['event_counts']['events_per_task'] = normalized[0]['events_per_task']
        initial = inputs.load_study(template)
        design = copy.deepcopy(initial['design'])
        if len(design['sites_mm']) != normalized[0]['sites']:
            raise ValueError('the first level must retain every site in its recorded design')
        weights = dict(design['stratum_weights']); labels = list(design['stratum'])
        ordered = [list(point) for point in design['sites_mm']]
        seen = {tuple(point) for point in ordered}
        if len(seen) != len(ordered):
            raise ValueError('the initial design contains repeated sites')
        regions = list(weights)
        counts = {name: labels.count(name) for name in regions}
        if any(counts[name] == 0 for name in regions):
            raise ValueError('every weighted physical region must contain sites')
        if set(regions) != {'surface'} and set(regions) != set(inputs.REGIONS):
            raise ValueError('refinement needs the recorded surface or five physical region definitions')
        fixed = initial['fixed']; seed = initial['sampling']['position_seed']
        span = float(fixed.get('position_half_span_mm', 4.)); z = float(initial['sampling'].get('z_mm', ordered[0][2]))
        streams, buffers, cursors = {}, {name: [] for name in regions}, {name: 0 for name in regions}
        for name in regions:
            if name == 'S0_junction':
                streams[name] = np.random.default_rng((seed * 1000003) % (2**31 - 1))
            else:
                region_seed = seed if name == 'surface' else (seed * 1000003 + inputs.REGIONS.index(name)) % (2**31 - 1)
                streams[name] = qmc.Sobol(d=2, scramble=True, seed=region_seed)
        sigma = initial['sampling'].get('region_sd', {})
        allocation = np.array([weights[name] * float(sigma.get(name, 1.)) for name in regions])
        if not np.isfinite(allocation).all() or np.any(allocation < 0) or allocation.sum() <= 0:
            raise ValueError('region sampling spreads must be finite and nonnegative')
        allocation /= allocation.sum()
        template = {key: copy.deepcopy(value) for key, value in initial.items() if key not in internal}
        if not explicit_temperature:
            template['fixed'].pop('temperature_K', None)
        for index, level in enumerate(normalized):
            while len(ordered) < level['sites']:
                deficit = level['sites'] * allocation - np.array([counts[name] for name in regions])
                name = regions[int(np.argmax(deficit))]
                point = None
                for _ in range(100000):
                    if not buffers[name]:
                        if name == 'S0_junction':
                            e = cursors[name] % len(fixed['electrode_x_mm']); cursors[name] += 1
                            x = fixed['electrode_x_mm'][e] + streams[name].uniform(-fixed['electrode_width_um'] / 2000, fixed['electrode_width_um'] / 2000)
                            y = fixed['electrode_y_mm'][e] + streams[name].uniform(-fixed['electrode_height_um'] / 2000, fixed['electrode_height_um'] / 2000)
                            buffers[name] = [[round(float(x), 6), round(float(y), 6)]]
                        else:
                            xy = np.round(streams[name].random(8192) * 2 * span - span, 6)
                            if name != 'surface':
                                xy = xy[inputs.region_distances(xy, fixed) == inputs.REGIONS.index(name)]
                            buffers[name] = xy.tolist()[::-1]
                    if not buffers[name]:
                        continue
                    candidate = [*buffers[name].pop(), z]
                    if tuple(candidate) not in seen:
                        point = candidate
                        break
                if point is None:
                    raise ValueError(f'could not append a distinct site in {name}')
                ordered.append(point); labels.append(name); seen.add(tuple(point)); counts[name] += 1
            child_design = {key: copy.deepcopy(value) for key, value in design.items()
                            if key not in ('sites_mm', 'stratum', 'position_strata', 'stratum_counts', 'design_hash')}
            child_design.update(sites_mm=copy.deepcopy(ordered), stratum=list(labels),
                                stratum_counts=dict(counts), stratum_weights=dict(weights))
            child_design['design_hash'] = records.content_hash(child_design)
            design_path = output / f'design-{index + 1:03d}.json'
            records.write_json(design_path, child_design)
            child = copy.deepcopy(template)
            child['id'] = f'{identifier}-level-{index + 1:03d}'
            child['sampling'].update(level, kind='recorded-stratified', design_file=str(design_path))
            if 'event_counts' in child:
                child['event_counts'].pop('events_total', None)
                child['event_counts']['events_per_task'] = level['events_per_task']
            jobs.append(child)
    for child in jobs:
        signature = {key: child.get(key) for key in ('fixed', 'parameters', 'constraints', 'realization', 'candidates', 'installation')}
        signature['objective'] = child.get('analysis', {}).get('objective')
        signatures.append(records.content_hash(signature))
    for index, child in enumerate(jobs):
        destination = output / f'{index + 1:03d}-{child["id"]}'
        try:
            report = run_study(child, destination, execute=execute)
        except (OSError, ValueError, RuntimeError) as exc:
            reports.append({'id': child['id'], 'complete': False, 'status': 'failed', 'reason': str(exc)})
            break
        reports.append(report)
        if execute and report.get('complete') is not True:
            break
    complete = bool(execute and len(reports) == len(jobs) and all(row.get('complete') is True for row in reports))
    prepared = bool(not execute and len(reports) == len(jobs) and all(row.get('executed') is False for row in reports))
    same_goal = len(set(signatures)) == 1
    transitions = []
    for index, (coarse, fine) in enumerate(zip(reports, reports[1:])):
        earlier, later = jobs[index]['sampling'], jobs[index + 1]['sampling']
        refined = [name for name in ('sites', 'replicas', 'events_per_task') if later[name] > earlier[name]]
        comparison = {'coarse': coarse['id'], 'fine': fine['id'], 'passed': False,
                      'ranking_checked': False, 'candidates': {}, 'refined_axes': refined,
                      'spatial_refined': 'sites' in refined}
        if not coarse.get('complete') or not fine.get('complete'):
            comparison['reason'] = 'both levels need complete measured results'
        elif coarse.get('objective') != fine.get('objective'):
            comparison['reason'] = 'the selected physical response differs'
        else:
            a = {row['name']: row for row in coarse['results']}
            b = {row['name']: row for row in fine['results']}
            if set(a) != set(b) or len(a) < 2:
                comparison['reason'] = 'at least two complete matching candidates are needed to check rankings'
            else:
                try:
                    ca = {name: row['estimate'] for name, row in a.items()}
                    cb = {name: row['estimate'] for name, row in b.items()}
                    baseline = next(iter(a))
                    settings = jobs[index + 1].get('analysis', {})
                    for name in a:
                        result = analysis.spatial_convergence(ca[name], cb[name],
                            baseline_coarse=ca[baseline]['value'], baseline_fine=cb[baseline]['value'],
                            relative_limit=float(settings.get('relative_limit', .05)),
                            sigma_limit=float(settings.get('sigma_limit', 2.)),
                            leverage_limit=float(settings.get('leverage_limit', .10)))
                        result['region_relative_shifts'] = {key: value if np.isfinite(value) else None
                                                            for key, value in result['region_relative_shifts'].items()}
                        comparison['candidates'][name] = result
                    ranking = analysis.rank_stability(ca, cb)
                    ranking = {key: None if isinstance(value, float) and not np.isfinite(value) else value
                               for key, value in ranking.items()}
                    comparison.update(ranking=ranking, ranking_checked=True,
                        passed=bool(refined) and all(row['passed'] for row in comparison['candidates'].values()) and ranking['stable_within_uncertainty'])
                except (KeyError, ValueError, TypeError) as exc:
                    comparison['reason'] = f'convergence quantities are unavailable: {exc}'
        transitions.append(comparison)
    verdict = bool(complete and same_goal and transitions and transitions[-1]['passed'] and transitions[-1]['spatial_refined'])
    summary = {'id': identifier, 'kind': raw.get('kind', 'series'), 'executed': bool(execute),
               'complete': complete, 'status': 'complete' if complete else 'prepared' if prepared else 'incomplete',
               'same_physics_goal': same_goal, 'declared_studies': len(jobs), 'studies': reports,
               'convergence': {'converged': verdict, 'ranking_checked': bool(transitions and transitions[-1]['ranking_checked']),
                               'comparisons': transitions,
                               'basis': 'the final two requested levels; every requested measurement must be complete',
                               'quantity': 'device area weighted junction QPs per injected eV',
                               'limitations': ['Coarse and fine error comparison omits their covariance.',
                                               'Convergence at the final two levels does not establish measured material properties.']}}
    records.write_json(output / 'summary.json', summary, overwrite=True)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); commands=parser.add_subparsers(dest='command',required=True)
    for name in ('check','run'):
        command=commands.add_parser(name); command.add_argument('study'); command.add_argument('--output',type=Path)
        if name=='run': command.add_argument('--prepare-only',action='store_true')
    command=commands.add_parser('analyse'); command.add_argument('path'); command.add_argument('--output'); command.add_argument('--settings')
    command=commands.add_parser('compare'); command.add_argument('first'); command.add_argument('second'); command.add_argument('--output'); command.add_argument('--margin',type=float,default=.05)
    command=commands.add_parser('plot'); command.add_argument('path'); command.add_argument('--output',required=True); command.add_argument('--kind',default='summary')
    for name in ('inventory','history','verify-archive'):
        command=commands.add_parser(name); command.add_argument('path'); command.add_argument('--output')
        if name=='history': command.add_argument('--invalidations')
        if name=='inventory': command.add_argument('--git-repository'); command.add_argument('--revision',default='HEAD')
    command=commands.add_parser('archive'); command.add_argument('path'); command.add_argument('--output',required=True); command.add_argument('--member',action='append')
    command=commands.add_parser('recover'); command.add_argument('path'); command.add_argument('--output',required=True)
    command=commands.add_parser('convert-copy'); command.add_argument('path'); command.add_argument('--output',required=True); command.add_argument('--old-root'); command.add_argument('--new-root')
    command=commands.add_parser('catalog'); command.add_argument('--output',required=True); command.add_argument('--ids',nargs='+'); command.add_argument('--max-results',type=int,default=50)
    command=commands.add_parser('project'); command.add_argument('target'); command.add_argument('--catalog',required=True); command.add_argument('--features',required=True); command.add_argument('--output',required=True); command.add_argument('--top',type=int,default=10)
    command=commands.add_parser('probe-density'); command.add_argument('material'); command.add_argument('--study',default=str(inputs.ROOT/'studies/reference.yaml')); command.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='check':
            declared=inputs.load_document(args.study)
            if declared.get('kind')=='convergence' or declared.get('steps'):
                import tempfile
                with tempfile.TemporaryDirectory(prefix='material-study-check-') as temporary:
                    checked=run_series(args.study,temporary,execute=False)
                if checked['status']!='prepared':
                    failure=next((row.get('reason') for row in checked['studies'] if row.get('status')=='failed'), 'a declared child study is invalid')
                    raise ValueError(failure)
                answer={'id':checked['id'],'kind':checked['kind'],'checked_studies':checked['declared_studies']}
            else:
                study=inputs.load_study(args.study)
                for candidate in inputs.candidate_table(study): inputs.prepare_values(study,candidate)
                answer={'id':study['id'],'kind':study['kind'],'sites':len(study['design']['sites_mm']),'replicas':study['sampling']['replicas'],
                        'events_total':study['events_total'],'search_parameters':study['space']['names']}
        elif args.command=='run':
            output=args.output or inputs.ROOT/'material_scan_data/experiments'/Path(args.study).stem
            answer=run_study(args.study,output,execute=not args.prepare_only)
        elif args.command=='analyse': answer=read_analysis(args.path,args.output,args.settings)
        elif args.command=='compare': answer=compare_runs(args.first,args.second,args.output,args.margin)
        elif args.command=='plot':
            import plots
            answer={'plot':str(plots.plot_result(args.path,args.output,args.kind))}
        elif args.command=='inventory': answer=records.git_inventory(args.path,args.git_repository,args.revision) if args.git_repository else records.inventory(args.path)
        elif args.command=='history': answer=records.history_summary(args.path,args.invalidations)
        elif args.command=='archive': answer=records.create_archive(args.path,args.member,args.output)
        elif args.command=='verify-archive': answer=records.verify_archive(args.path)
        elif args.command=='recover': answer={'path':str(records.recover_archive(args.path,args.output))}
        elif args.command=='catalog': answer=inputs.fetch_catalog(args.output,args.ids,args.max_results)
        elif args.command=='project':
            target=inputs.load_document(args.target); catalog=inputs.load_document(args.catalog); feature_spec=inputs.load_document(args.features)
            answer={'target':target,'projection':analysis.project_materials(target.get('features',target),catalog['candidates'],feature_spec,top=args.top),
                    'interpretation':'Distances use only measured fields; missing fields are reported. Simulate shortlisted complete material records before a material conclusion.'}
            records.write_json(args.output,answer)
        elif args.command=='probe-density':
            answer=simulate.probe_density(args.material,inputs.load_study(args.study),args.output)
        else: answer={'path':str(records.convert_copy(args.path,args.output,args.old_root,args.new_root))}
        if getattr(args,'output',None) and args.command in ('check','inventory','history','verify-archive'):
            records.write_json(args.output,answer)
        display=answer if args.command not in ('run','analyse') else {k:v for k,v in answer.items() if k not in ('results','build','design')}
        print(records.canonical_json(display)); return 0 if answer.get('complete',True) else 1
    except (OSError,ValueError,RuntimeError) as error:
        print(f'error: {error}',file=sys.stderr); return 2


if __name__=='__main__':
    raise SystemExit(main())
