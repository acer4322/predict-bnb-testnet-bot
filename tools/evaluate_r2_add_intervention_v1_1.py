from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl,finite,stats
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SPLIT=OUT/'r2_pending_management_fresh_split_v1.json'
ART=OUT/'r2_add_intervention_adapter_v1_1.joblib'

def seq(xs:list[float])->dict[str,Any]:
 c=0.0; peak=0.0; maxdd=0.0; curve=[]
 for x in xs:
  c+=float(x); peak=max(peak,c); maxdd=max(maxdd,peak-c); curve.append(c)
 n=len(xs); wins=sum(x>1e-9 for x in xs); losses=sum(x<-1e-9 for x in xs); flats=n-wins-losses
 return {'markets':n,'totalPnl':sum(xs),'wins':wins,'losses':losses,'flat':flats,'winRate':wins/n if n else None,'nonLossRate':(wins+flats)/n if n else None,'medianPnl':sorted(xs)[n//2] if n else None,'worstMarketPnl':min(xs) if xs else None,'bestMarketPnl':max(xs) if xs else None,'maxCumulativeDrawdown':maxdd,'endingCumulativePnl':c}

def one(mid:int,win:str|None)->tuple[dict[str,Any],list[dict[str,Any]]]:
 b=run_market(mid); s=run_market(mid,add_intervention_artifact=ART,pending_option_horizon_ms=5000)
 br=b['studentRollout']; sr=s['studentRollout']; bp=br.get('finalPortfolio') or {}; sp=sr.get('finalPortfolio') or {}
 bpnl=realized_pnl(br,win); spnl=realized_pnl(sr,win)
 idx={(int(x.get('checkpointMs') or -1),str(x.get('orderId') or '')):x for x in s.get('orderStateRows') or []}
 veto=[]
 for v in s.get('addInterventionVetoEvents') or []:
  st=idx.get((int(v['atMs']),str(v['existingOrderId']))) or {}
  veto.append({'marketId':mid,**v,'hftFill1sAfterVeto':int(st.get('labelAnyFill1s') or 0),'hftFill3sAfterVeto':int(st.get('labelAnyFill3s') or 0),'hftFill5sAfterVeto':int(st.get('labelAnyFill5s') or 0),'hftEventualFillAfterVeto':int(float(st.get('eventualAdditionalFillShares') or 0)>1e-9),'futureFirstFillDelayMs':st.get('futureFirstFillDelayMs')})
 row={'marketId':mid,'winner':win,'baselineRealizedPnl':bpnl,'skillRealizedPnl':spnl,'deltaRealizedPnl':(spnl-bpnl) if spnl is not None and bpnl is not None else None,
  'baselineMakerPlacements':int(br.get('makerPlacements') or 0),'skillMakerPlacements':int(sr.get('makerPlacements') or 0),'deltaMakerPlacements':int(sr.get('makerPlacements') or 0)-int(br.get('makerPlacements') or 0),
  'baselineMakerFilledShares':float(br.get('makerFilledShares') or 0),'skillMakerFilledShares':float(sr.get('makerFilledShares') or 0),'deltaMakerFilledShares':float(sr.get('makerFilledShares') or 0)-float(br.get('makerFilledShares') or 0),
  'baselineTakerFills':int(br.get('takerFills') or 0),'skillTakerFills':int(sr.get('takerFills') or 0),'deltaTakerFills':int(sr.get('takerFills') or 0)-int(br.get('takerFills') or 0),
  'baselineCombinedCoverage':finite(bp.get('combined_paired_coverage')),'skillCombinedCoverage':finite(sp.get('combined_paired_coverage')),'deltaCombinedCoverage':float(sp.get('combined_paired_coverage') or 0)-float(bp.get('combined_paired_coverage') or 0),
  'baselineCombinedAbsNet':finite(bp.get('combined_abs_net')),'skillCombinedAbsNet':finite(sp.get('combined_abs_net')),'deltaCombinedAbsNet':float(sp.get('combined_abs_net') or 0)-float(bp.get('combined_abs_net') or 0),
  'baselineWorstCaseFloor':finite(bp.get('worst_case_floor')),'skillWorstCaseFloor':finite(sp.get('worst_case_floor')),'deltaWorstCaseFloor':float(sp.get('worst_case_floor') or 0)-float(bp.get('worst_case_floor') or 0),
  'vetoes':len(veto),'uniqueOptionOrders':int(sr.get('pendingOptionUniqueOrders') or 0)}
 return row,veto

def aggregate(rows,vetoes):
 bp=[float(r['baselineRealizedPnl']) for r in rows if r.get('baselineRealizedPnl') is not None]; sp=[float(r['skillRealizedPnl']) for r in rows if r.get('skillRealizedPnl') is not None]
 n=len(vetoes)
 return {'markets':len(rows),'baseline':seq(bp),'skill':seq(sp),'deltaTotalPnl':sum(sp)-sum(bp),'vetoes':n,'marketsWithVeto':sum(r['vetoes']>0 for r in rows),'uniqueOptionOrders':sum(int(r['uniqueOptionOrders']) for r in rows),
  'vetoHftFill1sRate':sum(v['hftFill1sAfterVeto'] for v in vetoes)/n if n else None,'vetoHftFill3sRate':sum(v['hftFill3sAfterVeto'] for v in vetoes)/n if n else None,'vetoHftFill5sRate':sum(v['hftFill5sAfterVeto'] for v in vetoes)/n if n else None,'vetoHftEventualFillRate':sum(v['hftEventualFillAfterVeto'] for v in vetoes)/n if n else None,
  'deltaMakerFilledShares':stats([r['deltaMakerFilledShares'] for r in rows]),'deltaCombinedCoverage':stats([r['deltaCombinedCoverage'] for r in rows]),'deltaCombinedAbsNet':stats([r['deltaCombinedAbsNet'] for r in rows]),'deltaWorstCaseFloor':stats([r['deltaWorstCaseFloor'] for r in rows]),'deltaPnl':stats([float(r['deltaRealizedPnl']) for r in rows if r.get('deltaRealizedPnl') is not None])}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['dev-validation','sealed-holdout'],default='dev-validation'); a=ap.parse_args(); c=json.loads(SPLIT.read_text(encoding='utf-8'))
 ids=[int(x) for x in (c['developmentMarkets'][20:] if a.mode=='dev-validation' else c['sealedHoldoutMarkets'])]; win=winners(ids); rows=[]; veto=[]; errors=[]
 for i,mid in enumerate(ids,1):
  try:
   r,v=one(mid,win.get(mid)); rows.append(r); veto.extend(v); print(json.dumps({'progress':i,'marketId':mid,'skillPnl':r['skillRealizedPnl'],'deltaPnl':r['deltaRealizedPnl'],'vetoes':r['vetoes'],'dCoverage':r['deltaCombinedCoverage']},ensure_ascii=False),flush=True)
  except Exception as e:
   errors.append({'marketId':mid,'error':f'{type(e).__name__}: {e}'}); print(json.dumps({'progress':i,'marketId':mid,'error':errors[-1]['error']},ensure_ascii=False),flush=True)
 rep={'reportVersion':'R2_ADD_INTERVENTION_V1_1_CLOSED_LOOP','researchOnly':True,'liveTradingChanges':False,'studentScale':'PRE_CAP100_ORIGINAL_R2','mode':a.mode,'ids':ids,'artifact':str(ART),'naturalThreshold':0.5,'optionHorizonMs':5000,'dreamFillAllowed':False,'targetRuntimeInput':False,'aggregate':aggregate(rows,veto),'rows':rows,'vetoAudit':veto,'errors':errors,'graduationContract':{'name':'SECOND_GRADUATION_REAL_EXECUTION','requiredOnSealedHoldout':{'totalPnlPositive':True,'marketWinRateAtLeast':0.60},'stabilityDiagnostics':['maxCumulativeDrawdown','worstMarketPnl','pairedCoverage','combinedAbsNet'],'note':'DEV validation is not a graduation score.'}}
 out=OUT/f'r2_add_intervention_v1_1_{a.mode.replace("-","_")}.json'; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'report':str(out),'aggregate':rep['aggregate'],'errors':errors},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
