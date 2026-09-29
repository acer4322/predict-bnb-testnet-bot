from __future__ import annotations
import argparse,json,sys,math
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss

SCENARIOS={
 'EARLY_ACTIVE':('EARLY_ACTIVE',25,25,30,30,35),
 'LATE_ACTIVE':('LATE_ACTIVE',45,45,50,50,60),
 'LATE_HOLD_ONLY':('LATE_HOLD_ONLY',60,60,65,65,75),
}

def fit_soft(tr,features,q,balanced,seed):
    fs=a.usable(tr,features);X=a.num(tr,fs);q=np.clip(np.asarray(q,float),1e-5,1-1e-5)
    if balanced: wa,wh=a.effective_class_weights(q)
    else: wa=wh=1.0
    X2=pd.concat([X,X],ignore_index=True);y=np.concatenate([np.repeat('ACT',len(tr)),np.repeat('HOLD',len(tr))]);w=np.concatenate([q*wa,(1-q)*wh])
    m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=18,l2_regularization=1.0,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=seed);m.fit(X2,y,sample_weight=w)
    return m,fs,wa,wh

def correct_prior(p,wa,wh):
    p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
    aa=p/wa;hh=(1-p)/wh
    return aa/(aa+hh)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('scenario',choices=SCENARIOS);args=ap.parse_args()
    d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['exact_act']=d.teacher_mode.ne('HOLD').astype(int);d,_=a.align_soft_teacher(d);features=ss.CURRENT+mem
    name,train_hi,val_lo,val_hi,test_lo,test_hi=SCENARIOS[args.scenario];ms=a.market_order(d);ids=ms.market_id.astype(int).tolist();tr=d[d.market_id.astype(int).isin(set(ids[:train_hi]))&d.teacher_soft_act.notna()].copy();va=d[d.market_id.astype(int).isin(set(ids[val_lo:val_hi]))].copy();te=d[d.market_id.astype(int).isin(set(ids[test_lo:test_hi]))].copy();q=tr.teacher_soft_act.astype(float).to_numpy()
    mb,fb,wa,wh=fit_soft(tr,features,q,True,20260820);mu,fu,_,_=fit_soft(tr,features,q,False,20260821)
    res={}
    for sn,x in [('validation',va),('test',te)]:
        pb=a.p_act(mb,x,fb);pc=correct_prior(pb,wa,wh);pu=a.p_act(mu,x,fu)
        res[sn]={'BALANCED_RAW':a.metric_exact(x.exact_act.astype(int),pb),'PRIOR_CORRECTED':a.metric_exact(x.exact_act.astype(int),pc),'UNWEIGHTED_SOFT':a.metric_exact(x.exact_act.astype(int),pu)}
    rep={'reportVersion':'STUDENT_ACT_PRIOR_CORRECTION_V3','researchOnly':True,'scenario':name,'train':{'markets':train_hi,'rows':len(tr),'exactActRate':float(tr.exact_act.mean()),'softTeacherMeanPAct':float(tr.teacher_soft_act.mean()),'effectiveWeights':{'ACT':wa,'HOLD':wh},'rawPThresholdEquivalentToCorrected050':float(wa/(wa+wh))},'rowCounts':{'validation':len(va),'test':len(te)},'results':res,'interpretationRule':'Prior correction is accepted only if active AUC/AP are preserved by construction and Brier/logloss/ACT-rate move toward exact behavior without using validation/test labels to choose a threshold. HOLD-only test is safety-only.','guards':['No winner/PnL','No special','No final75-99','No test-tuned threshold','No runtime changes']}
    out=a.OUT/f'student_act_prior_correction_v3_{name.lower()}.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
