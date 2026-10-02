from __future__ import annotations
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_containment_smoke_v1 as cont
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0'; OUT=D/'r3s_triple_confirm_containment_ab10_v1.json'; STATUS=D/'r3s_triple_confirm_containment_ab10_status_v1.json'
POST=D/'r3_post_add_our_state_student_v2_stream.joblib'
MIDS=[1679102,1678736,1677242,1676837,1676476,1676312,1676286,1674459,1663753,1649545]
# Pre-registered from shadow evidence, not outcome mining: candidate only if Stable-ADD < .5, Stable Expansion < .5 and utility <=0, and EarlyRecovery15s < .5.
PRE={1663753:{'pStableAdd':0.07934407797071673,'pStableExpand':0.20251704029406611,'utility':-0.48144021950329746,'pEarly15':0.042672148090122396}}
def stat(xs):
 s=sorted(float(x) for x in xs); n=len(s); return {'n':n,'sum':sum(s),'mean':sum(s)/n,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2,'min':min(s),'max':max(s),'positiveRate':sum(x>0 for x in s)/n}
def dd(xs):
 eq=peak=m=0.; pi=ti=-1;pk=-1
 for i,x in enumerate(xs):
  eq+=x
  if eq>peak:peak=eq;pk=i
  if peak-eq>m:m=peak-eq;pi=pk;ti=i
 return {'maxDrawdown':m,'endingEquity':eq,'peakIndex':pi,'troughIndex':ti}
def fp(r,k): return float((r['studentRollout']['finalPortfolio'] or {}).get(k) or 0.)
def save(state,rows,errors):
 rep={'version':'R3S_TRIPLE_CONFIRM_CONTAINMENT_AB10_V1','state':state,'researchOnly':True,'cohort':MIDS,'policy':{'trigger':'pre-registered triple-confirm only','stableAddLt':.5,'stableExpansionLt':.5,'utilityLe':0.0,'earlyRecovery15Lt':.5,'containmentAfterMs':12000,'repairFractionResidual':.75},'rows':rows,'errors':errors}
 if rows:
  b=[r['baselinePnl'] for r in rows]; c=[r['candidatePnl'] for r in rows]
  rep['summary']={'baseline':{'pnl':stat(b),'maxDrawdown':dd(b),'floor':stat([r['baselineFloor'] for r in rows]),'absNet':stat([r['baselineAbsNet'] for r in rows])},'candidate':{'pnl':stat(c),'maxDrawdown':dd(c),'floor':stat([r['candidateFloor'] for r in rows]),'absNet':stat([r['candidateAbsNet'] for r in rows])},'deltaPnl':stat([r['candidatePnl']-r['baselinePnl'] for r in rows]),'candidateBetter':sum(r['candidatePnl']>r['baselinePnl']+1e-9 for r in rows),'candidateWorse':sum(r['candidatePnl']<r['baselinePnl']-1e-9 for r in rows),'same':sum(abs(r['candidatePnl']-r['baselinePnl'])<=1e-9 for r in rows),'triggeredMarkets':[r['marketId'] for r in rows if r['triggered']]}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); STATUS.write_text(json.dumps({'state':state,'completed':len(rows),'errors':len(errors),'artifact':str(OUT)},indent=2),encoding='utf-8')
def main():
 ww=winners(MIDS); rows=[]; errors={}; save('RUNNING',rows,errors)
 for mid in MIDS:
  try:
   b=base.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True)
   trig=mid in PRE
   if trig:
    c=cont.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,containment_after_add_ms=12000,containment_repair_fraction=.75)
   else:c=b
   w=ww[mid]
   rows.append({'marketId':mid,'triggered':trig,'triggerEvidence':PRE.get(mid),'baselinePnl':realized_pnl(b['studentRollout'],w),'candidatePnl':realized_pnl(c['studentRollout'],w),'baselineFloor':fp(b,'worst_case_floor'),'candidateFloor':fp(c,'worst_case_floor'),'baselineAbsNet':fp(b,'combined_abs_net'),'candidateAbsNet':fp(c,'combined_abs_net'),'baselineTakerShares':float(b['studentRollout'].get('takerFilledShares') or 0.),'candidateTakerShares':float(c['studentRollout'].get('takerFilledShares') or 0.),'containmentEvents':c.get('containmentEvents',[])})
  except Exception as e: errors[str(mid)]=repr(e)
  save('RUNNING',rows,errors)
 save('COMPLETE',rows,errors); print(json.dumps({'ok':True,'artifact':str(OUT),'completed':len(rows),'errors':len(errors)}))
if __name__=='__main__':main()
