"""TASK_002 work A: classify the 14 v28 paths (rc / safety_gate) and align with baselines. Read-only; gzip read in memory."""
import json,gzip,hashlib,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];RS=ROOT/'data'/'research';LW=RS/'lan_worker_returns'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def paths(job,prefix):
    rows=json.loads((LW/job/'ROWS.json').read_text(encoding='utf-8'));out=[]
    for r in rows:
        d=LW/job/'arms'/f"{prefix}_{r['arm']}_{r['market']}"
        rec=dict(job=job,arm=r['arm'],market=r['market'],row_rc=r.get('rc'),row_safety_pass=r.get('safety_pass'),row_UP=r.get('UP'),row_DOWN=r.get('DOWN'),row_cost=r.get('cost'))
        rp=d/'result.json.gz'
        if rp.exists():
            res=json.loads(gzip.decompress(rp.read_bytes()))
            sg=res.get('safety_gate') or {}
            ao=res.get('active_opportunity_submissions');co=res.get('coordination_submissions');gf=res.get('general_finite_active_submissions')
            n=lambda x:len(x) if isinstance(x,list) else x
            pay=(res.get('v12g_pay') or {}).get('orders') or {}
            rec.update(result_sha256=sha(rp),status=res.get('status'),error=res.get('error'),safety_gate=sg,failed_checks=[k for k,v in sg.items() if k!='pass' and not v],
                unresolved_owners=res.get('unresolved_owners'),execution_accounting_valid=res.get('execution_accounting_valid'),atomic_pass=(res.get('atomic_responsibility_summary') or {}).get('pass'),
                active_native_submits=res.get('active_native_submits'),active_birth_count=res.get('active_birth_count'),n_opportunity=n(ao),n_coordination=n(co),n_general_finite=n(gf),
                padd_orders=pay.get('PADD'),qualified_new_active=(res.get('qualified_restoration') or {}).get('new_qualified_active'),original_exit_code=(res.get('economic_option') or {}).get('original_exit_code'),
                final_UP=res['final_inventory']['UP']-res['final_cost'] if res.get('final_inventory') else None,final_DOWN=res['final_inventory']['DOWN']-res['final_cost'] if res.get('final_inventory') else None)
            so=d/'stdout.log';rec['stdout_tail']=so.read_text(encoding='utf-8',errors='replace').strip().splitlines()[-1][:300] if so.exists() and so.stat().st_size else ''
            se=d/'stderr.log';rec['stderr_bytes']=se.stat().st_size if se.exists() else None
        else:rec['result_missing']=True
        out.append(rec)
    return out
v28=paths('btc5m-v12g-active-repair4-small-20260927-v28','v12g28')
# baselines: v24 PADD80 (all 30) and pure G300_FLIP40 without PADD (v1 gross-decide small) + controls
v24=paths('btc5m-v12g-fresh30-20260927-v24','v12g24')
def summar(rs,label):
    c=collections.Counter((r['arm'],r.get('row_rc'),r.get('row_safety_pass'),tuple(r.get('failed_checks') or [])) for r in rs);return {label:[dict(arm=a,rc=rc,safety=s,failed=list(f),n=n) for (a,rc,s,f),n in sorted(c.items(),key=str)]}
extra={}
for job,pre in (('btc5m-v12g-gross-decide-small-20260926-v1','v12g1'),('btc5m-v12g-payoff-small-20260926-v11','v12g11')):
    try:
        rows=json.loads((LW/job/'ROWS.json').read_text(encoding='utf-8'))
        extra[job]=[dict(arm=a,rc=rc,safety=s,n=n) for (a,rc,s),n in sorted(collections.Counter((r['arm'],r.get('rc'),r.get('safety_pass')) for r in rows).items(),key=str)]
    except Exception as ex:extra[job]='UNKNOWN: '+repr(ex)[:200]
# check-market alignment v24 PADD80 vs v28 PADD80_CHECK
al=[]
for m in (2628553,2628769):
    a=next(r for r in v24 if r['arm']=='PADD80' and r['market']==m);b=next(r for r in v28 if r['arm']=='PADD80_CHECK' and r['market']==m)
    al.append(dict(market=m,v24=dict(rc=a['row_rc'],safety=a['row_safety_pass'],failed=a.get('failed_checks'),UP=a['row_UP'],DOWN=a['row_DOWN'],cost=a['row_cost'],n_active=a.get('active_native_submits'),padd=a.get('padd_orders')),
                   v28_check=dict(rc=b['row_rc'],safety=b['row_safety_pass'],failed=b.get('failed_checks'),UP=b['row_UP'],DOWN=b['row_DOWN'],cost=b['row_cost'],n_active=b.get('active_native_submits'),padd=b.get('padd_orders')),
                   identical=all(abs(a[k]-b[k])<1e-6 for k in ('row_UP','row_DOWN','row_cost'))))
src=dict(frozen_runner=str(RS/'v12g_fresh30_20260927_v24'/'base'/'frozen_runner.py'),frozen_runner_sha=sha(RS/'v12g_fresh30_20260927_v24'/'base'/'frozen_runner.py'),
         general_finite_active_sha=sha(RS/'v12g_fresh30_20260927_v24'/'base'/'general_finite_active.py'),run_variant_v28_sha=sha(RS/'v12g_active_repair4_small_20260927_v28'/'overlay'/'run_variant.py'),
         worker_v28_sha=sha(RS/'v12g_active_repair4_small_20260927_v28'/'worker.py'),ROWS_v28_sha=sha(LW/'btc5m-v12g-active-repair4-small-20260927-v28'/'ROWS.json'),RESULT_v28_sha=sha(LW/'btc5m-v12g-active-repair4-small-20260927-v28'/'RESULT.json'))
out=dict(v28_paths=v28,summary_v28=summar(v28,'v28'),summary_v24=summar(v24,'v24'),other_baselines=extra,check_alignment=al,sources=src)
(Path(__file__).parent/'A_VALIDITY.json').write_text(json.dumps(out,indent=1,ensure_ascii=False,default=str),encoding='utf-8')
for r in v28:print(r['arm'],r['market'],r['row_rc'],r['row_safety_pass'],r.get('failed_checks'),'unres',r.get('unresolved_owners'),'acct',r.get('execution_accounting_valid'),'atomic',r.get('atomic_pass'),'act',r.get('active_native_submits'),'opp/coord/gfa',r.get('n_opportunity'),r.get('n_coordination'),r.get('n_general_finite'),'padd',r.get('padd_orders'),'qual',r.get('qualified_new_active'),'orig_exit',r.get('original_exit_code'),'stderrB',r.get('stderr_bytes'))
print(json.dumps(out['summary_v24']));print(json.dumps(extra));print(json.dumps(al))
