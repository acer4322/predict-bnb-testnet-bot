from __future__ import annotations
import json,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as ctl
OUT=ROOT/'data/research/r3_v0/r31_r3s_pre_echtgeld_final_exam_v1.json'
COHORT=[1691070,1690716,1691181,1691059,1691178,1692505,1690674,1692495,1692220,1695790]
def s(r):
 x=r['studentRollout'];p=x['finalPortfolio'];c=r['r3Control'];return {'makerFillEvents':x['makerFillEvents'],'makerFilledShares':x['makerFilledShares'],'takerFills':x['takerFills'],'finalAbsNet':p.get('combined_abs_net'),'worstCaseFloor':p.get('worst_case_floor'),'makerNet':p.get('maker_net'),'vetoStrong':c['vetoStrong'],'weakSubstitutions':c['weakSubstitutions'],'contextualEvents':c['contextualEvents']}
def main():
 rows=[];errs={}
 for m in COHORT:
  try:
   a=ctl.run_market(m,False);b=ctl.run_market(m,True);A=s(a);B=s(b);rows.append({'marketId':m,'baseline':A,'r31R3S':B,'floorDelta':B['worstCaseFloor']-A['worstCaseFloor'],'absNetDelta':B['finalAbsNet']-A['finalAbsNet'],'makerSharesDelta':B['makerFilledShares']-A['makerFilledShares'],'executionExact':A['makerFillEvents']==B['makerFillEvents'] and A['makerFilledShares']==B['makerFilledShares'] and A['takerFills']==B['takerFills'] and A['finalAbsNet']==B['finalAbsNet'] and A['worstCaseFloor']==B['worstCaseFloor']})
  except Exception as e:errs[str(m)]=repr(e)
 summary={'markets':len(rows),'errors':len(errs),'floorBetter':sum(r['floorDelta']>1e-9 for r in rows),'floorWorse':sum(r['floorDelta']<-1e-9 for r in rows),'floorEqual':sum(abs(r['floorDelta'])<=1e-9 for r in rows),'absNetBetter':sum(r['absNetDelta']<-1e-9 for r in rows),'absNetWorse':sum(r['absNetDelta']>1e-9 for r in rows),'absNetEqual':sum(abs(r['absNetDelta'])<=1e-9 for r in rows),'executionExact':sum(r['executionExact'] for r in rows),'contextObserved':sum(r['r31R3S']['contextualEvents']>0 for r in rows)}
 out={'version':'R31_R3S_PRE_ECHTGELD_FINAL_EXAM_V1','paperOnly':True,'echtgeldAuthority':False,'cohortSelection':'recent HFT execution-anomaly markets, high partial/no-fill coverage; not selected by PnL','rows':rows,'errors':errs,'summary':summary,'createdAtMs':int(time.time()*1000)};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':not errs,'artifact':str(OUT),'summary':summary}))
if __name__=='__main__':main()
