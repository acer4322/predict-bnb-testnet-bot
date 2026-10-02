from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_containment_smoke_v1 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0'; POST=D/'r3_post_add_our_state_student_v2_stream.joblib'; CO=json.loads((D/'r3_targeted_add_graduation10_v1.json').read_text(encoding='utf-8'))['selected']
def one(mid,mode):
 r=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,taker_wtp_mode=mode)
 p=r['studentRollout']['finalPortfolio'];return r,{'floor':float(p.get('worst_case_floor') or 0),'absNet':float(p.get('combined_abs_net') or 0),'makerShares':float(r['studentRollout'].get('makerFilledShares') or 0),'takerShares':float(r['studentRollout'].get('takerFilledShares') or 0),'takerFills':int(r['studentRollout'].get('takerFills') or 0)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=5);ap.add_argument('--offset',type=int,default=0);a=ap.parse_args(); mids=CO[a.offset:a.offset+a.limit];ww=winners(mids);rows=[];errs={}
 tag=f'{a.offset}_{a.offset+a.limit}' if a.offset else f'{a.limit}'; out=D/f'r3s_wtp_v1_ab{tag}.json';status=D/f'r3s_wtp_v1_ab{tag}_status.json'
 for mid in mids:
  try:
   A,sa=one(mid,'legacy');B,sb=one(mid,'r3_wtp_v1');pa=realized_pnl(A['studentRollout'],ww[mid]);pb=realized_pnl(B['studentRollout'],ww[mid]);rows.append({'marketId':mid,'legacyPnl':pa,'wtpPnl':pb,'deltaPnl':pb-pa,'legacy':sa,'wtp':sb,'wtpEvents':B.get('wtpEvents',[])})
  except Exception as e:errs[str(mid)]=repr(e)
  status.write_text(json.dumps({'state':'RUNNING','completed':len(rows),'errors':len(errs),'artifact':str(out)},indent=2),encoding='utf-8')
 lp=[r['legacyPnl'] for r in rows];wp=[r['wtpPnl'] for r in rows];rep={'version':'R3S_WTP_EPISODE_ANCHOR_V1_AB','limit':a.limit,'rows':rows,'errors':errs,'summary':{'legacyPnl':sum(lp),'wtpPnl':sum(wp),'deltaPnl':sum(wp)-sum(lp),'legacyWins':sum(x>0 for x in lp),'wtpWins':sum(x>0 for x in wp),'better':sum(r['deltaPnl']>1e-9 for r in rows),'worse':sum(r['deltaPnl']<-1e-9 for r in rows),'same':sum(abs(r['deltaPnl'])<=1e-9 for r in rows),'floorBetter':sum(r['wtp']['floor']>r['legacy']['floor']+1e-9 for r in rows),'floorWorse':sum(r['wtp']['floor']<r['legacy']['floor']-1e-9 for r in rows)}};out.write_text(json.dumps(rep,indent=2),encoding='utf-8');status.write_text(json.dumps({'state':'COMPLETE','completed':len(rows),'errors':len(errs),'artifact':str(out)},indent=2),encoding='utf-8');print(json.dumps({'ok':not errs,'artifact':str(out),'summary':rep['summary']}))
if __name__=='__main__':main()
