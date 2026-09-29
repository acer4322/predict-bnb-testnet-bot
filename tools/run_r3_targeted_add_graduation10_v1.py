from __future__ import annotations
import sys,json,math,traceback,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0'; OUT=D/'r3_targeted_add_graduation10_v1.json'; STATUS=D/'r3_targeted_add_graduation10_status_v1.json'
POST=D/'r3_post_add_our_state_student_v2_stream.joblib'
# Fixed before outcomes. Ordered by recency/availability; older known replayable markets appended only to reach 10 ADD-active cases.
CAND=[1679102,1679098,1679097,1678739,1678736,1678735,1678695,1678394,1677654,1677482,1677380,1677368,1677345,1677242,1677230,1677226,1676850,1676837,1676834,1676832,1676476,1676473,1676472,1676462,1676458,1676312,1676302,1676297,1676296,1676286,1675579,1674459,1667540,1663753,1649744,1649545]
def st(x):
 x=[float(z) for z in x if z is not None and math.isfinite(float(z))]; s=sorted(x);n=len(s)
 if not n:return {}
 mu=sum(x)/n
 return {'n':n,'sum':sum(x),'mean':mu,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2,'min':min(x),'max':max(x),'positiveRate':sum(z>0 for z in x)/n}
def dd(x):
 eq=peak=mdd=0.;pi=ti=-1;pk=-1
 for i,z in enumerate(x):
  eq+=float(z)
  if eq>peak: peak=eq;pk=i
  if peak-eq>mdd: mdd=peak-eq;pi=pk;ti=i
 return {'maxDrawdown':mdd,'endingEquity':eq,'peakIndex':pi,'troughIndex':ti}
def fp(sr,k):
 try:return float((sr.get('finalPortfolio') or {}).get(k) or 0.)
 except:return 0.
def save(state,selected,rows,errors,scan):
 rep={'version':'R3_TARGETED_ADD_GRADUATION10_V1','state':state,'researchOnly':True,'selectionContract':'Fixed candidate list before outcomes; take first 10 replayable markets where current R3 baseline produces >=1 FILLED structural ADD. Selection ignores winner/PnL.','candidateOrder':CAND,'selected':selected,'completed':len(rows),'errors':errors,'scan':scan,'boundaries':['No dream fill','HFT absolute PnL relative diagnostic only','Winner post-hoc only','R3 no fixed18 cap','R3-S Stable-ADD/Early-Recovery remain shadow and are not economic authority'], 'rows':rows}
 if rows:
  r2=[x['r2Pnl'] for x in rows];r3=[x['r3Pnl'] for x in rows]
  rep['summary']={'R2':{'pnl':st(r2),'maxDrawdown':dd(r2),'floor':st([x['r2Floor'] for x in rows]),'absNet':st([x['r2AbsNet'] for x in rows]),'takerShares':st([x['r2TakerShares'] for x in rows])},'R3':{'pnl':st(r3),'maxDrawdown':dd(r3),'floor':st([x['r3Floor'] for x in rows]),'absNet':st([x['r3AbsNet'] for x in rows]),'takerShares':st([x['r3TakerShares'] for x in rows])},'deltaPnl':st([x['r3Pnl']-x['r2Pnl'] for x in rows]),'r3Better':sum(x['r3Pnl']>x['r2Pnl']+1e-9 for x in rows),'r3Worse':sum(x['r3Pnl']<x['r2Pnl']-1e-9 for x in rows),'same':sum(abs(x['r3Pnl']-x['r2Pnl'])<=1e-9 for x in rows)}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); STATUS.write_text(json.dumps({'state':state,'selected':len(selected),'completed':len(rows),'scanned':len(scan),'errors':len(errors),'artifact':str(OUT)},indent=2),encoding='utf-8')
def main():
 selected=[]; cached={}; errors={};scan=[];save('SCANNING',selected,[],errors,scan)
 for m in CAND:
  if len(selected)>=10:break
  try:
   r3=h.run_market(m,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True)
   adds=[a for a in r3.get('takerAttempts',[]) if a.get('structuralEffect')=='ADD_EFFECT' and a.get('result')=='FILLED']
   scan.append({'marketId':m,'filledStructuralAdds':len(adds)})
   if adds: selected.append(m);cached[m]=r3
  except Exception as e: errors[str(m)]='SCAN:'+repr(e)
  save('SCANNING',selected,[],errors,scan)
 rows=[]
 ww=winners(selected)
 save('RUNNING_AB',selected,rows,errors,scan)
 for m in selected:
  try:
   r3=cached[m];r2=h.run_market(m,taker_sizing_mode='fixed')
   sr2=r2['studentRollout'];sr3=r3['studentRollout'];w=ww[m]
   rows.append({'marketId':m,'winner':w,'filledStructuralAdds':sum(a.get('structuralEffect')=='ADD_EFFECT' and a.get('result')=='FILLED' for a in r3.get('takerAttempts',[])),'r2Pnl':realized_pnl(sr2,w),'r3Pnl':realized_pnl(sr3,w),'r2Floor':fp(sr2,'worst_case_floor'),'r3Floor':fp(sr3,'worst_case_floor'),'r2Upside':fp(sr2,'best_case_pnl'),'r3Upside':fp(sr3,'best_case_pnl'),'r2AbsNet':fp(sr2,'combined_abs_net'),'r3AbsNet':fp(sr3,'combined_abs_net'),'r2TakerShares':float(sr2.get('takerFilledShares') or 0),'r3TakerShares':float(sr3.get('takerFilledShares') or 0),'r2MakerShares':float(sr2.get('makerFilledShares') or 0),'r3MakerShares':float(sr3.get('makerFilledShares') or 0)})
  except Exception as e: errors[str(m)]='AB:'+repr(e)
  save('RUNNING_AB',selected,rows,errors,scan)
 save('COMPLETE',selected,rows,errors,scan)
 print(json.dumps({'ok':True,'artifact':str(OUT),'selected':len(selected),'completed':len(rows),'errors':len(errors)}))
if __name__=='__main__': main()
