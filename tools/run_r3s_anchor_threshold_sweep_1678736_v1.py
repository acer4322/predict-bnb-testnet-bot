from __future__ import annotations
import sys,json,traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_stable_execution_school_v1 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0';POST=D/'r3_post_add_our_state_student_v2_stream.joblib';ST=D/'r3_stable_expansion_teacher_v1_full.joblib';MID=1678736;SCALES=[0.80,0.85,0.90,0.95,1.00]
def one(sc):
 try:return sc,h.run_market(MID,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,stable_expansion_artifact=ST,stable_expansion_control=True,stable_expansion_bad_add_scale=sc),None
 except Exception:return sc,None,traceback.format_exc()
def f(d,k):
 try:return float(d.get(k) or 0.)
 except:return 0.
def main():
 w=winners([MID])[MID];rows=[];errs={}
 with ProcessPoolExecutor(max_workers=2) as ex:
  fs={ex.submit(one,s):s for s in SCALES}
  for fu in as_completed(fs):
   sc,r,e=fu.result()
   if e:errs[str(sc)]=e;continue
   sr=r['studentRollout'];p=sr.get('finalPortfolio') or {};rows.append({'scale':sc,'pnl':realized_pnl(sr,w),'floor':f(p,'worst_case_floor'),'upside':f(p,'best_case_pnl'),'absNet':f(p,'combined_abs_net'),'takerShares':float(sr.get('takerFilledShares') or 0.),'makerShares':float(sr.get('makerFilledShares') or 0.)});(D/'r3s_anchor_threshold_sweep_1678736_v1.json').write_text(json.dumps({'state':'RUNNING','rows':sorted(rows,key=lambda x:x['scale']),'errors':errs},indent=2),encoding='utf-8')
 rows.sort(key=lambda x:x['scale']);(D/'r3s_anchor_threshold_sweep_1678736_v1.json').write_text(json.dumps({'version':'R3S_ANCHOR_THRESHOLD_SWEEP_1678736_V1','state':'COMPLETE','researchOnly':True,'purpose':'mechanism-only threshold localization; not a production scale rule','marketId':MID,'winner':w,'rows':rows,'errors':errs},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3s_anchor_threshold_sweep_1678736_v1.json'}))
if __name__=='__main__':main()
