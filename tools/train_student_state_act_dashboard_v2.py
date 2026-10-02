from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss

OUT=a.OUT
REPORT=OUT/'student_state_act_dashboard_v2_report.json'
ART=OUT/'student_state_act_dashboard_v2.joblib'
DASH_RAW=['pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown','pTaker1s','pTaker3s','pResidualWake','pPassiveRepair','episodeActive','readiness']
SCENARIOS=[('EARLY_ACTIVE',25,25,30,30,35),('LATE_ACTIVE',45,45,50,50,60),('LATE_HOLD_ONLY',60,60,65,65,75)]

def add_dashboard(d):
    for c in DASH_RAW:
        if c not in d.columns:d[c]=np.nan
        d[c]=pd.to_numeric(d[c],errors='coerce')
    d['driver_maker_pressure_max']=d[['pMakerUp','pMakerDown']].max(axis=1)
    d['driver_maker_pressure_sum']=d[['pMakerUp','pMakerDown']].sum(axis=1,min_count=1)
    d['driver_taker_pressure_gap']=d['pTaker3s']-d['pTaker1s']
    d['driver_episode_ready']=d['episodeActive'].fillna(0)*d['readiness'].fillna(0)
    d['driver_passive_available']=d['pPassiveRepair'].notna().astype(float)
    return d,DASH_RAW+['driver_maker_pressure_max','driver_maker_pressure_sum','driver_taker_pressure_gap','driver_episode_ready','driver_passive_available']

def run_scenario(d,features,args):
    r,mods=a.scenario(d,features,*args)
    return r,mods

def main():
    d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,teacher=a.align_soft_teacher(d);d,dash=add_dashboard(d)
    common=ss.CURRENT+mem; features=common+dash
    scenarios=[];mods={}
    for sc in SCENARIOS:
        r,m=run_scenario(d,features,sc);scenarios.append(r);mods[r['name']]=m
        print(json.dumps({'progress':r['name'],'test':{k:v['test']['exact'] for k,v in r['models'].items()}},ensure_ascii=False),flush=True)
    rep={'reportVersion':'STUDENT_STATE_ACT_DASHBOARD_V2','researchOnly':True,'question':'Do runtime-safe OUR controller dashboard signals help the Student-State ACT adapter preserve active ranking while reducing HOLD false-ACT?','source':{'markets':int(d.market_id.nunique()),'rows':len(d),'softTeacherCoverage':float(d.teacher_soft_act.notna().mean())},'features':{'common':len(common),'dashboard':dash,'totalRequested':len(features)},'scenarios':scenarios,'interpretationRule':'Dashboard V2 is promising only if active AUC/AP is not degraded versus V1 and HOLD-only false-ACT materially falls. Soft teacher remains preferred over hard only if improvement is stable. No test threshold tuning.','artifact':str(ART),'guards':['No winner/PnL','Ordinary only','No 2026-08-16','No final75-99','Runtime-safe frozen controller outputs only','No runtime changes']}
    joblib.dump({'version':'STUDENT_STATE_ACT_DASHBOARD_V2','researchOnly':True,'runtimePromotion':False,'models':mods,'dashboardFeatures':dash},ART);REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
