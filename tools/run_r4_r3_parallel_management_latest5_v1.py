from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json,lzma,sqlite3,joblib
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
FULL=STACK['features']['full']; classes=list(STACK['classes'])
ci=classes.index('CONTINUE_WEAK')
m1=STACK['M1_model']; tm=TRANS['model']
c=sqlite3.connect(ROOT/'data/hft_forward_paper_v1.db')
mids=[int(r[0]) for r in c.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id desc limit 5")]
c.close()
r3db=sqlite3.connect(ROOT/'data/r3_dual_paper_shadow_v2.db'); r3db.row_factory=sqlite3.Row
market_rows=[]; all_rows=[]
for mid in mids:
    p=ROOT/'data/hft_forward_paper_v1/markets'/f'{mid}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists():
        market_rows.append({'marketId':mid,'status':'NO_ARCHIVE'}); continue
    with lzma.open(p,'rt',encoding='utf-8') as fh:d=json.load(fh)
    rep=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True)
    df=pd.DataFrame(rep.get('managementShadowRows') or []).replace([np.inf,-np.inf],np.nan)
    df=df[(df.seconds_left>=60)&(df.seconds_left<=300)&(df.build_now==1)].dropna(subset=FULL).copy()
    if df.empty:
        market_rows.append({'marketId':mid,'status':'NO_MGMT_ROWS'}); continue
    p1=m1.predict_proba(df[FULL]); df['multi_risk']=1.0-p1[:,ci]
    pt=tm.predict_proba(df[FULL]); pos=list(tm.classes_).index(1) if 1 in list(tm.classes_) else 1; df['transition_risk']=pt[:,pos]
    df['management_risk']=0.5*df.multi_risk+0.5*df.transition_risk
    df['repairStall5s']=((df.futureWeakMakerFill5s.astype(int)==0)&(df.floorImproved5s.astype(int)==0)&(df.absNetReduced5s.astype(int)==0)).astype(int)
    fail=(1-df.futureWeakMakerFill5s.astype(int))+(1-df.floorImproved5s.astype(int))+(1-df.absNetReduced5s.astype(int))
    df['repairBad2of3_5s']=(fail>=2).astype(int)
    df['marketId']=mid; all_rows.append(df)
    rr=r3db.execute("select * from r3_hft_control_runs_v2 where market_id=? and strategy_version='R3_FORMATION_R21_CONTEXT_V4' and status='COMPLETE'",(mid,)).fetchone()
    def auc(y,s): return float(roc_auc_score(y,s)) if len(set(map(int,y)))>1 else None
    market_rows.append({'marketId':mid,'status':'COMPLETE','managementRows':int(len(df)),'meanManagementRisk':float(df.management_risk.mean()),'highRiskFraction':float((df.management_risk>=.5).mean()),'stallRate':float(df.repairStall5s.mean()),'bad2of3Rate':float(df.repairBad2of3_5s.mean()),'stallAuc':auc(df.repairStall5s,df.management_risk),'bad2of3Auc':auc(df.repairBad2of3_5s,df.management_risk),'r3FinalAbsNet':None if rr is None else float(rr['final_abs_net']),'r3WorstCaseFloor':None if rr is None else float(rr['worst_case_floor']),'r3MakerFilledShares':None if rr is None else float(rr['maker_filled_shares']),'r3TakerFills':None if rr is None else int(rr['taker_fills'])})
r3db.close()
A=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
pooled={}
if not A.empty:
    for lab in ['repairStall5s','repairBad2of3_5s']:
        y=A[lab].astype(int); s=A.management_risk
        pooled[lab]={'n':int(len(A)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,s)) if y.nunique()>1 else None,'ap':float(average_precision_score(y,s)) if y.sum() else None,'lowQuartileRate':float(y[s<=s.quantile(.25)].mean()),'highQuartileRate':float(y[s>=s.quantile(.75)].mean())}
    A.to_csv(ROOT/'data/research/r4_v0/hourly/r4_r3_parallel_management_risk_latest5_v1_rows.csv',index=False)
out={'version':'R4_R3_PARALLEL_MANAGEMENT_RISK_LATEST5_V1','researchOnly':True,'actionAuthority':False,'r3Lane':'R3_FORMATION_R21_CONTEXT_V4','managementLane':'STACK_V4_M1 + TRANSITION_BELIEF_V1 50/50 risk belief','marketIds':mids,'markets':market_rows,'pooled':pooled,'note':'Management lane is shadow-only; R3 execution is untouched. Same HFT markets/checkpoints are used for scoring.'}
path=ROOT/'data/research/r4_v0/hourly/r4_r3_parallel_management_risk_latest5_v1.json'
path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
