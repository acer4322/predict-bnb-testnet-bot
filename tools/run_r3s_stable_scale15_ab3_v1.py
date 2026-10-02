from __future__ import annotations
import json,sys,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_stable_execution_school_v1 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0'; BASE=json.loads((D/'r2_vs_r3_active_hft_ab10_v1.json').read_text(encoding='utf-8')); B={int(x['marketId']):x for x in BASE['rows']}
MIDS=[1677482,1679102,1678736]; POST=D/'r3_post_add_our_state_student_v2_stream.joblib'; ST=D/'r3_stable_expansion_teacher_v1_full.joblib'; rows=[];errs={}; ww=winners(MIDS)
def fv(d,k):
 try:return float(d.get(k) or 0.)
 except:return 0.
for m in MIDS:
 try:
  r=h.run_market(m,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,stable_expansion_artifact=ST,stable_expansion_control=True,stable_expansion_bad_add_scale=0.15)
  sr=r['studentRollout']; fp=sr.get('finalPortfolio') or {}; b=B[m]; p=realized_pnl(sr,ww[m]); rows.append({'marketId':m,'winner':ww[m],'baselineR3Pnl':b['r3Pnl'],'r3sPnl':p,'deltaVsR3':p-b['r3Pnl'],'baselineFloor':b['r3Floor'],'r3sFloor':fv(fp,'worst_case_floor'),'baselineAbsNet':b['r3AbsNet'],'r3sAbsNet':fv(fp,'combined_abs_net'),'stableScaleEvents':r.get('stableExpansionGateEvents') or [],'takerShares':float(sr.get('takerFilledShares') or 0.0),'postAddVetoes':len(r.get('postAddGateEvents') or [])})
 except Exception: errs[str(m)]=traceback.format_exc()
rep={'version':'R3S_STABLE_SCALE15_AB3_V1','state':'COMPLETE','researchOnly':True,'policy':'only negative-risk-adjusted structural ADD scaled to 15%; positive ADD and REPAIR unchanged','markets':MIDS,'errors':errs,'summary':{'baselinePnl':sum(x['baselineR3Pnl'] for x in rows),'r3sPnl':sum(x['r3sPnl'] for x in rows),'delta':sum(x['deltaVsR3'] for x in rows),'scaleEvents':sum(len(x['stableScaleEvents']) for x in rows)},'rows':rows}
(D/'r3s_stable_scale15_ab3_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3s_stable_scale15_ab3_v1.json','completed':len(rows),'errors':len(errs),'delta':rep['summary']['delta']}))
