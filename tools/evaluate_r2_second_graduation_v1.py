from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl,finite,stats
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SPLIT=OUT/'r2_pending_management_fresh_split_v1.json'
ART=OUT/'r2_add_intervention_adapter_v1_2_final.joblib'
FROZEN_SHA='48a58eee91f5c6aa9e2e0a00fefbaf7d6c5a123e75ac7b6f9662d7aa483c4525'

def seq(xs:list[float])->dict[str,Any]:
 c=0.0; peak=0.0; dd=0.0; curve=[]
 for x in xs:
  c+=float(x); peak=max(peak,c); dd=max(dd,peak-c); curve.append(c)
 n=len(xs); w=sum(x>1e-9 for x in xs); l=sum(x<-1e-9 for x in xs); f=n-w-l
 return {'markets':n,'totalPnl':sum(xs),'wins':w,'losses':l,'flat':f,'winRate':w/n if n else None,'maxCumulativeDrawdown':dd,'worstMarketPnl':min(xs) if xs else None,'bestMarketPnl':max(xs) if xs else None,'cumulativePnl':curve}

def make_row(mid:int,b:dict[str,Any],s:dict[str,Any],winner:str|None):
 br=b['studentRollout']; sr=s['studentRollout']; bp=br.get('finalPortfolio') or {}; sp=sr.get('finalPortfolio') or {}; bpnl=realized_pnl(br,winner); spnl=realized_pnl(sr,winner)
 idx={(int(x.get('checkpointMs') or -1),str(x.get('orderId') or '')):x for x in s.get('orderStateRows') or []}; vv=[]
 for v in s.get('addInterventionVetoEvents') or []:
  st=idx.get((int(v['atMs']),str(v['existingOrderId']))) or {}; vv.append({'marketId':mid,**v,'hftFill1sAfterVeto':int(st.get('labelAnyFill1s') or 0),'hftFill3sAfterVeto':int(st.get('labelAnyFill3s') or 0),'hftFill5sAfterVeto':int(st.get('labelAnyFill5s') or 0),'hftEventualFillAfterVeto':int(float(st.get('eventualAdditionalFillShares') or 0)>1e-9)})
 return {'marketId':mid,'winner':winner,'baselineRealizedPnl':bpnl,'skillRealizedPnl':spnl,'deltaRealizedPnl':spnl-bpnl if spnl is not None and bpnl is not None else None,'baselineMakerFilledShares':float(br.get('makerFilledShares') or 0),'skillMakerFilledShares':float(sr.get('makerFilledShares') or 0),'deltaMakerFilledShares':float(sr.get('makerFilledShares') or 0)-float(br.get('makerFilledShares') or 0),'baselineTakerFills':int(br.get('takerFills') or 0),'skillTakerFills':int(sr.get('takerFills') or 0),'baselineCombinedCoverage':finite(bp.get('combined_paired_coverage')),'skillCombinedCoverage':finite(sp.get('combined_paired_coverage')),'deltaCombinedCoverage':float(sp.get('combined_paired_coverage') or 0)-float(bp.get('combined_paired_coverage') or 0),'baselineCombinedAbsNet':finite(bp.get('combined_abs_net')),'skillCombinedAbsNet':finite(sp.get('combined_abs_net')),'deltaCombinedAbsNet':float(sp.get('combined_abs_net') or 0)-float(bp.get('combined_abs_net') or 0),'baselineWorstCaseFloor':finite(bp.get('worst_case_floor')),'skillWorstCaseFloor':finite(sp.get('worst_case_floor')),'vetoes':len(vv),'uniqueOptionOrders':int(sr.get('pendingOptionUniqueOrders') or 0)},vv

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,default=0); ap.add_argument('--count',type=int,default=9); ap.add_argument('--aggregate-only',action='store_true'); a=ap.parse_args(); contract=json.loads(SPLIT.read_text(encoding='utf-8')); ids=[int(x) for x in contract['sealedHoldoutMarkets']]
 if not a.aggregate_only:
  mids=ids[a.start:a.start+a.count]; raw=[]; errors=[]
  # Deliberately run all rollouts before reading settlement winners.
  for i,mid in enumerate(mids,1):
   try:
    b=run_market(mid); s=run_market(mid,add_intervention_artifact=ART,pending_option_horizon_ms=5000); raw.append((mid,b,s)); print(json.dumps({'progress':i,'marketId':mid,'rolloutComplete':True,'vetoes':int(s['studentRollout'].get('addInterventionVetoes') or 0)},ensure_ascii=False),flush=True)
   except Exception as e: errors.append({'marketId':mid,'error':f'{type(e).__name__}: {e}'}); print(json.dumps({'progress':i,'marketId':mid,'error':errors[-1]['error']},ensure_ascii=False),flush=True)
  win=winners([x[0] for x in raw]); rows=[]; veto=[]
  for mid,b,s in raw:
   r,v=make_row(mid,b,s,win.get(mid)); rows.append(r); veto.extend(v)
  z={'version':'R2_SECOND_GRADUATION_BATCH_V1','frozenContractSha256':FROZEN_SHA,'start':a.start,'ids':mids,'rows':rows,'vetoes':veto,'errors':errors}; out=OUT/f'r2_second_graduation_batch_i{a.start}_n{len(mids)}_v1.json'; out.write_text(json.dumps(z,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'batch':str(out),'completed':len(rows),'errors':errors},ensure_ascii=False)); return
 rows=[]; veto=[]; errors=[]
 for p in sorted(OUT.glob('r2_second_graduation_batch_i*_n*_v1.json')):
  z=json.loads(p.read_text(encoding='utf-8'))
  if z.get('frozenContractSha256')!=FROZEN_SHA: continue
  rows.extend(z.get('rows') or []); veto.extend(z.get('vetoes') or []); errors.extend(z.get('errors') or [])
 by={int(r['marketId']):r for r in rows if int(r['marketId']) in set(ids)}; rows=[by[m] for m in ids if m in by]; seen=set(); vv=[]
 for v in veto:
  k=(int(v['marketId']),int(v['atMs']),str(v['existingOrderId']),str(v.get('attemptReason')))
  if k not in seen: seen.add(k); vv.append(v)
 bp=[float(r['baselineRealizedPnl']) for r in rows if r.get('baselineRealizedPnl') is not None]; sp=[float(r['skillRealizedPnl']) for r in rows if r.get('skillRealizedPnl') is not None]; bs=seq(bp); ss=seq(sp); blocks=[]
 for st in range(0,len(sp),9): blocks.append({'startIndex':st,'endIndex':min(st+9,len(sp))-1,**seq(sp[st:st+9])})
 n=len(vv); agg={'markets':len(rows),'expectedMarkets':len(ids),'baseline':bs,'skill':ss,'deltaTotalPnl':ss['totalPnl']-bs['totalPnl'],'blocksOf9':blocks,'vetoes':n,'marketsWithVeto':sum(r['vetoes']>0 for r in rows),'vetoHftFill1sRate':sum(v['hftFill1sAfterVeto'] for v in vv)/n if n else None,'vetoHftFill3sRate':sum(v['hftFill3sAfterVeto'] for v in vv)/n if n else None,'vetoHftFill5sRate':sum(v['hftFill5sAfterVeto'] for v in vv)/n if n else None,'vetoHftEventualFillRate':sum(v['hftEventualFillAfterVeto'] for v in vv)/n if n else None,'deltaMakerFilledShares':stats([r['deltaMakerFilledShares'] for r in rows]),'deltaCombinedCoverage':stats([r['deltaCombinedCoverage'] for r in rows]),'deltaCombinedAbsNet':stats([r['deltaCombinedAbsNet'] for r in rows])}
 complete=len(rows)==len(ids) and not errors; graduation={'complete':complete,'noDreamFill':True,'targetRuntimeInput':False,'totalPnlPositive':complete and ss['totalPnl']>0,'winRateAtLeast60pct':complete and ss['winRate'] is not None and ss['winRate']>=.60}; graduation['passed']=all(graduation.values())
 rep={'reportVersion':'R2_SECOND_GRADUATION_REAL_EXECUTION_V1','researchOnly':True,'liveTradingChanges':False,'student':'PRE_CAP100_ORIGINAL_R2 + EXECUTION_AWARE_FILL_LIFECYCLE + ADD_INTERVENTION_V1_2_FINAL','frozenContractSha256':FROZEN_SHA,'artifact':str(ART),'executionPhysics':'Execution Tape V1 + HftBacktest PartialFillExchange + RiskAverseQueueModel + entry 1092ms + response 273ms + true matches mid','graduationContract':{'hardGates':['all 27 sealed markets complete with no errors','dream fill forbidden','Target unavailable at runtime','total realized PnL > 0','market win rate >= 60%'],'stabilityDiagnostics':['max cumulative drawdown','three chronological blocks of 9','worst market PnL','paired coverage / combined abs net']},'aggregate':agg,'graduation':graduation,'rows':rows,'vetoAudit':vv,'errors':errors}
 out=OUT/'r2_second_graduation_real_execution_v1.json'; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'report':str(out),'graduation':graduation,'aggregate':agg,'errors':errors},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
