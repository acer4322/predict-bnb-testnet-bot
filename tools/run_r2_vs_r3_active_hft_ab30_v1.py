from __future__ import annotations
import json,sqlite3,math,sys,time,traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0'; TAPE=ROOT/'data/execution_tape_v1/markets'; DB=ROOT/'data/strategy_target_compare_v1.db'; ART=D/'r3_post_add_our_state_student_v2_stream.joblib'; OUT=D/'r2_vs_r3_active_hft_ab30_v1.json'; CK=D/'r2_vs_r3_active_hft_ab30_v1_checkpoint.json'

def cohort():
 con=sqlite3.connect(DB)
 mids=[int(r[0]) for r in con.execute("select distinct market_id from our_decisions where strategy_version=? order by market_id desc",(h.VERSION,)).fetchall()]
 con.close(); mids=[m for m in mids if (TAPE/f'{m}.json.xz').exists()]
 ww=winners(mids[:400])
 out=[]
 for m in mids:
  if m in ww:
   out.append(m)
   if len(out)>=30:break
 return sorted(out),ww

def one(mid):
 try:
  a=h.run_market(mid,taker_sizing_mode='fixed')
  b=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=ART,post_add_lifecycle_control=True)
  return mid,a,b,None
 except Exception as e:
  return mid,None,None,traceback.format_exc()

def row(mid,a,b,w):
 ar=a['studentRollout']; br=b['studentRollout']; ap=ar.get('finalPortfolio') or {}; bp=br.get('finalPortfolio') or {}
 pa=realized_pnl(ar,w); pb=realized_pnl(br,w)
 def f(x,k):
  try:return float(x.get(k) or 0.0)
  except:return 0.0
 return {'marketId':mid,'winner':w,'r2Pnl':pa,'r3Pnl':pb,'deltaPnl':pb-pa,'r2Floor':f(ap,'worst_case_floor'),'r3Floor':f(bp,'worst_case_floor'),'deltaFloor':f(bp,'worst_case_floor')-f(ap,'worst_case_floor'),'r2Upside':f(ap,'best_case_pnl'),'r3Upside':f(bp,'best_case_pnl'),'r2AbsNet':f(ap,'combined_abs_net'),'r3AbsNet':f(bp,'combined_abs_net'),'r2TakerFills':int(ar.get('takerFills') or 0),'r3TakerFills':int(br.get('takerFills') or 0),'r2TakerShares':float(ar.get('takerFilledShares') or 0),'r3TakerShares':float(br.get('takerFilledShares') or 0),'r2MakerFilledShares':float(ar.get('makerFilledShares') or 0),'r3MakerFilledShares':float(br.get('makerFilledShares') or 0),'r3GateVetoes':len(b.get('postAddGateEvents') or [])}

def stats(vals):
 x=[float(v) for v in vals if v is not None and math.isfinite(float(v))]
 if not x:return {}
 s=sorted(x); n=len(s)
 return {'n':n,'sum':sum(x),'mean':sum(x)/n,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2,'min':min(x),'max':max(x),'std':(sum((z-sum(x)/n)**2 for z in x)/n)**0.5,'positiveRate':sum(z>0 for z in x)/n}

def maxdd(vals):
 eq=peak=0.;mdd=0.;start=end=-1;peak_i=-1
 for i,v in enumerate(vals):
  eq+=v
  if eq>peak: peak=eq;peak_i=i
  dd=peak-eq
  if dd>mdd:mdd=dd;start=peak_i;end=i
 return {'maxDrawdown':mdd,'peakIndex':start,'troughIndex':end,'endingEquity':eq}

def main():
 mids,ww=cohort(); print('COHORT',mids,flush=True)
 raw={}; errors={}
 with ProcessPoolExecutor(max_workers=2) as ex:
  futs={ex.submit(one,m):m for m in mids}
  for fut in as_completed(futs):
   mid,a,b,e=fut.result()
   if e: errors[str(mid)]=e
   else: raw[mid]=(a,b); print('DONE',mid,flush=True)
   CK.write_text(json.dumps({'cohort':mids,'done':sorted(raw),'errors':errors},indent=2),encoding='utf-8')
 rows=[row(m,*raw[m],ww[m]) for m in mids if m in raw]
 r2=[x['r2Pnl'] for x in rows];r3=[x['r3Pnl'] for x in rows]
 rep={'version':'R2_VS_R3_ACTIVE_HFT_AB30_V1','researchOnly':True,'cohortContract':'Latest 30 chronological historical markets with current Frozen R2 decisions, Execution Tape archive, and offline settled winner; chosen before A/B outcomes.','cohort':mids,'completed':len(rows),'errors':errors,'R2':'Frozen R2 HFT, legacy fixed Taker sizing','R3':'Current action-authorized R3-active research stack: strict-past dynamic raw-Q sizing + structural effect + post-ADD V2 re-ADD-only lifecycle. Formation-conditioned ADD pilot is NOT action-authorized and is excluded from economic A/B.','boundaries':['HFT absolute PnL is a relative mechanism/stability diagnostic, not true-market PnL.','Winner is read only after rollouts for offline settlement scoring.','No dream fill.','No fixed18 cap in R3.'],'summary':{'r2Pnl':stats(r2),'r3Pnl':stats(r3),'deltaPnl':stats([x['deltaPnl'] for x in rows]),'r2MaxDrawdown':maxdd(r2),'r3MaxDrawdown':maxdd(r3),'r2Floor':stats([x['r2Floor'] for x in rows]),'r3Floor':stats([x['r3Floor'] for x in rows]),'r2AbsNet':stats([x['r2AbsNet'] for x in rows]),'r3AbsNet':stats([x['r3AbsNet'] for x in rows]),'r2TakerShares':stats([x['r2TakerShares'] for x in rows]),'r3TakerShares':stats([x['r3TakerShares'] for x in rows]),'trajectoryChangedMarkets':sum(abs(x['deltaPnl'])>1e-9 or abs(x['deltaFloor'])>1e-9 for x in rows),'r3BetterPnlMarkets':sum(x['deltaPnl']>1e-9 for x in rows),'r3WorsePnlMarkets':sum(x['deltaPnl']<-1e-9 for x in rows),'r3BetterFloorMarkets':sum(x['deltaFloor']>1e-9 for x in rows),'r3WorseFloorMarkets':sum(x['deltaFloor']<-1e-9 for x in rows)},'rows':rows}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep['summary'],indent=2),flush=True)
if __name__=='__main__':main()
