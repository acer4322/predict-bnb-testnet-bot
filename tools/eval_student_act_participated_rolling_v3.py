from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss
OUT=ROOT/'data'/'research'/'supervisor_options_v0';TARGET=ROOT/'data'/'target_wallet_official_v1.db'
a.TARGET_STATES=OUT/'supervisor_target_act_states_v2.csv';a.TEACHER_ART=OUT/'supervisor_target_act_large_v2_full699.joblib'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('train_n',type=int);args=ap.parse_args();n=args.train_n
 d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,_=a.align_soft_teacher(d)
 con=sqlite3.connect(f'file:{TARGET.resolve().as_posix()}?mode=ro',uri=True);participating={int(r[0]) for r in con.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and quote_type='BID'")};con.close()
 ms=a.market_order(d);pids=[int(x) for x in ms.market_id if int(x) in participating];assert n+10<=len(pids)
 tr=d[d.market_id.astype(int).isin(set(pids[:n]))&d.teacher_soft_act.notna()].copy();te=d[d.market_id.astype(int).isin(set(pids[n:n+10]))].copy();features=ss.CURRENT+mem
 hard,hf=a.fit_hard(tr,features,20260820);soft,sf=a.fit_soft_binary(tr,features,tr.teacher_soft_act.astype(float).to_numpy(),20260821);ph=a.p_act(hard,te,hf);ps=a.p_act(soft,te,sf);mh=a.metric_exact(te.exact_act.astype(int),ph);msf=a.metric_exact(te.exact_act.astype(int),ps)
 rep={'reportVersion':'STUDENT_ACT_PARTICIPATED_ROLLING_V3','researchOnly':True,'participatingMarketsTotal':len(pids),'trainMarkets':n,'testParticipatingIndices':[n,n+9],'trainRows':len(tr),'testRows':len(te),'testExactActRate':float(te.exact_act.mean()),'hard':mh,'soft':msf,'lift':{'auc':msf['auc']-mh['auc'],'ap':msf['ap']-mh['ap'],'logLoss':msf['logLoss']-mh['logLoss']},'testMarketIds':pids[n:n+10],'guards':['Confirmed Target-participating markets only','No winner/PnL','Ordinary only','No special 2026-08-16','No final75-99','No threshold sweep','No runtime changes']};p=OUT/f'student_act_participated_rolling_v3_n{n}.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
