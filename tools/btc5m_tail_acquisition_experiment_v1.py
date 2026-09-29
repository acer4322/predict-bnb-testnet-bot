"""V44: saved Target late-flow evidence and one isolated native NEW ablation."""
import argparse
from copy import deepcopy
import inspect
import json
import shutil
import sys
import types

import btc5m_demand_price_coordination_experiment_v1 as parent

ROOT,R,read,sha,once=parent.ROOT,parent.R,parent.read,parent.sha,parent.once
STEM='BTC5M_TAIL_ACQUISITION_ABLATION_V1_20260913'
BASE=parent.PACKAGE
PACKAGE=ROOT/'.lan_worker_v1/tail_acquisition_ablation_1977248_20260913_v1'
JOB='fixed15-core-loop-1977248-tail-acquisition-ablation-20260913-v1'
PARENT_JOB=deepcopy(parent.job())


def dump(tag,value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def job():
    j=deepcopy(PARENT_JOB);j['job_id']=JOB
    j['argv'][1]=f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py'
    return j


def worker():
    w=parent.worker();w.STEM=STEM;w.PACKAGE=PACKAGE;w.dump=dump;w.jobs=lambda:[job()]
    return w


def evidence():
    from audit_btc5m_price_retention_casepanel_v1 import load_paths, PACK, AUDIT, ALL_CASES
    from verify_btc5m_active_repair_opportunity_v1 import canonical_legs
    paths,meta=load_paths();cases=[]
    for mid,rows in paths.items():
        strong='UP' if rows[-1]['inv']['UP']>rows[-1]['inv']['DOWN'] else 'DOWN'
        windows=[]
        for lo in range(0,300,60):
            selected=[r for r in rows if lo*1000<=r['t']<(lo+60)*1000]
            flow={s:dict(qty=sum(v['qty'] for r in selected for v in r['flow'][s].values()),
                cash=sum(v['cash'] for r in selected for v in r['flow'][s].values()),
                event_seconds=sum(any(v['qty']>0 for v in r['flow'][s].values()) for r in selected),
                fill_legs=sum(v['legs'] for r in selected for v in r['flow'][s].values()),
                routes={route:{k:sum(r['flow'][s][route][k] for r in selected) for k in ('qty','cash','legs')}
                        for route in ('MAKER','TAKER')}) for s in ('UP','DOWN')}
            windows.append(dict(start=lo,end=lo+60,flow=flow))
        for s in ('UP','DOWN'):
            assert abs(sum(w['flow'][s]['qty'] for w in windows)-rows[-1]['inv'][s])<1e-7
        assert abs(sum(w['flow'][s]['cash'] for w in windows for s in ('UP','DOWN'))-rows[-1]['cost'])<1e-7
        last={s:max((r['t']/1000 for r in rows if any(v['qty']>0 for v in r['flow'][s].values())),default=None) for s in ('UP','DOWN')}
        cases.append(dict(market=mid,asset=meta[mid].get('source_summary',{}).get('asset','BTC'),
            strong=strong,metadata=meta[mid],terminal=rows[-1]['geometry'],windows=windows,last_observed_fills=last,
            final_minute_vs_previous=dict(event_seconds=windows[4]['flow'][strong]['event_seconds']-windows[3]['flow'][strong]['event_seconds'],
                qty=windows[4]['flow'][strong]['qty']-windows[3]['flow'][strong]['qty'])))
    folder=R/'lan_worker_returns'/parent.JOB
    n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz');legs=canonical_legs(n,tr)
    public=read(BASE/'inputs/public_1977248.json.gz');start=public['market']['window_start_ms']
    out=dict(status='PASS',cases=cases,sources={str(p.relative_to(ROOT)):sha(p) for p in (PACK,AUDIT,ALL_CASES)},
        case_selection='13 previously consumed BTC paths; deliberately selected outcome cases plus main market, not a representative sample.',
        observation='Fill legs/event-seconds are not submitted-order frequency. Final strong side is an offline accounting label, not evidence of fixed private intention.',
        final_minute_strong_event_seconds_lower=sum(c['final_minute_vs_previous']['event_seconds']<0 for c in cases),
        final_minute_strong_event_seconds_higher=sum(c['final_minute_vs_previous']['event_seconds']>0 for c in cases),
        final_minute_strong_event_seconds_equal=sum(c['final_minute_vs_previous']['event_seconds']==0 for c in cases),
        baseline_post_218875=dict(new_orders=[dict(seconds=(p['t']-start)/1000,**o) for p in tr['plans'] if p['t']>start+218875 for o in p['operations'] if o['kind']=='NEW'],
            flow={s:dict(qty=sum(l['qty'] for l in legs if l['t']>start+218875 and l['side']==s),cash=sum(l['cash'] for l in legs if l['t']>start+218875 and l['side']==s)) for s in ('UP','DOWN')}),
        baseline_result_sha256=sha(folder/'result.json'),baseline_trace_sha256=sha(folder/'clock_trace.json.gz'),
        model_fits=0,parameter_search=0,native_jobs=0)
    dump('TARGET_EVIDENCE',out)
    return dict(status='PASS',cases=len(cases),lower=out['final_minute_strong_event_seconds_lower'],higher=out['final_minute_strong_event_seconds_higher'],equal=out['final_minute_strong_event_seconds_equal'])


def prepare():
    assert not PACKAGE.exists() and not (R/(STEM+'_PROTOCOL.json')).exists()
    bm=read(BASE/'manifest.json')
    assert sha(BASE/'manifest.json')=='8a8d7a86ca4f233facd1f77e9ba652cb3104d780b9b08c0a1fabe9d59affc158'
    assert all(sha(BASE/k)==v for k,v in bm['files'].items())
    assert read(R/(STEM+'_TARGET_EVIDENCE.json'))['status']=='PASS'
    dump('PROTOCOL',dict(status='PREREGISTERED_BEFORE_NATIVE',market=1977248,mode='ORACLE_DOWN',baseline_job=parent.JOB,
        hypothesis='Determine whether forbidding fresh directional Passive commitments in the last third limits late re-exposure while existing repair rules and old owners remain.',
        dedup='V29/V30/V35 inspect addition timing and prices; V41 caps growth after service; V43 coordinates crossing owners for one repair service. No completed final-third NEW ablation found. V16/V17 early/late samples split chronological markets, not within-market clock. Existing V43 is reused, not rerun.',
        treatment='Before actual draft reservation, screen strong-side Passive NEW when 3*(current_public_time-start)>=2*(end-start), strictly before end. No new tail CANCEL, no demand reduction, no changed Active eligibility.',
        threshold='Predeclared final third of the public market duration (200s for 300s), a coarse diagnostic boundary, not a fit to Target last fill or an inferred private rule. No threshold sweep.',
        measurement='Replayed consequences, not subtraction of old fills. Count actual NEW, actual old-owner fills after cutoff, repair costs, terminal branches, path floor area and pending terminal state.',
        interpretation='Zero-new boundary ablation, not a trained lower-frequency policy; possible lost profitable acquisitions must be reported. Selected cases contain counterexamples to universal clock-only slowdown.',
        unchanged=['V43 WorkMemory exact completion including monetary dust','At most five Active and two renewed services','GrowthHold and underlying desired path','Cash gates OFF and cash caps null','Passive15 and variable Active NEW>=1','Existing maintenance and pending/UNKNOWN reservations','Demand-depth repair and conflict coordination','Inputs/theta/qref/native binary/shared sizing/live services'],
        cap_issue='Dust completion and repeated-work authority remain separate next mechanisms, deliberately frozen in this comparison.',
        max_native_jobs=1,max_threads=4,parameter_search=0,model_fits=0,local_native_jobs=0,
        failure='One submit; preserve all artifacts and UNKNOWN on failure. No automatic rerun.'))
    PACKAGE.mkdir()
    for name in bm['files']:
        dst=PACKAGE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,dst)
    shutil.copy2(ROOT/'tools/btc5m_tail_new_stop_v1.py',PACKAGE/'tail_new.py')
    p=PACKAGE/'money_runner.py';s=p.read_text(encoding='utf-8')
    s=once(s,'    source=transform_policy(source)',"    source=transform_policy(source)\n    tail=load('tail_new_stop',package/'tail_new.py');tail.self_test()\n    source=tail.instrument(source,replace)")
    s=once(s,'namespace = dict(roles=roles,','namespace = dict(_TailNewStop=tail.TailNewStop, roles=roles,')
    s=once(s,'payload = dict(growth_hold_rows=', 'payload = dict(tail_new_rows=producer.tail_new.rows,growth_hold_rows=')
    p.write_text(s,encoding='utf-8')
    # Observation-only addition: preserve the actual actor depth used by V43.
    p=PACKAGE/'renewed_work.py';s=p.read_text(encoding='utf-8')
    s=once(s,'row.update(adaptive=adaptive,cancellable=dict(frame[\'cancellable\']))',"row.update(adaptive=adaptive,cancellable=dict(frame['cancellable']),actor_asks=asks)")
    p.write_text(s,encoding='utf-8')
    m=deepcopy(bm);m.update(version=STEM,parent_manifest_sha256=sha(BASE/'manifest.json'),selection='Final-third directional Passive NEW ablation',maximum_native_jobs=1)
    m['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):
        src=parent.prior.parent.compiled(PACKAGE,mode)
        assert src.count('self.tail_new.veto(')==5
        compile(src,'V44_COMPILE_ONLY','exec')
    tail=parent.prior.parent.load('tail_local',PACKAGE/'tail_new.py')
    dump('COMPONENT',dict(status='PASS',local=tail.self_test(),three_mode_compile=True,reservation_sites=5,
        changed_parent_files=[k for k,h in bm['files'].items() if m['files'][k]!=h],files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'),native_jobs=0))
    dump('WAVE',dict(jobs=[job()]));dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',submissions=0))
    return dict(status='PASS',files=len(m['files']),manifest_sha256=sha(PACKAGE/'manifest.json'))


def audit():
    # Reuse the V43 structural checks against this pinned package and job.
    ns=dict(parent.audit.__globals__,STEM=STEM,PACKAGE=PACKAGE,JOB=JOB,
        dump=dump,job=job,worker=worker)
    return types.FunctionType(parent.audit.__code__,ns)()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('evidence','prepare','preflight','submit','status','collect','audit'));a=ap.parse_args()
    if a.action in ('evidence','prepare','audit'):result=globals()[a.action]()
    elif a.action=='status':result=worker().dispatch.cmd_status(worker().HOST,JOB)
    elif a.action=='preflight':result=worker().preflight()
    else:result=getattr(worker(),a.action)(0)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
