from __future__ import annotations
import sys,json,traceback,warnings,math
from pathlib import Path
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0';ART=D/'r3_post_add_our_state_student_v2_stream.joblib';OUT=D/'r2_vs_r3_active_hft_ab30_v2_rows.json';STATUS=D/'r2_vs_r3_active_hft_ab30_v2_status.json'
MIDS=[1683220,1683186,1683175,1682081,1682079,1681958,1681874,1681707,1681675,1681337,1681311,1681130,1681127,1681111,1681055,1681052,1681051,1680996,1680988,1680987,1680977,1680974,1680969,1680842,1680800,1680792,1680670,1680667,1680509,1680499]
def fv(d,k):
 try:return float(d.get(k) or 0.0)
 except:return 0.0
def main():
 ww=winners(MIDS); rows=[];err={}
 if OUT.exists():
  z=json.loads(OUT.read_text(encoding='utf-8'));rows=z.get('rows',[]);err=z.get('errors',{})
 done={int(x['marketId']) for x in rows}|{int(x) for x in err}
 for idx,m in enumerate(MIDS):
  if m in done:continue
  STATUS.write_text(json.dumps({'state':'RUNNING','marketId':m,'index':idx,'completed':len(rows),'errors':len(err),'cohort':MIDS},indent=2),encoding='utf-8')
  try:
   a=h.run_market(m,taker_sizing_mode='fixed')
   b=h.run_market(m,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=ART,post_add_lifecycle_control=True)
   ar=a['studentRollout'];br=b['studentRollout'];ap=ar.get('finalPortfolio') or {};bp=br.get('finalPortfolio') or {};w=ww.get(m);pa=realized_pnl(ar,w);pb=realized_pnl(br,w)
   rows.append({'marketId':m,'winner':w,'r2Pnl':pa,'r3Pnl':pb,'deltaPnl':pb-pa if pa is not None and pb is not None else None,'r2Floor':fv(ap,'worst_case_floor'),'r3Floor':fv(bp,'worst_case_floor'),'r2Upside':fv(ap,'best_case_pnl'),'r3Upside':fv(bp,'best_case_pnl'),'r2AbsNet':fv(ap,'combined_abs_net'),'r3AbsNet':fv(bp,'combined_abs_net'),'r2TakerFills':int(ar.get('takerFills') or 0),'r3TakerFills':int(br.get('takerFills') or 0),'r2TakerShares':float(ar.get('takerFilledShares') or 0),'r3TakerShares':float(br.get('takerFilledShares') or 0),'r2MakerShares':float(ar.get('makerFilledShares') or 0),'r3MakerShares':float(br.get('makerFilledShares') or 0),'r3GateVetoes':len(b.get('postAddGateEvents') or [])})
  except Exception:err[str(m)]=traceback.format_exc()
  OUT.write_text(json.dumps({'version':'R2_R3_ACTIVE_HFT_AB30_V2_ROWS','cohort':MIDS,'rows':rows,'errors':err},indent=2),encoding='utf-8')
 STATUS.write_text(json.dumps({'state':'DONE','completed':len(rows),'errors':len(err),'cohort':MIDS},indent=2),encoding='utf-8')
if __name__=='__main__':main()
