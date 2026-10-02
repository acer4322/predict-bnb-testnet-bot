from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss

SCENARIOS={
 'EARLY_ACTIVE':('EARLY_ACTIVE',25,25,30,30,35),
 'LATE_ACTIVE':('LATE_ACTIVE',45,45,50,50,60),
 'LATE_HOLD_ONLY':('LATE_HOLD_ONLY',60,60,65,65,75),
}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('scenario',choices=SCENARIOS);args=ap.parse_args()
 d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,teacher=a.align_soft_teacher(d)
 r,models=a.scenario(d,ss.CURRENT+mem,*SCENARIOS[args.scenario])
 out=a.OUT/f'student_state_act_adapter_v1_{args.scenario.lower()}_report.json';art=a.OUT/f'student_state_act_adapter_v1_{args.scenario.lower()}.joblib'
 rep={'reportVersion':'STUDENT_STATE_ACT_ADAPTER_SCENARIO_V1','researchOnly':True,'scenario':r,'softTeacherCoverageOverall':float(d.teacher_soft_act.notna().mean()),'softTeacherCoverageTest':float(d[d.market_id.astype(int).isin(set(a.market_order(d).market_id.astype(int).tolist()[SCENARIOS[args.scenario][4]:SCENARIOS[args.scenario][5]]))].teacher_soft_act.notna().mean()),'artifact':str(art),'guards':['No winner/PnL','Ordinary only','No final75-99','No runtime changes']}
 joblib.dump({'version':'STUDENT_STATE_ACT_ADAPTER_SCENARIO_V1','researchOnly':True,'scenario':args.scenario,'models':models},art);out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
