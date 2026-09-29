from __future__ import annotations
import sys,json,math,traceback,warnings
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0';ART=D/'r3_post_add_our_state_student_v2_stream.joblib'
def one(mid):
 try:
  a=h.run_market(mid,taker_sizing_mode='fixed');b=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=ART,post_add_lifecycle_control=True);return mid,a,b,None
 except Exception:return mid,None,None,traceback.format_exc()
def fv(d,k):
 try:return float(d.get(k) or 0.0)
 except:return 0.0
def main():
 mids=[int(x) for x in sys.argv[1].split(',')];out=Path(sys.argv[2]);ww=winners(mids);raw={};err={}
 with ProcessPoolExecutor(max_workers=2) as ex:
  fs={ex.submit(one,m):m for m in mids}
  for f in as_completed(fs):
   m,a,b,e=f.result();print('DONE',m,flush=True)
   if e:err[str(m)]=e
   else:raw[m]=(a,b)
 rows=[]
 for m in mids:
  if m not in raw:continue
  a,b=raw[m]; ar=a['studentRollout'];br=b['studentRollout'];ap=ar.get('finalPortfolio') or {};bp=br.get('finalPortfolio') or {};w=ww.get(m);pa=realized_pnl(ar,w);pb=realized_pnl(br,w)
  rows.append({'marketId':m,'winner':w,'r2Pnl':pa,'r3Pnl':pb,'deltaPnl':pb-pa if pa is not None and pb is not None else None,'r2Floor':fv(ap,'worst_case_floor'),'r3Floor':fv(bp,'worst_case_floor'),'r2Upside':fv(ap,'best_case_pnl'),'r3Upside':fv(bp,'best_case_pnl'),'r2AbsNet':fv(ap,'combined_abs_net'),'r3AbsNet':fv(bp,'combined_abs_net'),'r2TakerFills':int(ar.get('takerFills') or 0),'r3TakerFills':int(br.get('takerFills') or 0),'r2TakerShares':float(ar.get('takerFilledShares') or 0),'r3TakerShares':float(br.get('takerFilledShares') or 0),'r2MakerShares':float(ar.get('makerFilledShares') or 0),'r3MakerShares':float(br.get('makerFilledShares') or 0),'r3GateVetoes':len(b.get('postAddGateEvents') or [])})
 out.write_text(json.dumps({'mids':mids,'rows':rows,'errors':err},indent=2),encoding='utf-8');print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
