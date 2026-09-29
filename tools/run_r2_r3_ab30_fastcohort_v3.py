from __future__ import annotations
import sys,json,traceback,warnings,math
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0';ART=D/'r3_post_add_our_state_student_v2_stream.joblib';OUT=D/'r2_vs_r3_active_hft_ab30_fastcohort_v3.json';STATUS=D/'r2_vs_r3_active_hft_ab30_fastcohort_v3_status.json'
MIDS=[1679102,1679098,1679097,1678739,1678736,1678735,1678695,1678394,1677654,1677482,1677380,1677368,1677345,1677242,1677230,1677226,1676850,1676837,1676834,1676832,1676476,1676473,1676472,1676462,1676458,1676312,1676302,1676297,1676296,1676286]
def runone(m):
 try:
  a=h.run_market(m,taker_sizing_mode='fixed'); b=h.run_market(m,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=ART,post_add_lifecycle_control=True); return m,a,b,None
 except Exception:return m,None,None,traceback.format_exc()
def fv(d,k):
 try:return float(d.get(k) or 0.)
 except:return 0.
def mkrow(m,a,b,w):
 ar=a['studentRollout'];br=b['studentRollout'];ap=ar.get('finalPortfolio') or {};bp=br.get('finalPortfolio') or {};pa=realized_pnl(ar,w);pb=realized_pnl(br,w)
 return {'marketId':m,'winner':w,'r2Pnl':pa,'r3Pnl':pb,'deltaPnl':pb-pa,'r2Floor':fv(ap,'worst_case_floor'),'r3Floor':fv(bp,'worst_case_floor'),'r2Upside':fv(ap,'best_case_pnl'),'r3Upside':fv(bp,'best_case_pnl'),'r2AbsNet':fv(ap,'combined_abs_net'),'r3AbsNet':fv(bp,'combined_abs_net'),'r2TakerFills':int(ar.get('takerFills') or 0),'r3TakerFills':int(br.get('takerFills') or 0),'r2TakerShares':float(ar.get('takerFilledShares') or 0),'r3TakerShares':float(br.get('takerFilledShares') or 0),'r2MakerShares':float(ar.get('makerFilledShares') or 0),'r3MakerShares':float(br.get('makerFilledShares') or 0),'r3GateVetoes':len(b.get('postAddGateEvents') or [])}
def stats(x):
 x=[float(z) for z in x if z is not None and math.isfinite(float(z))];s=sorted(x);n=len(s)
 if not n:return {}
 mu=sum(x)/n;return {'n':n,'sum':sum(x),'mean':mu,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2,'min':min(x),'max':max(x),'std':(sum((z-mu)**2 for z in x)/n)**.5,'positiveRate':sum(z>0 for z in x)/n}
def dd(x):
 eq=peak=mdd=0.;pi=ti=-1;pk=-1
 for i,z in enumerate(x):
  eq+=z
  if eq>peak:peak=eq;pk=i
  if peak-eq>mdd:mdd=peak-eq;pi=pk;ti=i
 return {'maxDrawdown':mdd,'peakIndex':pi,'troughIndex':ti,'endingEquity':eq}
def save(rows,errs,state):
 ordered=sorted(rows,key=lambda r:MIDS.index(r['marketId'])); r2=[x['r2Pnl'] for x in ordered];r3=[x['r3Pnl'] for x in ordered]
 rep={'version':'R2_VS_R3_ACTIVE_HFT_AB30_FASTCOHORT_V3','state':state,'researchOnly':True,'cohortContract':'30 chronological historical current-R2 replayable markets from 1676xxx-1679xxx era, selected by data availability before outcomes; excludes pathological >2min/tape recent cohort for runtime feasibility, not by performance.','cohort':MIDS,'completed':len(ordered),'errors':errs,'R2':'Frozen R2 fixed-18 HFT baseline','R3':'action-authorized R3-active stack: strict-past raw-Q + structural effect + post-ADD V2 re-ADD-only lifecycle; Formation-conditioned ADD remains shadow/not economic authority','boundaries':['No dream fill','HFT absolute PnL diagnostic only','Winner post-hoc only','R3 no fixed18 cap'],'summary':{'r2Pnl':stats(r2),'r3Pnl':stats(r3),'deltaPnl':stats([x['deltaPnl'] for x in ordered]),'r2Drawdown':dd(r2),'r3Drawdown':dd(r3),'r2Floor':stats([x['r2Floor'] for x in ordered]),'r3Floor':stats([x['r3Floor'] for x in ordered]),'r2AbsNet':stats([x['r2AbsNet'] for x in ordered]),'r3AbsNet':stats([x['r3AbsNet'] for x in ordered]),'r2TakerShares':stats([x['r2TakerShares'] for x in ordered]),'r3TakerShares':stats([x['r3TakerShares'] for x in ordered]),'r3BetterPnl':sum(x['deltaPnl']>1e-9 for x in ordered),'r3WorsePnl':sum(x['deltaPnl']<-1e-9 for x in ordered),'samePnl':sum(abs(x['deltaPnl'])<=1e-9 for x in ordered),'r3BetterFloor':sum(x['r3Floor']>x['r2Floor']+1e-9 for x in ordered),'r3WorseFloor':sum(x['r3Floor']<x['r2Floor']-1e-9 for x in ordered),'trajectoryChanged':sum(abs(x['deltaPnl'])>1e-9 or abs(x['r3Floor']-x['r2Floor'])>1e-9 for x in ordered)},'rows':ordered};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');STATUS.write_text(json.dumps({'state':state,'completed':len(ordered),'errors':len(errs)},indent=2),encoding='utf-8')
def main():
 ww=winners(MIDS);rows=[];errs={};save(rows,errs,'RUNNING')
 with ProcessPoolExecutor(max_workers=4) as ex:
  fs={ex.submit(runone,m):m for m in MIDS}
  for f in as_completed(fs):
   m,a,b,e=f.result()
   if e:errs[str(m)]=e
   else:rows.append(mkrow(m,a,b,ww[m]))
   save(rows,errs,'RUNNING');print('DONE',m,'completed',len(rows),'errors',len(errs),flush=True)
 save(rows,errs,'DONE')
if __name__=='__main__':main()
