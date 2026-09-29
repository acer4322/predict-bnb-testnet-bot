"""Freeze a three-arm repair correction panel, reusing the completed V24 cell."""
import argparse
import json
import shutil
from prepare_btc5m_commitment_repair_probe_v1 import ROOT,R,START,read,sha,once,compile_source,get,RET
from prepare_btc5m_unbudgeted_opportunity_v1 import load
from hft244_pair_route_legality_v1 import crossing_owners
import btc5m_repair_constraints_v1 as candidate

PARENT=ROOT/'.lan_worker_v1/fixed15_commitment_repair_2026085_20260913_v1'
BASE=RET/'fixed15-core-loop-2026085-commitment-repair-20260913-v1'
ARMS=dict(concurrent=(True,False),quote=(False,True),both=(True,True))


def config(arm):
    assert arm in ARMS
    return dict(PACKAGE=ROOT/f'.lan_worker_v1/fixed15_repair_{arm}_2026085_20260913_v1',
        JOB=f'fixed15-core-loop-2026085-repair-{arm}-20260913-v1',
        STEM=f'BTC5M_REPAIR_CONSTRAINTS_{arm.upper()}_V1_20260913',
        WAVE=f'repair_constraints_{arm}_wave_20260913.json')


def dump_for(stem,suffix,obj):
    (R/(stem+'_'+suffix+'.json')).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def first_difference(trace,concurrent,quote):
    candidate.CONCURRENT=concurrent;candidate.LEGAL_QUOTE=quote
    for row in trace['commitment_repair_rows']:
        d=candidate.decide(row['state'],row['original_operations'],row['price'],row['ask'],row['active_confirmed'],row['outstanding'],crossing_owners)
        if d['eligible']!=row['eligible'] or (d['eligible'] and d['price']!=row['price']):
            return dict(t=row['t'],seconds=(row['t']-START)/1000,baseline=row,candidate=d,
                        selection='First causal decision difference on unchanged V24 prefix; no timestamp supplied to actor')
    raise AssertionError('No difference: do not replay an unchanged arm')


def main():
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    baseline,trace=get(BASE)
    assert read(R/'BTC5M_COMMITMENT_REPAIR_V1_20260913_RESULT.json')['verification']=='PASS'
    checked=candidate.self_test(crossing_owners)
    # All three experiments are preregistered before any native outcomes are seen.
    assert all(not (config(a)['PACKAGE']/'manifest.json').exists() for a in ARMS), 'Already frozen: inspect, never rebuild'
    parent_source=compile_source(PARENT);arms={}
    for arm,(concurrent,quote) in ARMS.items():
        cfg=config(arm);package=cfg['PACKAGE'];stem=cfg['STEM'];job=cfg['JOB']
        obs=first_difference(trace,concurrent,quote);dump_for(stem,'OBSERVATION',obs)
        dump_for(stem,'COMPONENT',checked)
        source=(ROOT/'tools/btc5m_repair_constraints_v1.py').read_text(encoding='utf-8')
        source=once(source,'CONCURRENT = False',f'CONCURRENT = {concurrent}')
        source=once(source,'LEGAL_QUOTE = False',f'LEGAL_QUOTE = {quote}')
        runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
        runner=once(runner,'commitment_repair_rows=producer.commitment_repair.rows,',
                    'commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,commitment_repair_rows=producer.commitment_repair.rows,')
        package.mkdir(exist_ok=True)
        for name in parent['files']:
            assert not (package/name).exists(),name
            if name=='money_runner.py':(package/name).write_text(runner,encoding='utf-8')
            elif name=='commitment_repair.py':(package/name).write_text(source,encoding='utf-8')
            else:shutil.copy2(PARENT/name,package/name)
        shutil.copy2(PARENT/'commitment_repair.py',package/'commitment_base.py')
        manifest=dict(parent)
        manifest.update(version=stem,parent_manifest_sha256=sha(PARENT/'manifest.json'),
            existing_control=str(BASE.relative_to(ROOT)),baseline_result_sha256=sha(BASE/'result.json'),
            baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),observation_sha256=sha(R/(stem+'_OBSERVATION.json')),
            concurrent_repair=concurrent,minimum_legal_repair_quote=quote,panel_maximum_native_jobs=3,
            selection='V24 current commitment demand; optionally remove artificial one-extra-owner count gate and/or align extra repair NEW/maintenance with minimum legal fixed15 quote. Existing Active unchanged.',
            finite_authority='One extra NEW per observed frame; all pending DOWN quantity and limit costs count toward remaining economic demand. All missing reservation owners block. Resource cap 4096 and own-cross remain. No new arbitrary repair owner count.' if concurrent else parent['finite_authority'],
            quote_scope='Only commitment repair overlay orders; post canonical Active. Minimum price=ceil_to_tick(1/15), only if strictly below current ask. Every NEW checks pending and same-plan own-cross. Only these extra owners use the same price for stale maintenance; surplus/end/lifecycle rules unchanged.' if quote else 'V24 unchanged',
            hypothesis='Separate effects and interaction of the two identified repair constraints using V24 as the completed control cell.',
            unchanged='13 parent Python files identical; pinned V24 demand copied verbatim into commitment_base.py. Active/theta/legacy producer quote/sizing/native unchanged; cash gates OFF.',
            evaluation=['Three new cells vs reused V24; prefix equality up to first actual changed decision',
                'Every decision, reservation, quote and maintenance trace reconstructed; original Active unchanged',
                'Actual source/binary/receipt/accounting/owner/sizing checks; all unknown fields preserved on failure',
                'Both branch payoff, pending exposure, filled quantities and trajectory; no winner-based success',
                'Separate structural constraint removal from economic improvement and Target identification'],
            limitations='No absolute repair target learned. Full pending UP fill remains a scenario, not forecast. Individual ticket floor condition is retained; multiple pending DOWN fills without UP may worsen current floor. No result-driven parameter search.',
            files={p.name:sha(p) for p in package.glob('*.py')})
        (package/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        actual=compile_source(package)
        for a,b in [(str(package).replace('\\','\\\\'),str(PARENT).replace('\\','\\\\')),(str(package),str(PARENT)),(package.as_posix(),PARENT.as_posix())]:actual=actual.replace(a,b)
        if quote:
            marker="     maintenance_price=prices[s]\n     if s=='DOWN':maintenance_price,stale=self.commitment_repair.maintenance(f,k,c,prices[s],stale,tick*(1.+softplus(w[11])))\n     self.demand.maintenance(f,k,s,maintenance_price,stale,surplus)"
            actual=once(actual,marker,'     self.demand.maintenance(f,k,s,prices[s],stale,surplus)')
        assert actual==parent_source,'Unexpected producer behavior change'
        staged_module=load('frozen_constraints_'+arm,package/'commitment_repair.py')
        assert staged_module.CONCURRENT==concurrent and staged_module.LEGAL_QUOTE==quote
        assert staged_module.self_test(crossing_owners)['status']=='PASS'
        dump_for(stem,'LOCAL_PREFLIGHT',dict(status='PASS',worker_replay=False,local_native=0,
            actual_runner_prefix_compile_only=True,parent_source_equal_except_declared_maintenance_hook=True,
            unchanged_parent_modules=13,base_demand_byte_identical=True))
        dump_for(stem,'PREREGISTERED',manifest)
        wave=dict(progress_artifact='data/research/REPAIR_CONSTRAINTS_PROGRESS_20260913.json',jobs=[dict(job_id=job,
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{package.name}/money_runner.py',
                  '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR',
                  '--retention','0','--opportunity-mode','ONE_ACTIVE'],cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/cfg['WAVE']).write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
        arms[arm]=dict(job_id=job,package=str(package.relative_to(ROOT)),manifest_sha256=sha(package/'manifest.json'),
                      first_difference_seconds=obs['seconds'],concurrent=concurrent,quote=quote)
    panel=dict(status='FROZEN',new_native_jobs_planned=3,reused_control=str(BASE.relative_to(ROOT)),arms=arms,
        local_native=0,model_fits=0,parameter_search=0,dispatch_order=['concurrent','quote','both'])
    dump_for('BTC5M_REPAIR_CONSTRAINTS_PANEL_V1_20260913','PREREGISTERED',panel)
    print(json.dumps(panel))


if __name__=='__main__':main()
