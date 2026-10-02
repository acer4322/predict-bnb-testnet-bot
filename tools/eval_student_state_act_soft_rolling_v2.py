from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
a.TARGET_STATES=OUT/'supervisor_target_act_states_v2.csv';a.TEACHER_ART=OUT/'supervisor_target_act_large_v2_full699.joblib'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('train_n',type=int);args=ap.parse_args();n=args.train_n;assert n in (20,30,40,50,60)
 d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,_=a.align_soft_teacher(d);ids=a.market_order(d).market_id.astype(int).tolist();tr=d[d.market_id.astype(int).isin(set(ids[:n])) & d.teacher_soft_act.notna()].copy();te=d[d.market_id.astype(int).isin(set(ids[n:n+10]))].copy();features=ss.CURRENT+mem
 hard,hf=a.fit_hard(tr,features,20260820);soft,sf=a.fit_soft_binary(tr,features,tr.teacher_soft_act.astype(float).to_numpy(),20260821)
 ph=a.p_act(hard,te,hf);ps=a.p_act(soft,te,sf);rep={'reportVersion':'STUDENT_STATE_ACT_SOFT_ROLLING_V2','researchOnly':True,'trainMarkets':n,'testMarketIndices':[n,n+9],'trainRows':len(tr),'testRows':len(te),'trainExactActRate':float(tr.exact_act.mean()),'trainTeacherMeanPAct':float(tr.teacher_soft_act.mean()),'testExactActRate':float(te.exact_act.mean()),'hard':a.metric_exact(te.exact_act.astype(int),ph),'soft':a.metric_exact(te.exact_act.astype(int),ps),'lift':{'auc':(a.metric_exact(te.exact_act.astype(int),ps)['auc'] or 0)-(a.metric_exact(te.exact_act.astype(int),ph)['auc'] or 0),'ap':(a.metric_exact(te.exact_act.astype(int),ps)['ap'] or 0)-(a.metric_exact(te.exact_act.astype(int),ph)['ap'] or 0),'logLoss':a.metric_exact(te.exact_act.astype(int),ps)['logLoss']-a.metric_exact(te.exact_act.astype(int),ph)['logLoss']},'guards':['No winner/PnL','Ordinary only','No final75-99','No threshold sweep','No runtime changes']};p=OUT/f'student_state_act_soft_rolling_v2_n{n}.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
