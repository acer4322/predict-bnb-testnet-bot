from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
a.TARGET_STATES=OUT/'supervisor_target_act_states_v2.csv'
a.TEACHER_ART=OUT/'supervisor_target_act_large_v2_full699.joblib'
SCENARIOS={
 'EARLY':('V2_EARLY',25,25,30,30,35),
 'MID':('V2_MID',45,45,50,50,60),
 'FUTURE':('V2_FUTURE',60,60,65,65,75),
}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('scenario',choices=SCENARIOS);args=ap.parse_args();d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,teacher=a.align_soft_teacher(d);r,models=a.scenario(d,ss.CURRENT+mem,*SCENARIOS[args.scenario]);name=args.scenario.lower();art=OUT/f'student_state_act_adapter_teacher_v2_{name}.joblib';report=OUT/f'student_state_act_adapter_teacher_v2_{name}_report.json';joblib.dump({'version':'STUDENT_STATE_ACT_ADAPTER_TEACHER_V2','researchOnly':True,'runtimePromotion':False,'scenario':args.scenario,'models':models,'teacherArtifact':str(a.TEACHER_ART)},art);rep={'reportVersion':'STUDENT_STATE_ACT_ADAPTER_TEACHER_V2','researchOnly':True,'teacher':'SUPERVISOR_TARGET_ACT_LARGE_V2_FULL699','targetState':'SUPERVISOR_TARGET_ACT_STATES_V2','softTeacherCoverageOverall':float(d.teacher_soft_act.notna().mean()),'softTeacherAgeMs':{'median':float(d.teacher_soft_age_ms.dropna().median()),'p90':float(d.teacher_soft_age_ms.dropna().quantile(.9))},'scenario':r,'artifact':str(art),'guards':['No winner/PnL','Ordinary only','No special 2026-08-16','No final75-99','No runtime changes']};report.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
