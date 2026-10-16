"""Study -> transport tasks -> N[i,r,e] -> J(x) or density response.

x: physical properties; i: source site; r: realization; e: junction region.
"""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import inspect
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

import numpy as np

import analysis
import physics
import records
import search

ROOT = Path(__file__).resolve().parent
RUNTIME_FIELDS = ('material_catalog', 'lattices', 'parameter_table')


def evaluate_points(study, candidates, results, context):
    """Evaluate each x using its complete (i,r) task set before estimating J(x)."""
    started=time.perf_counter(); prepared_jobs=[]; futures={}; rows=[]
    stop_event=threading.Event()
    design=analysis.analysis_design(study)
    if 'score_hash' not in context: context['score_hash']=analysis.scoring_identity()
    if 'analysis_sha256' not in context: context['analysis_sha256']=records.sha256_file(analysis.__file__)
    verified=context.setdefault('verified',{})
    needs_time=study['analysis'].get('time_resolved',False) or study['analysis']['objective']=='integrated_density'
    pool=ThreadPoolExecutor(max_workers=study['execution']['workers'])
    try:
        for candidate in candidates:
            prepared=physics.prepare_values(study,candidate)
            prepared.update({key:candidate[key] for key in ('macro','config','ode','trajectory','stream_group') if key in candidate})
            if study['sampling'].get('independent_candidate_streams',False):
                prepared.setdefault('stream_group',records.content_hash({'study':study['id'],'name':candidate.get('name'),
                                                                         'values':prepared['values']}))
            prepared['input_text']=input_text(prepared,context.get('installation',study['installation']))
            tasks=task_plan(study,prepared,context['build'])
            identifier=records.content_hash({'tasks':[row['task_id'] for row in tasks],'ode':prepared.get('ode',{})})
            declared={'experiment_id':study['id'],
                      'simulation_hash':records.content_hash({'fixed':study['fixed'],'sampling':study['sampling'],'realization':study.get('realization'),'build':context['build']}),
                      'analysis_hash':records.content_hash({'options':study['analysis'],'design':design}),
                      'search_hash':records.content_hash(study.get('search',{}))}
            results.plan(declared,identifier,{k:v for k,v in prepared.items() if k not in ('source_text','input_text')},tasks)
            old=results.tasks(identifier); changed=False
            for row in old:
                if row['status']=='complete' and row.get('result',{}).get('output'):
                    path=Path(row['result']['output']).resolve()
                    before=path.stat() if path.is_file() else None
                    if before is None or records.sha256_file(path)!=row['result'].get('sha256'):
                        results.revise(row['task_id'],reason='saved output differs from its recorded checksum')
                        changed=True
                    else:
                        stat=path.stat()
                        signature=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
                        if signature!=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns):
                            raise ValueError('saved output changed during checksum verification')
                        verified[(str(path),row['result']['sha256'])]=signature
                        time_file=row['result'].get('time_data')
                        invalid_time=needs_time and (not time_file or not Path(time_file).is_file() or records.sha256_file(time_file)!=row['result'].get('time_data_sha256'))
                        if row['result'].get('score_hash')!=context['score_hash'] or invalid_time:
                            updated=analysis.score_output(row['result'],prepared,row['input'],time_resolved=needs_time,score_hash=context['score_hash'])
                            results.revise(row['task_id'],result=updated); changed=True
            if changed: old=results.tasks(identifier)
            complete={row['task_id'] for row in old if row['status']=='complete'}
            prepared_jobs.append({'candidate':candidate,'prepared':prepared,'tasks':tasks,'candidate_id':identifier,'cached':len(complete),'task_rows':old,'ran':False})
        todo=[]
        for job in prepared_jobs:
            completed={row['task_id'] for row in job['task_rows'] if row['status']=='complete'}
            todo.append([task for task in job['tasks'] if task['task_id'] not in completed])
        if context.get('transport_read_only') and any(todo):
            raise ValueError('earlier source comparison permits cached analysis only; use a new output directory for transport')
        shared=set(); exhausted=False
        queue=((job,task) for group in itertools.zip_longest(*todo) for job,task in zip(prepared_jobs,group) if task is not None)
        while futures or not exhausted:
            if context.get('deadline') is not None and time.monotonic()>=context['deadline']:
                close_workers(pool,futures,context['active'],context['lock'],stop_event)
                for job,task,token in futures.values():
                    results.finish(task['task_id'],token,'interrupted',{'reason':'search elapsed limit reached'})
                futures.clear(); break
            while not exhausted and len(futures)<2*study['execution']['workers']:
                try: job,task=next(queue)
                except StopIteration:
                    exhausted=True; break
                if task['task_id'] in shared: continue
                shared.add(task['task_id']); token=results.start(task['task_id']); job['ran']=True
                path=context['output']/'tasks'/task['task_id']/(token+'.partial')
                future=pool.submit(run_task,task,job['prepared'],context.get('installation',study['installation']),context['environment'],path,context['active'],context['lock'],stop_event)
                futures[future]=(job,task,token)
            if not futures: break
            done,_=wait(futures,timeout=.25,return_when=FIRST_COMPLETED)
            monitor_processes(context['active'],context['lock'],study['execution'],stop_event)
            for future in done:
                job,task,token=futures.pop(future)
                try:
                    result=future.result()
                    if result['status']=='complete':
                        result=analysis.score_output(result,job['prepared'],task,time_resolved=needs_time,score_hash=context['score_hash'])
                        temporary=Path(result['paths']['directory']); published=temporary.with_suffix('')
                        records.publish(temporary,published)
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
            task_rows=results.tasks(job['candidate_id']) if shared.intersection(row['task_id'] for row in job['task_rows']) else job['task_rows']
            row={'name':job['candidate'].get('name',job['candidate_id'][:12]),'candidate_id':job['candidate_id'],
                 'parameters':job['prepared']['values'],'cached_tasks':job['cached'],'tasks':len(task_rows),
                 'completed_tasks':sum(r['status']=='complete' for r in task_rows),
                 'elapsed_s':sum((r.get('result') or {}).get('elapsed_s',0.) for r in task_rows)/study['execution']['workers'],
                 'evaluation_wall_s':time.perf_counter()-started}
            row.update({key:job['candidate'][key] for key in ('inputs','trajectory','design_index','tolerance') if key in job['candidate']})
            if not results.completed(job['candidate_id'],tasks=task_rows,verified=verified):
                row['elapsed_s']=max(row['elapsed_s'],row['evaluation_wall_s']/max(1,len(prepared_jobs)))
                row.update(status='incomplete',value=None,standard_error=None,
                           reasons=[{'task_id':r['task_id'],'state':r['status'],'reason':(r.get('result') or {}).get('reason')} for r in task_rows if r['status']!='complete'])
            else:
                blocks=[r['result'] for r in task_rows]; row['blocks']=blocks
                options=study['analysis']; objective=options['objective']; data=analysis.prepare_counts(blocks,design)
                diagnostics=bool(options.get('diagnostics',study['kind']!='search'))
                estimate=analysis.summarize(data,bootstrap_draws=int(options.get('bootstrap_draws',1000)),
                    tail_bootstrap_draws=int(options.get('tail_bootstrap_draws',500)),diagnostics=diagnostics,
                    uncertainty=objective in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') or diagnostics)
                historical=analysis.summarize(data,objective='total_qps_per_primary',diagnostics=diagnostics)
                selected=estimate if objective in ('device_junction_qps_per_eV','device_weighted_junction_qps_per_energy') else historical
                row.update(status='complete',blocks=blocks,value=selected['value'],standard_error=selected['standard_error'],
                           units=selected['units'],estimate=estimate,historical=historical)
                replicas=selected.get('by_replica',[])
                # s_T=sd_r(J_D,r)/sqrt(R); source-position error stays separate.
                row['transport_standard_error']=float(np.std(replicas,ddof=1)/np.sqrt(len(replicas))) if len(replicas)>1 else None
                if needs_time:
                    defaults={entry[0]:entry[1] for entry in study['parameter_table']['morris']['QPDE_params']}
                    row.update(analysis.density_response(blocks,job['prepared'],defaults,context.setdefault('time_sums',{})))
                    if objective=='integrated_density':
                        row.update(value=row['integrated_density'],standard_error=None,transport_standard_error=None,units='MHz us',
                                   uncertainty_note='Replica responses are supplied; pooled nonlinear ODE uncertainty is not estimated.')
                results.observation(job['candidate_id'],{k:v for k,v in row.items() if k not in ('name','cached_tasks','blocks','evaluation_wall_s')},
                                    analysis={'options':options,'design':design,'code':context['analysis_sha256']},
                                    kind='physical_zero' if row['value']==0 else 'measured',tasks=task_rows,verified=verified)
            rows.append(row)
    finally:
        close_workers(pool,futures,context['active'],context['lock'],stop_event)
    context['timing']['evaluation_s']=context['timing'].get('evaluation_s',0)+time.perf_counter()-started
    return rows


def close_workers(pool, futures, active, lock, stop_event):
    """Close the launch gate, cancel waiting work and reap every started child."""
    previous={}
    if threading.current_thread() is threading.main_thread():
        for kind in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
            previous[kind]=signal.signal(kind,signal.SIG_IGN)
    try:
        with lock:
            stop_event.set()
            for future in futures: future.cancel()
            for row in list(active.values()): stop_process(row['process'])
        pool.shutdown(wait=True,cancel_futures=True)
    finally:
        for kind,handler in previous.items(): signal.signal(kind,handler)


def run_study(study_or_path, output, *, execute=True):
    start=time.perf_counter(); declared=records.load_document(study_or_path)
    if declared.get('kind')=='convergence' or declared.get('steps'):
        return run_series(study_or_path,output,execute=execute)
    study=load_study(study_or_path)
    if study['kind']=='search' and not study['analysis'].get('store_search_blocks',True) and study['analysis'].get('diagnostics',False):
        raise ValueError('search diagnostics require stored blocks; use diagnostics false for compact search history')
    if study.get('build',{}).get('mode')=='historical-unresolved':
        raise ValueError('historical reconstruction is read-only; use a new explicit study for simulation')
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    if (output/'summary.json').is_file() and records.load_document(output/'summary.json').get('raw_files_present') is False:
        raise ValueError('recover the recorded archive or use a new output directory before simulation')
    candidates=candidate_table(study) if study['kind']!='search' else None
    study.pop('resolved_hash',None)
    frozen={key:value for key,value in study.items() if key not in RUNTIME_FIELDS}
    study['resolved_hash']=records.content_hash(frozen); frozen['resolved_hash']=study['resolved_hash']
    records.write_json(output/'resolved.json',frozen)
    if not execute:
        for candidate in candidates if candidates is not None else [{'values':study['space']['baseline']}]:
            physics.prepare_values(study,candidate)
        return {'id':study['id'],'events_total':study['events_total'],'sites':len(study['design']['sites_mm']),
                'replicas':study['sampling']['replicas'],'candidates':candidates,'executed':False}
    env,build=prepare_environment(study['installation'])
    current_build=build
    build=verify_previous_inputs(study,candidates,output,build)
    records.write_json(output/'build.json',build)
    context={'installation':{**study['installation'],'template_text':Path(study['installation']['macro_template']).read_text()},'environment':env,'build':build,'transport_read_only':build['renderer_sha256']!=current_build['renderer_sha256'],'active':{},'lock':threading.Lock(),'output':output,'timing':{'preparation_s':time.perf_counter()-start}}
    rows=[]
    with records.Results(output/'results.sqlite') as results:
        results.save_state('output_root',str(output))
        if study['kind']=='search':
            options=study.get('search',{}); method=options.get('method','bo_gp'); target=int(options.get('evaluations',96))
            if target<1 or not study['space']['names']: raise ValueError('search needs positive evaluations and varied properties')
            measured_target=options.get('measured_evaluations',False)
            max_attempts=options.get('max_attempts',2*target if measured_target else target)
            if not isinstance(measured_target,bool) or isinstance(max_attempts,bool) or not isinstance(max_attempts,int) or max_attempts<target:
                raise ValueError('search measured_evaluations must be boolean and max_attempts an integer at least evaluations')
            opt={**options.get('options',{}),'floor':.5/study['events_total'],'objective':study['analysis']['objective']}
            progress=results.read('state','search_progress')
            saved=progress['optimizer'] if progress is not None else results.read('state','optimizer')
            state=search.load_state(saved) if saved else search.new_state(method,seed=int(options.get('seed',41)),options=opt)
            initial=options.get('initial_observations',[])
            if initial and not saved:
                search.import_observations(state,study['space'],initial)
                opt=state['options']
            initial_count=int(state.get('initial_count',0))
            rows=progress['rows'] if progress is not None else results.read('state','search_rows',[])
            if len(state['Y'])-initial_count+len(state['rejected'])!=len(rows):
                raise RuntimeError('saved optimizer and measured search rows are inconsistent')
            previous_elapsed=float((progress or {}).get('elapsed_s',0.))
            search_started=time.perf_counter()
            max_elapsed=float(options.get('max_elapsed_s',0.))
            if max_elapsed<0 or not np.isfinite(max_elapsed): raise ValueError('search elapsed limit must be finite and nonnegative')
            if max_elapsed: context['deadline']=time.monotonic()+max(0.,max_elapsed-previous_elapsed)
            ai_pause=None
            # The measured target counts complete J(x), not unmeasured attempts.
            while (len(state['Y'])-initial_count if measured_target else len(rows))<target and len(rows)<max_attempts:
                elapsed=previous_elapsed+time.perf_counter()-search_started
                if max_elapsed and elapsed>=max_elapsed: break
                remaining=min(target-(len(state['Y'])-initial_count if measured_target else len(rows)),max_attempts-len(rows))
                count=min(remaining,int(opt.get('popsize',12))) if method=='cmaes' else min(remaining,int(options.get('parallel_candidates',1)))
                try:
                    points=list(state.get('pending',[])) or search.propose(method,state,study['space'],count=count,options=opt)
                except RuntimeError:
                    if method not in ('llm_bo','llm_agent') or not state.get('unavailable'): raise
                    ai_pause=state['unavailable']
                    results.save_state('search_progress',{'optimizer':search.save_state(state),'rows':rows,
                                       'elapsed_s':previous_elapsed+time.perf_counter()-search_started})
                    break
                proposal_ids=list(state['pending_ids'])
                if not points: raise RuntimeError('optimizer has no proposal while no simulations are pending')
                results.save_state('search_progress',{'optimizer':search.save_state(state),'rows':rows,'elapsed_s':elapsed})
                batch=evaluate_points(study,[{'name':f'{method}_{len(rows)+i:04d}','values':point} for i,point in enumerate(points)],results,context)
                for number,point,row in zip(proposal_ids,points,batch):
                    row['proposal_id']=number
                    row['proposal']=state['proposals'][str(number)]
                    noise=row.get('transport_standard_error') if options.get('conditional_transport_noise',False) else row.get('standard_error')
                    update_options={'se':noise,'proposal_id':number,
                                    'rejected_reason':None if row['status']=='complete' else str(row.get('reasons'))}
                    if options.get('record_runtime',False): update_options.update(elapsed_s=row.get('elapsed_s'),status=row['status'])
                    search.update(method,state,point,row['value'],**update_options)
                    if not study['analysis'].get('store_search_blocks',True): row.pop('blocks',None)
                    rows.append(row)
                for previous in rows:
                    key=str(previous['proposal_id'])
                    previous['proposal']['used_in_optimizer_update']=bool(state['used'].get(key,False))
                results.save_state('search_progress',{'optimizer':search.save_state(state),'rows':rows,
                                   'elapsed_s':previous_elapsed+time.perf_counter()-search_started})
        else:
            rows=evaluate_points(study,candidates,results,context)
        report=analysis.analyse_results(study,rows)
        if study['kind']!='search' and study['analysis'].get('store_blocks',True) is False:
            for row in report['results']: row.pop('blocks',None)
        if study['kind']=='search':
            reached=(len(state['Y'])-initial_count if measured_target else len(rows))>=target
            report['initial_results']=[{**row,'parameters':row['values'],'status':'complete'} for row in initial]
            report['search_counts']={'measured':len(state['Y'])-initial_count,'initial':initial_count,'target':target,'attempts':len(rows),
                                     'max_attempts':max_attempts,'measured_evaluations':measured_target,'target_reached':reached}
            report['search_elapsed_s']=previous_elapsed+time.perf_counter()-search_started
            if measured_target:
                report.update(complete=reached,status='complete' if reached else 'incomplete')
                if not reached: report['incomplete_reason']='elapsed limit or maximum attempts reached before the measured target'
            if method in ('llm_bo','llm_agent'):
                stats=copy.deepcopy(state.get('llm_stats',{})); tool_required=bool(state['options'].get('agent_tools'))
                valid=not state.get('unavailable') and stats.get('calls',0)>0 and stats.get('accepted',0)>0 and (not tool_required or stats.get('tools',0)>0)
                selected=[row for row in rows if row.get('proposal',{}).get('proposal_source','').startswith('llm')]
                report['ai']={'required':True,'valid':bool(valid),'model':state.get('llm_model',opt.get('model')),
                    'digest':state.get('llm_digest',opt.get('model_digest')),'stats':stats,
                    'records':copy.deepcopy(state.get('llm_records',[])),'rejected':copy.deepcopy(state.get('llm_rejected',[])),
                    'selected_llm_points':len(selected),'measured_llm_points':sum(row['status']=='complete' for row in selected),
                    'tools_required':tool_required,'route':state.get('llm_route'),
                    'status':'paused' if ai_pause else 'valid' if valid else 'invalid'}
                if ai_pause or not valid:
                    report.update(complete=False,status='paused',incomplete_reason=ai_pause or 'AI stage has no verified inference, valid proposals or required tool use')
        report.update(build=build,timing={**context['timing'],'total_s':time.perf_counter()-start})
        records.write_json(output/'summary.json',report,overwrite=True)
        results.save_state('summary',report)
    return report


def run_series(study_or_path, output, execute=True):
    """Run complete child studies in order, retaining earlier sites and streams."""
    source=Path(study_or_path).resolve() if not isinstance(study_or_path,dict) else ROOT/'study.yaml'
    raw=records.load_document(study_or_path)
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    identifier=raw.get('id',source.stem); steps=raw.get('steps'); levels=raw.get('levels')
    if bool(steps)==bool(levels): raise ValueError('choose nonempty study steps or refinement levels')
    jobs=[]; signatures=[]; reports=[]
    if steps:
        if not isinstance(steps,list): raise ValueError('study steps must be a list')
        for index,step in enumerate(steps):
            path=(source.parent/step).resolve() if isinstance(step,str) else step
            if path==source: raise ValueError('a study cannot include itself as a step')
            if not isinstance(path,(Path,dict)): raise ValueError('each step must be a study path or mapping')
            child=load_study(path,directory=source.parent)
            child['id']=f"{identifier}-step-{index+1:03d}-{child['id']}"
            jobs.append(child)
    else:
        if raw.get('kind')!='convergence' or not isinstance(levels,list):
            raise ValueError('refinement levels require kind convergence and a list')
        template={key:value for key,value in raw.items() if key not in ('levels','resolved_hash')}
        template['kind']='confirmation'
        previous={'sites':template.get('sampling',{}).get('sites',16),
                  'replicas':template.get('sampling',{}).get('replicas',4),
                  'events_per_task':template.get('event_counts',{}).get('events_per_task',template.get('sampling',{}).get('events_per_task',31250))}
        normalized=[]
        for level in levels:
            if not isinstance(level,dict) or set(level)-set(previous): raise ValueError('unknown refinement count')
            current={**previous,**level}
            if any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in current.values()):
                raise ValueError('refinement counts must be positive integers')
            if normalized and any(current[k]<previous[k] for k in ('sites','replicas')):
                raise ValueError('refinement must retain earlier positions and realizations')
            normalized.append(current); previous=current
        template.setdefault('sampling',{}).update(normalized[0])
        if 'event_counts' in template:
            template['event_counts'].pop('events_total',None)
            template['event_counts']['events_per_task']=normalized[0]['events_per_task']
        initial=load_study(template,directory=source.parent); design=initial['design']
        if len(design['sites_mm'])!=normalized[0]['sites']:
            raise ValueError('the first level must retain every site in its recorded design')
        for index,level in enumerate(normalized):
            sampling={**initial['sampling'],**level}
            if index:
                design=physics.build_design(initial['fixed'],sampling,previous=design)
                design['design_hash']=records.content_hash(design)
            design_path=output/f'design-{index+1:03d}.json'; records.write_json(design_path,design)
            child={**initial,'id':f'{identifier}-level-{index+1:03d}',
                   'sampling':{**sampling,'kind':'recorded-stratified','design_file':str(design_path)},
                   'design':copy.deepcopy(design),'events_total':level['sites']*level['replicas']*level['events_per_task']}
            if 'event_counts' in child:
                child['event_counts']={**child['event_counts'],'events_per_task':level['events_per_task'],'events_total':child['events_total']}
            jobs.append(child)
    for child in jobs:
        child.pop('resolved_hash',None)
        child['resolved_hash']=records.content_hash({key:value for key,value in child.items() if key not in RUNTIME_FIELDS})
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
    command=commands.add_parser('probe-density'); command.add_argument('material'); command.add_argument('--study',default=str(ROOT/'studies/reference.yaml')); command.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    previous_signals={}
    if args.command in ('run','probe-density') and threading.current_thread() is threading.main_thread():
        for kind in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            previous_signals[kind]=signal.signal(kind,signal.default_int_handler)
    try:
        if args.command=='check':
            declared=records.load_document(args.study)
            if declared.get('kind')=='convergence' or declared.get('steps'):
                import tempfile
                with tempfile.TemporaryDirectory(prefix='material-study-check-') as temporary:
                    checked=run_series(args.study,temporary,execute=False)
                if checked['status']!='prepared':
                    failure=next((row.get('reason') for row in checked['studies'] if row.get('status')=='failed'), 'a declared child study is invalid')
                    raise ValueError(failure)
                answer={'id':checked['id'],'kind':checked['kind'],'checked_studies':checked['declared_studies']}
            else:
                study=load_study(args.study)
                for candidate in candidate_table(study): physics.prepare_values(study,candidate)
                answer={'id':study['id'],'kind':study['kind'],'sites':len(study['design']['sites_mm']),'replicas':study['sampling']['replicas'],
                        'events_total':study['events_total'],'search_parameters':study['space']['names']}
        elif args.command=='run':
            output=args.output or ROOT/'material_scan_data/experiments'/Path(args.study).stem
            answer=run_study(args.study,output,execute=not args.prepare_only)
        elif args.command=='analyse': answer=analysis.read_analysis(args.path,args.output,args.settings)
        elif args.command=='compare': answer=analysis.compare_runs(args.first,args.second,args.output,args.margin)
        elif args.command=='plot':
                answer={'plot':str(analysis.plot_result(args.path,args.output,args.kind))}
        elif args.command=='inventory': answer=records.git_inventory(args.path,args.git_repository,args.revision) if args.git_repository else records.inventory(args.path)
        elif args.command=='history': answer=records.history_summary(args.path,args.invalidations)
        elif args.command=='archive': answer=records.create_archive(args.path,args.member,args.output,include_manifest=False)
        elif args.command=='verify-archive': answer=records.verify_archive(args.path,include_manifest=False)
        elif args.command=='recover': answer={'path':str(records.recover_archive(args.path,args.output))}
        elif args.command=='catalog': answer=records.fetch_catalog(args.output,args.ids,args.max_results)
        elif args.command=='project':
            target=records.load_document(args.target); catalog=records.load_document(args.catalog); feature_spec=records.load_document(args.features)
            answer={'target':target,'projection':analysis.project_materials(target.get('features',target),catalog['candidates'],feature_spec,top=args.top),
                    'interpretation':'Distances use only measured fields; missing fields are reported. Simulate shortlisted complete material records before a material conclusion.'}
            records.write_json(args.output,answer)
        elif args.command=='probe-density':
            answer=probe_density(args.material,load_study(args.study),args.output)
        else: answer={'path':str(records.convert_copy(args.path,args.output,args.old_root,args.new_root))}
        if getattr(args,'output',None) and args.command in ('check','inventory','history','verify-archive'):
            records.write_json(args.output,answer)
        display=answer if args.command not in ('run','analyse') else {k:v for k,v in answer.items() if k not in ('results','build','design')}
        print(records.canonical_json(display)); return 0 if answer.get('complete',True) else 1
    except (OSError,ValueError,RuntimeError) as error:
        print(f'error: {error}',file=sys.stderr); return 2
    except KeyboardInterrupt:
        print('interrupted: pending inputs and completed tasks remain available for restart',file=sys.stderr)
        return 130
    finally:
        for kind,handler in previous_signals.items(): signal.signal(kind,handler)


def load_study(path, *, directory=None):
    source=Path(path).resolve() if not isinstance(path,dict) else Path(directory or ROOT)/'study.yaml'
    raw=records.load_document(path)
    cached={name:raw.pop(name) for name in RUNTIME_FIELDS if name in raw}
    parameter_table=cached.get('parameter_table') or records.load_document(ROOT/'parameters.yaml')
    if 'resolved_hash' in raw:
        expected=raw.pop('resolved_hash')
        if records.content_hash(raw)!=expected: raise ValueError('resolved study was changed after recording')
        if raw['events_total']!=len(raw['design']['sites_mm'])*raw['sampling']['replicas']*raw['sampling']['events_per_task']:
            raise ValueError('resolved task event counts disagree')
        study={**raw,'resolved_hash':expected}
    else:
        allowed={'schema','id','kind','description','purpose','parameters','fixed','sampling','analysis','execution','candidates','search','constraints',
                 'installation','realization','materials_file','output','steps','event_counts','physics','build','provenance','tolerance','levels','morris'}
        if set(raw)-allowed:
            raise ValueError(f'unknown study fields: {sorted(set(raw)-allowed)}')
        if raw.get('schema') not in ('material-scan-study-2','material-scan-experiment-1'):
            raise ValueError('unsupported study schema')
        if raw.get('kind','confirmation') not in ('confirmation','materials','search','sensitivity','tolerance','convergence','sequence'):
            raise ValueError('unknown scientific study kind')
        reference = records.load_document(ROOT/'studies/reference.yaml')
        fixed = dict(reference['fixed']); fixed.update(raw.get('fixed',{}))
        if set(raw.get('fixed',{}))-set(reference['fixed']):
            raise ValueError('unknown fixed physical fields')
        phys = raw.get('physics',{})
        for name in ('electrode_x_mm','electrode_y_mm'):
            if name in phys: fixed[name]=phys[name]
        fixed['setTopGap']=phys.get('junction_gap_eV',fixed['setTopGap'])
        install={'executable':str(Path.home()/'geant4_workdir/bin/Linux-g++/Main'),
                 'lattice_data':str(Path.home()/'src/G4CMP_htseng/CrystalMaps'),
                 'environment_scripts':['/home/software/Geant4/Geant4-Install/share/Geant4-10.7.4/geant4make/geant4make.sh',str(Path.home()/'src/G4CMP_htseng/g4cmp_env.sh')],
                 'library_dir':str(Path.home()/'geant4_workdir/lib/Linux-g++'),
                 'macro_template':str(ROOT/'legacy/macros/sensitivity_template_beamOn1e6.mac')}
        install.update(raw.get('installation',{}))
        for name in ('executable','lattice_data','macro_template','library_dir'):
            value=Path(install[name]).expanduser()
            install[name]=str(value if value.is_absolute() else source.parent/value)
        install['environment_scripts']=[str((source.parent/Path(p).expanduser()).resolve()) if not Path(p).expanduser().is_absolute() else str(Path(p).expanduser()) for p in install.get('environment_scripts',[])]
        sampling=dict(reference['sampling']); sampling.update(raw.get('sampling',{}))
        if not isinstance(sampling.get('independent_candidate_streams',False),bool):
            raise ValueError('independent_candidate_streams must be boolean')
        if 'seed' in sampling:
            sampling['seed_base']=sampling['seed']['base']; sampling['seed_bank']=sampling['seed']['bank_id']
        sampling['events_per_task']=raw.get('event_counts',{}).get('events_per_task',sampling['events_per_task'])
        fixed['gun_energy_eV']=raw.get('event_counts',{}).get('gun_energy_eV',fixed['gun_energy_eV'])
        if 'design_file' in sampling:
            sampling['design_file']=str((source.parent/sampling['design_file']).resolve())
        for name in ('replicas','events_per_task','seed_base','seed_bank','position_seed'):
            value=sampling[name]
            if isinstance(value,bool) or not isinstance(value,int) or value < (1 if name in ('replicas','events_per_task') else 0):
                raise ValueError(f'{name}: invalid integer')
        execution=dict(reference['execution']); execution.update(raw.get('execution',{}))
        if isinstance(execution['workers'],bool) or not isinstance(execution['workers'],int):
            raise ValueError('workers must be a positive integer')
        if execution['workers']<1: raise ValueError('workers must be positive')
        for name in ('timeout_s','stall_timeout_s','per_task_mem_gb','total_mem_gb','system_mem_reserve_gb'):
            if name not in execution: continue
            value=execution[name]
            zero_allowed=name.endswith('_s')
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value) or value<0 or (value==0 and not zero_allowed):
                raise ValueError(f'{name}: expected a finite positive memory limit or nonnegative time limit')
        parameters=copy.deepcopy(raw.get('parameters',{}))
        if 'temperature_K' in raw.get('fixed',{}) and 'temperature' not in parameters:
            parameters['temperature']={'mode':'fixed','value':fixed['temperature_K']}
        space=physics.search_space(parameters,kind=raw.get('kind','confirmation'),constraints=raw.get('constraints'),table=parameter_table['parameters'])
        if 'temperature' not in raw.get('parameters',{}):
            space['baseline']['temperature']=fixed['temperature_K']
        elif 'temperature_K' in raw.get('fixed',{}):
            if 'temperature' in space['names'] or space['baseline']['temperature']!=fixed['temperature_K']:
                raise ValueError('fixed temperature and parameter temperature disagree')
        space.update(junction_gap_eV=fixed['setTopGap'],gun_energy_eV=fixed['gun_energy_eV'],cutoff_eV=fixed['min_e_phonons_eV'])
        study={**raw,'id':raw.get('id',source.stem),'kind':raw.get('kind','confirmation'),'parameters':parameters,'fixed':fixed,'space':space,'installation':install,
               'sampling':sampling,'execution':execution,'analysis':{**reference['analysis'],**raw.get('analysis',{})}}
        if study['kind']=='sensitivity' and 'objective' not in raw.get('analysis',{}):
            study['analysis']['objective']='integrated_density'
        if study['analysis']['objective'] not in ('total_qps_per_primary','total_QPs_per_primary','junction_qps_per_primary',
                                                 'device_junction_qps_per_eV','device_weighted_junction_qps_per_energy','integrated_density'):
            raise ValueError('unknown physical objective')
        study['realization']=raw.get('realization',phys.get('realization',{}))
        if 'materials_file' in raw:
            study['materials_file']=str((source.parent/raw['materials_file']).resolve())
        recorded=records.load_document(sampling['design_file']) if sampling['kind']=='recorded-stratified' else None
        study['design']=physics.build_design(fixed,sampling,recorded=recorded)
        study['design'].setdefault('design_hash',records.content_hash(study['design']))
        count=len(study['design']['sites_mm'])*sampling['replicas']*sampling['events_per_task']
        if raw.get('event_counts',{}).get('events_total',count)!=count:
            raise ValueError('declared total primary count differs from task grid')
        study['events_total']=count
        initial_file=study.get('search',{}).get('initial_observations_file')
        if initial_file:
            initial_path=Path(initial_file).expanduser()
            if not initial_path.is_absolute(): initial_path=source.parent/initial_path
            shared=records.load_document(initial_path)
            if any(shared.get(key)!=value for key,value in
                   [('objective',study['analysis']['objective']),('design_hash',study['design']['design_hash']),
                    ('events_per_task',sampling['events_per_task']),('replicas',sampling['replicas'])]):
                raise ValueError('shared initial observations differ in objective or sampling')
            observations=shared.get('observations',[])
            if not observations: raise ValueError('shared starting measurements are empty')
            for observation in observations:
                point={**space['baseline'],**observation['values']}
                if any(not np.isclose(point[name],value,rtol=1e-12,atol=1e-14)
                       for name,value in space['baseline'].items() if name not in space['names']):
                    raise ValueError('shared initial observation changes a fixed physical property')
                if observation.get('objective',study['analysis']['objective'])!=study['analysis']['objective']:
                    raise ValueError('shared initial observation objective differs')
            study['search']=copy.deepcopy(study['search'])
            study['search'].update(initial_observations=observations,initial_observations_file=str(initial_path.resolve()),
                                   initial_observations_sha256=records.sha256_file(initial_path))
        study['resolved_hash']=records.content_hash(study)

    catalog=cached.get('material_catalog') or records.load_document(study.get('materials_file',ROOT/'legacy/material_catalog.yaml'))
    names={'Si'}
    candidates=study.get('candidates') or [{}]
    if study['kind']=='materials' and not study.get('candidates'):
        names.update(row['lattice_map'] for row in catalog['substrates'].values() if row.get('enabled'))
    for candidate in candidates:
        realization={**study.get('realization',{}),**candidate.get('realization',{})}
        linked=candidate.get('materials')
        material=linked[0] if isinstance(linked,list) else linked.split('/')[0] if linked else realization.get('material')
        if material in catalog['substrates']: names.add(catalog['substrates'][material]['lattice_map'])
        elif realization.get('mode')=='custom_material': names.add(realization.get('lattice_map','Si'))
        else: names.add(realization.get('base_lattice_map','Si'))
    lattices=cached.get('lattices')
    if lattices is None or not names<=set(lattices):
        lattices=records.material_inputs(catalog,study['installation']['lattice_data'],names)['lattices']
    study.update(material_catalog=catalog,lattices=lattices,parameter_table=parameter_table)
    for observation in study.get('search',{}).get('initial_observations',[]):
        physics.prepare_values(study,{'values':observation['values']})
    return study


def task_plan(study, prepared, build_identity):
    """Declare every (i,r) task with N_p primaries, source position and random seeds."""
    sampling=study['sampling']; tasks=[]
    excluded=('ode','trajectory','design_index','transport_group','stream_group','problem','parameter_specs','source_text','input_text','proposal_id')
    transport={key:value for key,value in prepared.items() if key not in excluded}
    encoded=records.canonical_json({'build':build_identity,'physics':transport})[:-1]+',"task":'
    prefix=hashlib.sha256(encoded.encode())
    for r in range(sampling['replicas']):
        for i,site in enumerate(study['design']['sites_mm']):
            seed=(sampling['seed_base']+sampling['seed_bank']*7919003+prepared.get('trajectory',0)*1000003+r*10007+i*101)%900000000+1
            seeds=[seed,seed+1]
            if sampling.get('independent_candidate_streams',False):
                group=prepared.get('stream_group',records.content_hash(prepared['values']))
                digest=hashlib.sha256(records.canonical_json([sampling['seed_base'],sampling['seed_bank'],group,r,i]).encode()).digest()
                seeds=[int.from_bytes(digest[j:j+8],'big')%900000000+1 for j in (0,8)]
            raw={'position':i,'replica':r,'site_mm':site,'seeds':seeds,'events':sampling['events_per_task'],
                 'stratum':study['design']['stratum'][i]}
            digest=prefix.copy(); digest.update((records.canonical_json(raw)+'}').encode())
            raw['task_id']=digest.hexdigest(); tasks.append(raw)
    return tasks


def candidate_table(study):
    kind = study['kind']; specified = study.get('candidates')
    if kind == 'materials' and not specified:
        catalog = study['material_catalog']
        names = [[k for k, v in catalog[section].items() if v.get('enabled')]
                 for section in ('substrates', 'top_films', 'bottom_films')]
        specified = [{'name': '/'.join(row), 'materials': list(row)} for row in itertools.product(*names)]
    if kind == 'tolerance':
        specified = specified or [{'name': 'reference', 'values': {}}]
        base = copy.deepcopy(specified[0])
        point = {**study['space']['baseline'], **base.get('values', {})}
        specified = [base]
        table = study['parameter_table']['parameters']
        skipped = []
        for name, fraction in study.get('tolerance', {}).items():
            if name not in table or name not in point or not 0 < float(fraction) < 1:
                raise ValueError('tolerance needs known parameters and fractions in (0,1)')
            spec = study.get('parameters', {}).get(name, {})
            spec = spec if isinstance(spec, dict) else {}
            lo, hi = map(float, spec.get('bounds', spec.get('range', table[name]['search_range'])))
            scale = spec.get('scale', table[name].get('scale', 'linear'))
            interval = {'names': [name], 'low': [lo], 'high': [hi], 'scales': [scale],
                        'baseline': point, 'constraints': study['space'].get('constraints', {})}
            centre = float(physics.to_unit(point, interval)[0])
            for sign in (-1, 1):
                u = float(np.clip(centre + sign * float(fraction), 0., 1.))
                label = f'{name}_{sign:+d}'
                if abs(u - centre) < 1e-9:
                    skipped.append({'name': label, 'reason': 'already at this search bound'})
                    continue
                moved = physics.from_unit([u], interval)
                try:
                    physics.validate_values(moved, study['space'], exact=False)
                except ValueError as exc:
                    skipped.append({'name': label, 'reason': str(exc)})
                    continue
                specified.append({**copy.deepcopy(base), 'name': label, 'values': moved,
                                  'tolerance': {'variable': name, 'sign': sign,
                                                'fraction': float(fraction), 'unit_delta': u - centre}})
        study['tolerance_skipped'] = skipped
    if kind == 'sensitivity':
        from SALib.sample.morris import sample
        table = study['parameter_table']
        rows, options = table['morris'], study.get('morris', {})
        entries = [row for section in ('electrode_params', 'detector_params', 'G4CMP_params',
                                      'config_params', 'QPDE_params') for row in rows[section]]
        requested = options.get('parameters')
        if requested:
            requested = {name.strip() for name in requested}
            unknown = requested - {row[0].strip() for row in entries}
            if unknown:
                raise ValueError(f'unknown Morris parameters: {sorted(unknown)}')
            entries = [row for row in entries if row[0].strip() in requested]
        if not entries:
            raise ValueError('no sensitivity parameters selected')
        names = [row[0].strip() for row in entries]
        problem = {'num_vars': len(names), 'names': names, 'bounds': [row[2] for row in entries]}
        trajectories = int(options.get('trajectories', 4)); levels = int(options.get('levels', 4))
        seed = int(options.get('seed', 41))
        if trajectories < 1 or levels < 2 or levels % 2:
            raise ValueError('positive trajectory count and an even number of Morris levels required')
        length = len(names) + 1
        candidate_count = 2 * trajectories
        baseline = study['space']['baseline']
        while True:
            raw = sample(problem, candidate_count, num_levels=levels, seed=seed)
            paths = raw.reshape(candidate_count, length, len(names))
            stiffness = []
            for command, canonical in (('stiffness 1 1', 'sub_c11'), ('stiffness 1 2', 'sub_c12'),
                                       ('stiffness 4 4', 'sub_c44')):
                stiffness.append(paths[:, :, names.index(command)] if command in names
                                 else np.full(paths.shape[:2], baseline[canonical]))
            c11, c12, c44 = stiffness
            valid = np.all((c11 > c12) & (c11 + 2 * c12 > 0) & (c44 > 0), axis=1)
            if int(valid.sum()) >= trajectories:
                samples = paths[valid][:trajectories].reshape(trajectories * length, len(names))
                break
            candidate_count *= 2
            if candidate_count > int(options.get('max_candidate_trajectories', 1048576)):
                raise ValueError('too few complete Born-stable Morris trajectories within the requested box')
        destinations = {row['target']: name for name, row in table['parameters'].items()}
        fixed_names = {'/main/electrode_param/setHeight': 'electrode_height_um',
                       '/main/electrode_param/setWidth': 'electrode_width_um',
                       '/main/electrode_param/setIsland': 'electrode_island_um',
                       '/main/electrode_param/setIslandSpacing': 'electrode_island_spacing_um'}
        specs = [{'name': name, 'bounds': list(row[2]), 'unit': row[3].strip() if len(row) > 3 else '',
                  'destination': 'postprocess' if name in ('f_01', 'r', 's', 'I_ph', 'pt', 'n_cooper')
                                 else 'macro' if name.startswith('/') else 'lattice'}
                 for name, row in zip(names, entries)]
        study['morris_design'] = {'problem': problem, 'parameter_specs': specs, 'seed': seed,
                                  'levels': levels, 'trajectories': trajectories,
                                  'candidate_trajectories': candidate_count,
                                  'rejected_trajectories': candidate_count - int(valid.sum())}
        specified = []
        for i, sample_row in enumerate(samples):
            values, macro, config, ode, fixed = {}, {}, {}, {}, {}
            for name, entry, value in zip(names, entries, sample_row):
                value = float(value)
                unit = entry[3].strip() if len(entry) > 3 else ''
                if name in ('f_01', 'r', 's', 'I_ph', 'pt', 'n_cooper'):
                    ode[name] = value
                    continue
                if name.startswith('/'):
                    macro[name] = f'{value:.12g}' + (f' {unit}' if unit else '')
                    target = 'macro:' + name
                    if name in fixed_names:
                        fixed[fixed_names[name]] = value
                else:
                    if unit.startswith('e-'):
                        exponent, unit = unit.split(' ', 1)
                        value *= float('1' + exponent)
                    config[name] = [value, unit]
                    target = 'config:' + name
                if target in destinations:
                    values[destinations[target]] = value
            transport = {'values': values, 'macro': macro, 'config': config, 'fixed': fixed,
                         'trajectory': i // length}
            specified.append({'name': f'morris_{i:04d}', **transport, 'ode': ode,
                              'inputs': dict(zip(names, map(float, sample_row))),
                              'design_index': i, 'transport_group': records.content_hash(transport)})
    specified = specified or [{'name': 'reference', 'values': {}}]
    if len({point.get('name') for point in specified}) != len(specified):
        raise ValueError('candidate names must be unique')
    return specified


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
    for key,value in installation.get('data_paths',{}).items():
        path=Path(value).resolve()
        if not key.startswith('G4') or 'DATA' not in key or not path.is_dir():
            raise ValueError('simulator data overrides require existing G4 data directories')
        env[key]=str(path)
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


def verify_previous_inputs(study, candidates, output, build):
    """Retain an earlier transport identity only after exact input comparison."""
    path=output/'build.json'
    if not path.exists(): return build
    previous=records.load_document(path)
    if previous==build: return build
    changed={key for key in previous.keys()|build.keys() if previous.get(key)!=build.get(key)}
    if changed!={'renderer_sha256'} or candidates is None:
        raise ValueError('simulator/build changed; use a new output directory')
    checked=[]
    with records.Results(output/'results.sqlite',create=False) as results:
        saved={row['task_id']:row for row in results.tasks()}
        for candidate in candidates:
            prepared=physics.prepare_values(study,candidate)
            prepared.update({key:candidate[key] for key in ('macro','config','ode','trajectory') if key in candidate})
            lattice,base=input_text(prepared,study['installation'])
            for task in task_plan(study,prepared,previous):
                old=saved.get(task['task_id'])
                if old is None or old['input']!=task or old['status']!='complete' or not old.get('result',{}).get('paths'):
                    raise ValueError('earlier task inputs are unavailable for source comparison; use a new output directory')
                paths=old['result']['paths']
                if not old['result'].get('sha256') or not Path(old['result'].get('output','')).is_file() or not Path(paths['witness']).is_file():
                    raise ValueError('earlier measured output or completion marker is unavailable')
                config=Path(paths['lattice_root'])/prepared['derived']['lattice_map_name']/'config.txt'
                macro=Path(paths['macro'])
                for kind,source in (('macro',macro),('lattice',config)):
                    if not source.is_file() or records.sha256_file(source)!=paths['input_sha256'][kind]:
                        raise ValueError(f'earlier {kind} input differs from its recorded checksum')
                recorded=macro.read_text()
                output_command=next(row.split(None,1)[1] for row in recorded.splitlines() if row.startswith('/g4cmp/HitsFile '))
                completion=next(row.split(None,1)[1] for row in recorded.splitlines() if row.startswith('/control/shell '))
                if len(shlex.split(completion))!=2 or shlex.split(completion)[0]!='touch':
                    raise ValueError('earlier completion command is not a single touch')
                text=replace_commands(base,{'/g4cmp/HitsFile':output_command,
                    '/main/gun/setPosition':' '.join(map(str,task['site_mm']))+' mm','/run/beamOn':str(task['events'])})
                block=f"/random/setSeeds {task['seeds'][0]} {task['seeds'][1]}\n/run/beamOn {task['events']}\n/control/shell {completion}"
                text=text.replace('/run/beamOn '+str(task['events']),block)
                if recorded!=text or config.read_text()!=lattice:
                    raise ValueError('rendered physical inputs differ; use a new output directory')
                checked.append({'task_id':task['task_id'],'input_sha256':paths['input_sha256']})
    if not checked: raise ValueError('no earlier physical inputs are available for source comparison')
    provenance={'previous_build':previous,'current_source_build':build,'checked_tasks':checked,
                'agreement':'identical executable, libraries, template, data paths, lattice and macro bytes; identical sites, seeds and primary counts'}
    records.write_json(output/('source-comparison-'+build['renderer_sha256']+'.json'),provenance)
    return previous


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


def input_text(prepared, installation):
    """Render the candidate lattice and common macro once before task dispatch."""
    derived=prepared['derived']; point=prepared['values']; fixed=prepared['fixed']
    top,bottom=prepared['top_film'],prepared['bottom_film']
    text=prepared['source_text']
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
    template=installation.get('template_text') or Path(installation['macro_template']).read_text()
    return text,replace_commands(template,commands)


def render_inputs(prepared, task, installation, directory):
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=False)
    derived=prepared['derived']; lattice_dir=directory/'CrystalMaps'/derived['lattice_map_name']
    lattice_dir.mkdir(parents=True)
    lattice,base=prepared.get('input_text') or input_text(prepared,installation)
    (lattice_dir/'config.txt').write_text(lattice)
    output=directory/'hits.csv'; witness=directory/'complete'; macro=directory/'run.mac'; log=directory/'run.log'
    text=replace_commands(base,{'/g4cmp/HitsFile':str(output),'/main/gun/setPosition':' '.join(map(str,task['site_mm']))+' mm','/run/beamOn':str(task['events'])})
    block=f"/random/setSeeds {task['seeds'][0]} {task['seeds'][1]}\n/run/beamOn {task['events']}\n/control/shell touch {shlex.quote(str(witness))}"
    text=text.replace('/run/beamOn '+str(task['events']),block)
    validate_macro(text); macro.write_text(text)
    return {'directory':str(directory),'macro':str(macro),'output':str(output),'witness':str(witness),'log':str(log),
            'lattice_root':str(directory/'CrystalMaps'),'expected_material':derived['g4_material_name'],
            'expected_density_kg_m3':derived['substrate_density_kg_m3'],'density_probe':derived.get('density_probe',False),
            'input_sha256':{'macro':records.sha256_file(macro),'lattice':records.sha256_file(lattice_dir/'config.txt')}}


def process_stats(pid, *, children=True):
    if not Path('/proc/self/stat').exists(): return None
    try:
        if int(Path('/proc/self/stat').read_text().split(' ',1)[0])!=os.getpid(): return None
        queue=[pid]; ticks=0; memory=0
        while queue:
            current=queue.pop()
            fields=Path(f'/proc/{current}/stat').read_text().rsplit(')',1)[1].split()
            ticks+=int(fields[11])+int(fields[12]); memory+=int(fields[21])*os.sysconf('SC_PAGE_SIZE')
            child=Path(f'/proc/{current}/task/{current}/children')
            if children and child.exists(): queue.extend(map(int,child.read_text().split()))
        return ticks,memory
    except (OSError,ValueError,IndexError): return None


def stop_process(process):
    if process.poll() is not None: return
    try: os.killpg(process.pid,signal.SIGKILL)
    except (ProcessLookupError,PermissionError): process.kill()


def memory_headroom():
    """Host/cgroup available bytes; only inactive file cache receives credit."""
    available=None; limiting=None; raw_available=None
    for row in Path('/proc/meminfo').read_text().splitlines():
        if row.startswith('MemAvailable:'): available=int(row.split()[1])*1024; break
    for row in Path('/proc/self/cgroup').read_text().splitlines():
        if not row.startswith('0::'): continue
        root=Path('/sys/fs/cgroup'); leaf=root/row.split('::',1)[1].lstrip('/')
        for path in (leaf,*leaf.parents):
            if path!=root and root not in path.parents: continue
            maximum,current=path/'memory.max',path/'memory.current'
            if not maximum.is_file() or not current.is_file(): continue
            value=maximum.read_text().strip()
            if value=='max': continue
            usage=int(current.read_text()); residual=max(0,int(value)-usage)
            raw_available=residual if raw_available is None else min(raw_available,residual)
            stat=path/'memory.stat'; inactive=0
            if stat.is_file():
                for item in stat.read_text().splitlines():
                    if item.startswith('inactive_file '): inactive=max(0,min(usage,int(item.split()[1]))); break
            residual=max(0,int(value)-usage+inactive)
            if available is None or residual<available: available,limiting=residual,str(path)
    return {'available_bytes':available,'limiting_cgroup':limiting,
            'raw_unreclaimed_headroom_bytes':raw_available if raw_available is not None else available}


def monitor_processes(active,lock,limits,stop_event=None):
    now=time.monotonic()
    with lock:
        rows=list(active.values())
        parent=process_stats(os.getpid(),children=False)
        total=parent[1] if parent else 0
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
        headroom=memory_headroom()
        if headroom['available_bytes'] is not None and headroom['available_bytes']<limits.get('system_mem_reserve_gb',16)*1024**3:
            if stop_event is not None: stop_event.set()
            for row in rows:
                row['stopped_reason']='system_memory_limit'; stop_process(row['process'])
            raise RuntimeError('host or cgroup memory reserve reached; restart after memory is available')


def run_task(task, prepared, installation, environment, directory, active=None, lock=None, stop_event=None):
    paths=render_inputs(prepared,task,installation,directory)
    env=dict(environment); env['G4LATTICEDATA']=paths['lattice_root']
    active={} if active is None else active; lock=lock or threading.Lock()
    started=time.monotonic(); row={**paths,'started':started,'last_change':started}
    with open(paths['log'],'xb') as log:
        with lock:
            if stop_event is not None and stop_event.is_set():
                return {'status':'interrupted','reason':'launch stopped','elapsed_s':time.monotonic()-started,'paths':paths}
            process=subprocess.Popen([installation['executable'],paths['macro']],cwd=paths['directory'],env=env,
                                     stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            row['process']=process
            active[task['task_id']]=row
        try: returncode=process.wait()
        finally:
            with lock: active.pop(task['task_id'],None)
    elapsed=time.monotonic()-started
    if row.get('stopped_reason'):
        return {'status':'timeout' if row['stopped_reason'] in ('timeout','stalled') else 'failed','reason':row['stopped_reason'],
                'elapsed_s':elapsed,'peak_rss_bytes':row.get('peak_rss_bytes'),'paths':paths,'returncode':returncode}
    try:
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
        result={'output':str(path),'sha256':records.sha256_file(path),'bytes':path.stat().st_size,
                'runtime_material':match[1],'runtime_density_kg_m3':float(match[2])*1000,
                'teardown_fault':returncode!=0}
    except (ValueError,OSError) as exc:
        return {'status':'invalid','reason':str(exc),'elapsed_s':elapsed,'peak_rss_bytes':row.get('peak_rss_bytes'),'paths':paths,'returncode':returncode}
    return {'status':'complete','elapsed_s':elapsed,'returncode':returncode,'peak_rss_bytes':row.get('peak_rss_bytes'),
            'paths':paths,**result}


def probe_density(material, study, output):
    """Read a named Geant4 density; this diagnostic does not model that compound."""
    from concurrent.futures import ThreadPoolExecutor
    if not re.fullmatch(r'G4_[A-Za-z0-9_]+',material):
        raise ValueError('expected one named Geant4 material')
    root=Path(output).resolve(); root.mkdir(parents=True,exist_ok=True)
    prepared=physics.prepare_values(study)
    prepared['derived'].update(g4_material_name=material,density_probe=True)
    environment,build=prepare_environment(study['installation'])
    task={'task_id':'density-probe','events':1,'seeds':[41,42],'site_mm':study['design']['sites_mm'][0]}
    active={}; lock=threading.Lock(); stop_event=threading.Event()
    pool=ThreadPoolExecutor(max_workers=1); future=None
    try:
        future=pool.submit(run_task,task,prepared,study['installation'],environment,root/'attempt',active,lock,stop_event)
        while not future.done():
            monitor_processes(active,lock,{'timeout_s':60,'stall_timeout_s':30,'per_task_mem_gb':2,'total_mem_gb':2},stop_event)
            time.sleep(.1)
        result=future.result()
    finally:
        close_workers(pool,[future] if future is not None else [],active,lock,stop_event)
    if result['status']!='complete':
        raise RuntimeError('density probe failed: '+result.get('reason','unknown'))
    report={'material':material,'density_kg_m3':result['runtime_density_kg_m3'],'build':build,
            'log':result['paths']['log'],'scope':'Density measurement only; the Si lattice is a diagnostic placeholder.'}
    records.write_json(root/'density.json',report)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
