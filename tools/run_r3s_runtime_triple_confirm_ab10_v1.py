from __future__ import annotations
import json,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_containment_smoke_v1 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
import joblib, numpy as np
D=ROOT/'data/research/r3_v0'; OUT=D/'r3s_runtime_triple_confirm_ab10_v1.json'; STATUS=D/'r3s_runtime_triple_confirm_ab10_status_v1.json'
BASE=json.loads((D/'r3_targeted_add_graduation10_v1.json').read_text(encoding='utf-8'))
COHORT=BASE['selected']; BASE_ROWS={int(r['marketId']):r for r in BASE['rows']}
POST=D/'r3_post_add_our_state_student_v2_stream.joblib'; STABLE_ADD=joblib.load(D/'r3_stable_add_eligibility_pilot500_v2_normalized.joblib'); STABLE_EXP=joblib.load(D/'r3_stable_expansion_teacher_v1_full.joblib'); EARLY=joblib.load(D/'r3_early_recovery_continuation_pilot500_v2_hftscale.joblib')

def st(a):
 a=[float(x) for x in a];s=sorted(a);n=len(a)
 return {'n':n,'sum':sum(a),'mean':sum(a)/n,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2,'min':min(a),'max':max(a),'positiveRate':sum(x>0 for x in a)/n}
def dd(a):
 eq=peak=mdd=0.;pi=ti=-1;pk=-1
 for i,z in enumerate(a):
  eq+=float(z)
  if eq>peak:peak=eq;pk=i
  if peak-eq>mdd:mdd=peak-eq;pi=pk;ti=i
 return {'maxDrawdown':mdd,'endingEquity':eq,'peakIndex':pi,'troughIndex':ti}
def save(state,rows,errs):
 rep={'version':'R3S_RUNTIME_TRIPLE_CONFIRM_AB10_V1','state':state,'researchOnly':True,'cohort':COHORT,'policy':{'stableAddLt':.5,'stableExpansionLt':.5,'utilityLe':0.0,'earlyRecovery15Lt':.5,'containmentAfterMs':12000,'repairFractionResidual':.75},'rows':rows,'errors':errs}
 if rows:
  bp=[r['baselinePnl'] for r in rows];cp=[r['candidatePnl'] for r in rows]
  rep['summary']={'baseline':{'pnl':st(bp),'maxDrawdown':dd(bp)},'candidate':{'pnl':st(cp),'maxDrawdown':dd(cp)},'deltaPnl':st([c-b for b,c in zip(bp,cp)]),'candidateBetter':sum(c>b+1e-9 for b,c in zip(bp,cp)),'candidateWorse':sum(c<b-1e-9 for b,c in zip(bp,cp)),'same':sum(abs(c-b)<=1e-9 for b,c in zip(bp,cp)),'triggeredMarkets':[r['marketId'] for r in rows if r['triggered']]}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); STATUS.write_text(json.dumps({'state':state,'completed':len(rows),'errors':len(errs),'artifact':str(OUT)},indent=2),encoding='utf-8')

def main():
 rows=[];errs={};ww=winners(COHORT);save('RUNNING',rows,errs)
 # runtime approximation uses live-generated R3 episode evidence, not precomputed trigger files.
 for mid in COHORT:
  try:
   base=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True)
   # derive first filled ADD strict-past features from this run through the same shadow adapters; these tools only read base-run fills.
   import subprocess
   for sc in ['tools/shadow_r3_stable_add_hft_market_v2_normalized.py','tools/shadow_r3_stable_expansion_hft_market_v1.py','tools/shadow_r3_early_recovery_hft_market_v2_hftscale.py']:
    subprocess.run([sys.executable,sc,'--market-id',str(mid)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
   sa=json.loads((D/f'r3_stable_add_hft_shadow_market{mid}_v2_normalized.json').read_text(encoding='utf-8'))
   se=json.loads((D/f'r3_stable_expansion_hft_shadow_market{mid}_v1.json').read_text(encoding='utf-8'))
   er=json.loads((D/f'r3_early_recovery_hft_shadow_market{mid}_v2_hftscale.json').read_text(encoding='utf-8'))
   ps=[x.get('pStableAddEligibilityV2') for x in sa.get('structuralAddAttempts',[]) if x.get('result')=='FILLED']; pe=[x.get('pStableExpand') for x in se.get('structuralAddAttempts',[]) if x.get('result')=='FILLED']; uu=[x.get('predUtility5s') for x in se.get('structuralAddAttempts',[]) if x.get('result')=='FILLED']
   p15=[]
   for a in er.get('attempts',[]):
    if a.get('result')!='FILLED':continue
    for s in a.get('scores',[]):
     if s.get('horizonMs')==15000:p15.append(s.get('pEarlyRecovery'))
   stable=min([x for x in ps if x is not None],default=1.0); expand=min([x for x in pe if x is not None],default=1.0); util=min([x for x in uu if x is not None],default=1.0); early=min([x for x in p15 if x is not None],default=1.0)
   trig=stable<.5 and expand<.5 and util<=0.0 and early<.5
   cand=base
   if trig: cand=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,containment_after_add_ms=12000,containment_repair_fraction=.75)
   w=ww[mid];bp=realized_pnl(base['studentRollout'],w);cp=realized_pnl(cand['studentRollout'],w)
   bf=(base['studentRollout'].get('finalPortfolio') or {});cf=(cand['studentRollout'].get('finalPortfolio') or {})
   rows.append({'marketId':mid,'triggered':trig,'evidence':{'pStableAdd':stable,'pStableExpand':expand,'utility':util,'pEarly15':early},'baselinePnl':bp,'candidatePnl':cp,'baselineFloor':bf.get('worst_case_floor'),'candidateFloor':cf.get('worst_case_floor'),'baselineAbsNet':bf.get('combined_abs_net'),'candidateAbsNet':cf.get('combined_abs_net'),'containmentEvents':cand.get('containmentEvents',[])})
  except Exception as e: errs[str(mid)]=repr(e)
  save('RUNNING',rows,errs)
 save('COMPLETE',rows,errs);print(json.dumps({'ok':True,'artifact':str(OUT),'completed':len(rows),'errors':len(errs)}))
if __name__=='__main__':main()
