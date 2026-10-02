"""Mechanism-only aggregate of reused first path and nine authorized new paths."""
import hashlib,json,sys
from collections import Counter
from pathlib import Path
P=Path(__file__).resolve().parents[1];ROOT=P.parents[2];sys.path.insert(0,str(P))
from analyze import read
plan=read(P/'PROTOCOL.json');ids=[2671717]+plan['markets'];returns=ROOT/'data/research/lan_worker_returns'
rows=[];stats=Counter();gates=Counter();legacy=[];BASE_legacy=[]
for mid in ids:
 first=mid==2671717
 arm=returns/('btc5m-deep-layer-stage1-20261002-v1' if first else plan['job_id'])/'arms'/f"{'deep1' if first else 'deep1r9'}_DEEP_{mid}"
 r=read(arm/'result.json');a=read(arm/('AUDIT_POST_COLLECTION.json' if first else 'AUDIT.json'));d=read(arm/'deep_layer_trace.json');par=read(arm/'PARITY.json');ex=read(arm/'EXECUTION.json');cl=read(arm/'execution_clock.json')
 assert r['status']=='COMPLETE' and a['status']=='PASS' and a['path_valid']
 assert all(a['mandatory'].values()) and not r['unresolved_owners'] and all(c['state']=='TERMINAL' for c in cl['carriers'].values())
 stats.update(d['stats']);gates.update({k:int(v) for k,v in a['mandatory'].items()})
 base=returns/f'btc5m-cg1at-fresh100a-20260930/arms/c100_CG1AT_{mid}'
 old=read(base/'EXECUTION.json')['env_v12'];env=dict(ex['env_v12']);assert env.pop('V12G_DEEP_LAYER')=='ON' and env==old
 br=read(base/'result.json')
 if not r['safety_gate']['active_matches_opportunity']:legacy.append(mid)
 if not br['safety_gate']['active_matches_opportunity']:BASE_legacy.append(mid)
 attempts=d['stats']['attempts'];placed=len(d['orders']);filled=sum(o['filled_qty']>0 for o in d['orders']);qty=sum(o['filled_qty'] for o in d['orders'])
 races=sum(bool(o['filled_qty']>0 and any(c['reason']=='DEEP_TTL' for c in o['cancels'])) for o in d['orders'])
 rows.append(dict(market=mid,status=r['status'],audit=a['status'],parity=par['status'],prefix_plan_rows=par['rows'][0]['deep_count'],first_deep_new_t=par['first_deep_new_t'],attempts=attempts,orders=placed,filled_orders=filled,fill_rate=filled/placed if placed else None,filled_qty=qty,TTL_cancel=d['stats']['ttl_cancel'],cancel_fill_races=races,stats=d['stats'],mandatory=a['mandatory'],legacy_active_matches_opportunity=r['safety_gate']['active_matches_opportunity'],original_AUDIT=read(arm/'AUDIT.json')['status'],reused=first))
placed=sum(x['orders'] for x in rows);filled=sum(x['filled_orders'] for x in rows)
result=dict(status='COMPLETE_STAGE1_10_NATIVE_PATHS',resume_job=plan['job_id'],original_job='btc5m-deep-layer-stage1-20261002-v1',native_new_paths=9,reused_paths=1,total_paths=10,no_replacement=True,invalid_paths=0,rows=rows,stats=dict(stats),orders=placed,filled_orders=filled,fill_rate=filled/placed,filled_qty=sum(x['filled_qty'] for x in rows),mandatory_pass_counts=dict(gates),empty_prefix_paths=sum(x['prefix_plan_rows']==0 for x in rows),legacy_DEEP_fail=legacy,legacy_BASE_fail=BASE_legacy,inert_scope='local disabled source identity + existing-plan replay; native OFF rerun not performed',model_fits=0,economic_verdict=None,next_stage_started=False)
(P/'SUMMARY.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
for r in rows:print(json.dumps({k:r[k] for k in ('market','audit','parity','prefix_plan_rows','orders','filled_orders','filled_qty','legacy_active_matches_opportunity')}))
