from __future__ import annotations
import json, math
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, f1_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
TARGET_STATES=OUT/'supervisor_target_act_states_v1.csv'
TEACHER_ART=OUT/'supervisor_target_act_large_v1.joblib'
REPORT=OUT/'student_state_act_adapter_v1_report.json'
ART=OUT/'student_state_act_adapter_v1.joblib'
ROWS=OUT/'student_state_act_adapter_v1_states.csv'
SEED=20260820

# Reuse the already-audited OUR-state feature construction and exact Target teacher labels.
import sys
sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_supervisor_v0 as ss


def num(df,fs):
    return df.reindex(columns=fs).apply(pd.to_numeric,errors='coerce')

def usable(tr,features):
    out=[]
    for f in features:
        z=pd.to_numeric(tr[f],errors='coerce').dropna()
        if len(z)>=2 and z.nunique()>=2: out.append(f)
    return out

def effective_class_weights(q:np.ndarray):
    a=max(float(np.sum(q)),1e-6); h=max(float(np.sum(1-q)),1e-6); n=a+h
    wa=math.sqrt(n/a); wh=math.sqrt(n/h); scale=(a*wa+h*wh)/n
    return wa/scale, wh/scale

def fit_soft_binary(tr,features,q,seed):
    features=usable(tr,features); X=num(tr,features)
    q=np.clip(np.asarray(q,float),1e-5,1-1e-5)
    wa,wh=effective_class_weights(q)
    X2=pd.concat([X,X],ignore_index=True)
    y=np.concatenate([np.repeat('ACT',len(tr)),np.repeat('HOLD',len(tr))])
    w=np.concatenate([q*wa,(1-q)*wh])
    m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=18,l2_regularization=1.0,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=seed)
    m.fit(X2,y,sample_weight=w)
    return m,features

def fit_hard(tr,features,seed):
    q=tr.exact_act.astype(float).to_numpy()
    return fit_soft_binary(tr,features,q,seed)

def p_act(m,x,features):
    p=m.predict_proba(num(x,features)); cl=list(m.classes_); return p[:,cl.index('ACT')]

def metric_exact(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-6,1-1e-6); pred=(p>=.5).astype(int)
    out={'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'predictedActRate':float(pred.mean()) if len(y) else None,'meanPAct':float(p.mean()) if len(y) else None,'p90PAct':float(np.quantile(p,.9)) if len(y) else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'logLoss':float(log_loss(y,np.column_stack([1-p,p]),labels=[0,1])) if len(y) else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(y) else None,'f1':float(f1_score(y,pred,zero_division=0)) if len(y) else None}
    if len(np.unique(y))==2:
        out['auc']=float(roc_auc_score(y,p)); out['ap']=float(average_precision_score(y,p))
    else:
        out['auc']=None; out['ap']=None
    return out

def soft_metric(q,p):
    q=np.asarray(q,float);p=np.asarray(p,float)
    return {'n':int(len(q)),'maeToTeacher':float(np.mean(np.abs(q-p))) if len(q) else None,'rmseToTeacher':float(np.sqrt(np.mean((q-p)**2))) if len(q) else None,'teacherMeanPAct':float(np.mean(q)) if len(q) else None}

def align_soft_teacher(our):
    teacher=joblib.load(TEACHER_ART); tf=list(teacher['features']); tm=teacher['model']; cls=list(tm.classes_); ai=cls.index('ACT')
    t=pd.read_csv(TARGET_STATES).sort_values(['market_end_ms','checkpoint_ms']).copy()
    t['teacher_soft_act']=tm.predict_proba(num(t,tf))[:,ai]
    keep=['market_end_ms','checkpoint_ms','teacher_soft_act']
    # Strict-past asof within each shared market end. Target checkpoint at or before OUR checkpoint only.
    xs=[]
    for we,g in our.groupby('market_end_ms',sort=False):
        z=t[t.market_end_ms.astype('int64').eq(int(we))][['checkpoint_ms','teacher_soft_act']].sort_values('checkpoint_ms')
        gg=g.sort_values('checkpoint_ms').copy()
        if len(z)==0:
            gg['teacher_soft_act']=np.nan; gg['teacher_checkpoint_ms']=np.nan
        else:
            zz=z.rename(columns={'checkpoint_ms':'teacher_checkpoint_ms'})
            gg=pd.merge_asof(gg,zz,left_on='checkpoint_ms',right_on='teacher_checkpoint_ms',direction='backward',tolerance=2500)
        xs.append(gg)
    d=pd.concat(xs,ignore_index=True).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    d['teacher_soft_age_ms']=pd.to_numeric(d.checkpoint_ms,errors='coerce')-pd.to_numeric(d.teacher_checkpoint_ms,errors='coerce')
    return d,teacher

def market_order(d):
    return d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']).reset_index(drop=True)

def scenario(d,features,name,train_hi,val_lo,val_hi,test_lo,test_hi):
    ms=market_order(d); ids=ms.market_id.astype(int).tolist()
    tr_ids=set(ids[:train_hi]); va_ids=set(ids[val_lo:val_hi]); te_ids=set(ids[test_lo:test_hi])
    tr=d[d.market_id.astype(int).isin(tr_ids) & d.teacher_soft_act.notna()].copy(); va=d[d.market_id.astype(int).isin(va_ids)].copy(); te=d[d.market_id.astype(int).isin(te_ids)].copy()
    configs={
      'HARD_EXACT':tr.exact_act.astype(float).to_numpy(),
      'SOFT_TEACHER':tr.teacher_soft_act.astype(float).to_numpy(),
      'HYBRID_50_50':(.5*tr.exact_act.astype(float)+.5*tr.teacher_soft_act.astype(float)).to_numpy(),
    }
    models={};res={}
    for i,(kind,q) in enumerate(configs.items()):
        m,fs=(fit_hard(tr,features,SEED+i) if kind=='HARD_EXACT' else fit_soft_binary(tr,features,q,SEED+i))
        models[kind]={'model':m,'features':fs}
        rv={}
        for sn,x in [('validation',va),('test',te)]:
            p=p_act(m,x,fs); mask=x.teacher_soft_act.notna().to_numpy(); rv[sn]={'exact':metric_exact(x.exact_act.astype(int),p),'softTeacher':soft_metric(x.loc[mask,'teacher_soft_act'].astype(float),p[mask]) if mask.any() else None}
        res[kind]=rv
    # direct large Target-state teacher naively applied to OUR state, kept only as transfer-collapse baseline
    direct={}
    teacher=joblib.load(TEACHER_ART); tf=teacher['features']; tm=teacher['model']; ai=list(tm.classes_).index('ACT')
    usable_direct=[f for f in tf if f in d.columns]
    if len(usable_direct)==len(tf):
        for sn,x in [('validation',va),('test',te)]:
            p=tm.predict_proba(num(x,tf))[:,ai]; mask=x.teacher_soft_act.notna().to_numpy(); direct[sn]={'exact':metric_exact(x.exact_act.astype(int),p),'softTeacher':soft_metric(x.loc[mask,'teacher_soft_act'].astype(float),p[mask]) if mask.any() else None}
    return {'name':name,'marketSlices':{'train':[0,train_hi-1],'validation':[val_lo,val_hi-1],'test':[test_lo,test_hi-1]},'marketCounts':{'train':len(tr_ids),'validation':len(va_ids),'test':len(te_ids)},'rowCounts':{'train':len(tr),'validation':len(va),'test':len(te)},'teacherCounts':{'train':tr.exact_act.value_counts().to_dict(),'validation':va.exact_act.value_counts().to_dict(),'test':te.exact_act.value_counts().to_dict()},'models':res,'directTeacherOnOurState':direct},models

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,mem,files=ss.build(); d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    d['exact_act']=d.teacher_mode.ne('HOLD').astype(int)
    d,teacher=align_soft_teacher(d)
    features=ss.CURRENT+mem
    coverage=float(d.teacher_soft_act.notna().mean())
    scenarios=[];saved={}
    # Predeclared active chronology windows from the curriculum coverage audit.
    for args in [
      ('EARLY_ACTIVE',25,25,30,30,35),
      ('LATE_ACTIVE',45,45,50,50,60),
      ('LATE_HOLD_ONLY',60,60,65,65,75),
    ]:
        r,m=scenario(d,features,*args);scenarios.append(r);saved[r['name']]=m
        print(json.dumps({'progress':r['name'],'test':{k:v['test']['exact'] for k,v in r['models'].items()}},ensure_ascii=False),flush=True)
    # Final adapter artifact is NOT promoted: use the largest pre-hold training window and all three methods for research comparison.
    art={'version':'STUDENT_STATE_ACT_ADAPTER_V1','researchOnly':True,'runtimePromotion':False,'featuresSemantics':'OUR strict-past portfolio/public state; ~6/20/60s trajectory deltas','teacher':'exact Target ACT/HOLD and large Target-state ACT soft probability are labels only','models':saved['LATE_HOLD_ONLY'],'guards':['No winner/PnL.','No special 2026-08-16.','No final75-99.','No runtime changes.']}
    joblib.dump(art,ART);d.to_csv(ROWS,index=False)
    rep={'reportVersion':'STUDENT_STATE_ACT_ADAPTER_V1','researchOnly':True,'question':'Can a large Target-state ACT teacher transfer its experience into an OUR-state ACT adapter, and are soft labels better than exact hard labels?','source':{'ourMarkets':int(d.market_id.nunique()),'ourRows':len(d),'files':[Path(f).name for f in files],'softTeacherCoverage':coverage,'softTeacherAgeMs':{'median':float(d.teacher_soft_age_ms.dropna().median()),'p90':float(d.teacher_soft_age_ms.dropna().quantile(.9))},'targetTeacherMarkets':len(teacher.get('trainingMarkets',[]))},'methods':{'HARD_EXACT':'Exact Target ACT/HOLD only.','SOFT_TEACHER':'Large 489-market Target-state teacher P(ACT) distilled by weighted soft cross-entropy.','HYBRID_50_50':'Fixed 50/50 exact hard target and teacher soft probability; no sweep.'},'scenarios':scenarios,'interpretationRule':'Require adapter to improve materially over direct teacher-on-OUR collapse and remain directionally stable on both active windows; HOLD-only window is a false-ACT safety check, not an AUC test. Soft/hybrid is preferred only if it improves active-window ranking/calibration without materially worsening HOLD false-ACT behavior.','artifact':str(ART),'states':str(ROWS),'guards':['No winner/PnL labels/features.','Ordinary markets only.','2026-08-16 sealed.','final75-99 untouched.','No threshold/PnL sweep.','8784/8786/Echtgeld untouched.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
