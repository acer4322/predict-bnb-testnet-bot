"""Freeze V27 RAW/LEGAL maintenance scopes with V26 restore Active unchanged."""
import json
import math
import shutil
from prepare_btc5m_reexposure_coordination_v1 import ROOT,R,START,read,sha,once,compile_source,get,RET,dump_for,load
import btc5m_repair_maintenance_scope_v1 as scope

PARENT=ROOT/'.lan_worker_v1/fixed15_reexposure_restore_2026085_20260913_v1'
BASE=RET/'fixed15-core-loop-2026085-reexposure-restore-20260913-v1'
ARMS=dict(raw='RAW',legal='LEGAL')


def config(arm):
    assert arm in ARMS
    return dict(PACKAGE=ROOT/f'.lan_worker_v1/fixed15_maintenance_{arm}_2026085_20260913_v1',
        JOB=f'fixed15-core-loop-2026085-maintenance-{arm}-20260913-v1',
        STEM=f'BTC5M_MAINTENANCE_{arm.upper()}_V1_20260913',WAVE=f'maintenance_{arm}_wave_20260913.json')


def observation(result,trace,source,base_module):
    active=trace['coordination_submissions'][0]['key']
    terminal=min(x['t'] for x in trace['demand_owner_rows'] if x['key']==active and x['state']=='TERMINAL' and x['filled']>0)
    books={x['received_ms']:x for x in source['books']};owners={o['key']:o for o in trace['demand_final']['all_final_carriers']}
    extra={(x['t'],x['key']):x for x in trace['commitment_maintenance_rows']}
    threshold=.01*(1+math.log1p(math.exp(result['theta'][11])))
    rows=[];first=None;first_observation=None
    for m in trace['demand_maintenance_rows']:
        t=m['t'];key=m['key'];o=owners[key]
        if t<terminal or m['side']!='DOWN' or o['route']!='PASSIVE':continue
        old=extra.get((t,key));raw=old['quote']['raw_price'] if old else m['current_passive_price']
        book=books[t];assert book['source_ms']<=t
        row=scope.decide(raw,round(1-book['best_bid'],10),o['limit'],threshold,old is not None,base_module.legal_quote)
        assert row['original_stale']==m['stale'] and abs(row['original_price']-m['current_passive_price'])<1e-8
        effect=row['changed_stale'] and m['cancellable'] and m['owner_state']!='CANCEL_PENDING' and not m['surplus']
        row.update(t=t,key=key,seconds=(t-START)/1000,limit=o['limit'],threshold=threshold,is_extra=old is not None,
            baseline_maintenance=m,changes_cancel=effect,book_source_ms=book['source_ms'])
        if first_observation is None and row['changed_price']:first_observation=t
        if first is None and effect:first=row
        rows.append(row)
    assert first is not None, 'No exercisable maintenance change; do not replay'
    return dict(status='PASS',gate_key=active,gate_terminal_t=terminal,first=first,first_observation_t=first_observation,
        matched_baseline_rows=rows,selection='First executable stale decision change after canonical terminal of the same V26 additional Active; no event time passed to actor.')


def main():
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert read(R/'BTC5M_REEXPOSURE_RESTORE_V1_20260913_RESULT.json')['verification']=='PASS'
    assert all(not config(a)['PACKAGE'].exists() for a in ARMS), 'Existing package: do not rebuild'
    base,trace=get(BASE);input_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(input_path)==parent['oracle_source_sha256'];source=read(input_path)
    base_module=load('v26_maintenance_parent',PARENT/'commitment_repair.py');assert base_module.CONCURRENT and base_module.LEGAL_QUOTE
    component=scope.self_test(base_module);parent_source=compile_source(PARENT);prepared={}
    for arm,mode in ARMS.items():
        scope.MODE=mode;obs=observation(base,trace,source,base_module);c=config(arm);p=c['PACKAGE'];stem=c['STEM']
        dump_for(stem,'OBSERVATION',obs);dump_for(stem,'COMPONENT',component)
        module=once((ROOT/'tools/btc5m_repair_maintenance_scope_v1.py').read_text(encoding='utf-8'),"MODE='RAW'",'MODE='+repr(mode))
        runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
        runner=once(runner,'    source=coordination.instrument(source,replace)',
            "    source=coordination.instrument(source,replace)\n    maintenance_scope=load('maintenance_scope',package/'maintenance_scope.py')\n    source=maintenance_scope.instrument(source,replace)")
        runner=once(runner,'_CommitmentRepairProbe=commitment_repair.CommitmentRepairProbe,',
            '_CommitmentRepairProbe=maintenance_scope.make_probe(commitment_repair),')
        runner=once(runner,'commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,',
            'maintenance_scope_rows=producer.commitment_repair.scope_rows,commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,')
        p.mkdir()
        for name in parent['files']:
            if name=='money_runner.py':(p/name).write_text(runner,encoding='utf-8')
            else:shutil.copy2(PARENT/name,p/name)
        (p/'maintenance_scope.py').write_text(module,encoding='utf-8')
        m=dict(parent)
        m.update(version=stem,parent_manifest_sha256=sha(PARENT/'manifest.json'),existing_control=str(BASE.relative_to(ROOT)),
            baseline_result_sha256=sha(BASE/'result.json'),baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),
            observation_sha256=sha(R/(stem+'_OBSERVATION.json')),maintenance_scope_mode=mode,
            maximum_native_jobs=1,panel_maximum_native_jobs=2,
            scope_gate='Additional V26 Active canonical TERMINAL with positive filled quantity; no time or Target input',
            maintenance_scope='All DOWN PASSIVE owners after gate; RAW price' if mode=='RAW' else 'All DOWN PASSIVE owners after gate; fixed15 legal price if strictly below current ask',
            unchanged='16 parent modules byte-identical, original and additional Active quantities and trigger frozen, all NEW and UP logic unchanged. Surplus, cancellability and reservation lifecycle unchanged. Endogenous later plans can differ.',
            evaluation=['Same both-Active observations, submissions and receipts; full prefix before maintenance effect',
                'Per-owner RAW/LEGAL scope and actual cancel/keep audit with pending reservations retained',
                'All raw and canonical receipt accounting; DOWN repair and UP addition flow changes',
                'Compare two new maintenance scopes against reused V26 restore; no fitted parameters'],
            limitations='Consumed one-market mechanism isolation, after fixed restore Active. Not new direction or recurrent repair policy; no held-out Target identification.',
            files={f.name:sha(f) for f in p.glob('*.py')})
        (p/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
        transformed=compile_source(p)
        for a,b in [(str(p).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(p),str(PARENT)),(p.as_posix(),PARENT.as_posix())]:transformed=transformed.replace(a,b)
        transformed=once(transformed,';self.commitment_repair.coordinator=self.coordination','')
        assert transformed==parent_source, 'Undeclared generated-source change'
        frozen=load('checked_maintenance_'+arm,p/'maintenance_scope.py');assert frozen.MODE==mode
        assert frozen.self_test(base_module)['status']=='PASS'
        dump_for(stem,'LOCAL_PREFLIGHT',dict(status='PASS',unchanged_parent_modules=16,generated_source_one_attachment_only=True,local_native=0))
        dump_for(stem,'PREREGISTERED',m)
        wave=dict(progress_artifact='data/research/MAINTENANCE_SCOPE_PROGRESS_20260913.json',jobs=[dict(job_id=c['JOB'],
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{p.name}/money_runner.py','--mode','ORACLE_UP',
                '--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/c['WAVE']).write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
        prepared[arm]=dict(job_id=c['JOB'],package=str(p.relative_to(ROOT)),manifest_sha256=sha(p/'manifest.json'),
            mode=mode,first_observation_seconds=(obs['first_observation_t']-START)/1000,first_cancel_change=obs['first'])
    panel=dict(status='FROZEN',arms=prepared,new_native_jobs_planned=2,reused_native_controls=1,local_native_jobs=0,model_fits=0,parameter_search=0)
    dump_for('BTC5M_MAINTENANCE_SCOPE_PANEL_V1_20260913','PREREGISTERED',panel)
    print(json.dumps(dict(status='FROZEN',arms={arm:{k:v for k,v in a.items() if k!='first_cancel_change'} | dict(first_cancel_seconds=a['first_cancel_change']['seconds']) for arm,a in prepared.items()})))


if __name__=='__main__':main()
