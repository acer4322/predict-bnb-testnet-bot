from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np
from tools import audit_eth_v32f_joint_parent_reachability_v1 as base
from tools.audit_eth_v32j_current_v30_execution_credit import depth_path, auc, med

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES=[
 ROOT/'data/research/lan_worker_returns/v32k-v30-stagea-b1/result.json',
 ROOT/'data/research/lan_worker_returns/v32k-v30-stagea-b2/result.json',
 ROOT/'data/research/lan_worker_returns/v32k-v30-stagea-b3/result.json',
 ROOT/'data/research/lan_worker_returns/v32k-v30-stagea-b4/result.json',
]
OUT=B/'ETH_REPAIR_V32K_EXECUTION_CREDIT_CONFIRMATORY_STAGEA_RESULT.json'

def cycle_boundaries(f):
 ev=sorted(f.get('thesisEvents',[]),key=lambda x:int(x.get('t',0)))
 starts=[e for e in ev if e.get('event') in ('OPEN_DIRECTION_ACTUAL_FILL','REEXPAND_ACTUAL_FILL')]
 recovs=[e for e in ev if e.get('event')=='PAYOFF_RECOVERED']
 out=[]
 for i,s in enumerate(starts):
  st=int(s['t']); nxt=int(starts[i+1]['t']) if i+1<len(starts) else 10**30
  rr=[r for r in recovs if st<=int(r['t'])<nxt]
  out.append({'cycle':i,'startT':st,'endT':nxt,'recovered':bool(rr),'recoveryT':int(rr[0]['t']) if rr else None})
 return out

def main():
 artifacts=[]
 for p in FILES:
  d=json.loads(p.read_text(encoding='utf-8'))
  for row in d['rows']:artifacts.append((p.name,int(row['marketId']),row['functional']))
 con=sqlite3.connect(f'file:{base.DB.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 rows=[]; structural=[]
 for src,mid,f in artifacts:
  caps=sorted(f.get('payoffRepairQtyCaps',[]),key=lambda x:int(x['t']))
  thesis=f.get('thesisSide'); repair='DOWN' if thesis=='UP' else 'UP'
  for cyc in cycle_boundaries(f):
   cc=[x for x in caps if cyc['startT']<=int(x['t'])<cyc['endT']]
   if len(cc)<2:
    structural.append({'source':src,'marketId':mid,'cycle':cyc['cycle'],'recovered':cyc['recovered'],'carrierCount':len(cc),'classification':'NO_SECOND_CARRIER'})
    continue
   first,second=cc[0],cc[1]
   q=depth_path(con,mid,int(first['t']),int(second['t']),repair,float(first['price']))
   if not q:
    structural.append({'source':src,'marketId':mid,'cycle':cyc['cycle'],'recovered':cyc['recovered'],'carrierCount':len(cc),'classification':'BOOK_PATH_UNAVAILABLE'})
    continue
   rows.append({'source':src,'marketId':mid,'cycle':cyc['cycle'],'recovered':cyc['recovered'],'failed':not cyc['recovered'],'repairSide':repair,'carrierCount':len(cc),'firstCarrierT':int(first['t']),'secondCarrierT':int(second['t']),'firstCarrierPrice':float(first['price']),'credit':q})
 con.close()
 rec=[r for r in rows if r['recovered']]; fail=[r for r in rows if r['failed']]; labels=[r['recovered'] for r in rows]
 feats=['publicLevelDepletionFraction','persistentNetDepletionFraction','grossReplenishmentFraction','replenishmentToDepletion','atBestReceiptFraction','maxBehindTicks']
 metrics={}
 for k in feats:
  vals=[r['credit'][k] for r in rows]
  metrics[k]={'aucRecovered':auc(vals,labels),'recoveredMedian':med([r['credit'][k] for r in rec]),'failedMedian':med([r['credit'][k] for r in fail])}
 support=len(rows)>=6 and len(rec)>=2 and len(fail)>=2
 dep=metrics['publicLevelDepletionFraction']; net=metrics['persistentNetDepletionFraction']; churn=metrics['replenishmentToDepletion']
 direction=bool(dep['recoveredMedian'] is not None and dep['failedMedian'] is not None and dep['recoveredMedian']>dep['failedMedian'] and net['recoveredMedian']>net['failedMedian'] and churn['failedMedian']>churn['recoveredMedian'])
 aucpass=bool((dep['aucRecovered'] or 0)>=.70 and (net['aucRecovered'] or 0)>=.70)
 keep=bool(support and direction and aucpass)
 out={'version':'ETH_REPAIR_V32K_EXECUTION_CREDIT_CONFIRMATORY_STAGEA_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'preregistered':'ETH_REPAIR_V32K_EXECUTION_CREDIT_CONFIRMATORY_PREREGISTERED.json','stage':'A','currentParentCount':len(rows),'recovered':len(rec),'failed':len(fail),'supportAdequate':support,'metrics':metrics,'checks':{'directionPass':direction,'aucPass':aucpass},'decision':'KEEP_EXECUTION_CREDIT_SHADOW' if keep else ('TESTED_INCONCLUSIVE_STAGEB_REQUIRED' if not support else 'REJECT_EXECUTION_CREDIT_CURRENT_V30'),'rows':rows,'nonGradientCurrentParents':structural,'boundary':['Stage-A fixed chronological consumed cohort only','current V30 PAYOFF_RECOVERED truth only','strict-past first-carrier book path ends at first replacement','no winner/PnL','no threshold sweep','no Taker authority','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:out[k] for k in ['currentParentCount','recovered','failed','supportAdequate','metrics','checks','decision']},ensure_ascii=False))
if __name__=='__main__':main()
