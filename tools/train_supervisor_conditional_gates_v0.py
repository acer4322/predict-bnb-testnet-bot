from __future__ import annotations
import bisect, json
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, f1_score, log_loss
from train_supervisor_mode_v0 import OUT, SRC, CURRENT, build, numeric, sample_weights, SEED

TAKER_EVENTS=SRC/'taker_event_states_v1.csv'
REPORT=OUT/'supervisor_conditional_gates_v0_report.json'
ART=OUT/'supervisor_conditional_gates_v0.joblib'


def fit_bin(train, features, label):
    m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=70,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=1.0,early_stopping=True,validation_fraction=.1,n_iter_no_change=15,random_state=SEED)
    y=train[label].astype(str)
    m.fit(numeric(train,features),y,sample_weight=sample_weights(y))
    return m

def metric(m,x,features,label,positive):
    y=x[label].astype(str); z=numeric(x,features); cls=list(m.classes_); pi=cls.index(positive); p=m.predict_proba(z)[:,pi]; pred=m.predict(z); yy=y.eq(positive).astype(int).to_numpy(); pp=pd.Series(pred,index=y.index).eq(positive).astype(int).to_numpy()
    return {'n':len(y),'positive':positive,'rate':float(yy.mean()),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'balancedAccuracy':float(balanced_accuracy_score(yy,pp)),'f1':float(f1_score(yy,pp,zero_division=0)),'logLoss':float(log_loss(yy,np.column_stack([1-p,p]),labels=[0,1]))}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,mem=build(); features={'currentOnly':CURRENT,'currentPlusMemory':CURRENT+mem}
    up=pd.to_numeric(d.label_up_next1s,errors='coerce').fillna(0).astype(int).gt(0); dn=pd.to_numeric(d.label_down_next1s,errors='coerce').fillna(0).astype(int).gt(0); net=pd.to_numeric(d.maker_net,errors='coerce').fillna(0.0)
    rep=((net>1e-9)&dn)|((net<-1e-9)&up)
    d['maker_gate_label']=np.where(rep,'PASSIVE_REPAIR','NORMAL_MAKER')

    te=pd.read_csv(TAKER_EVENTS,usecols=['market_id','checkpoint_ms','label_effect']).sort_values(['market_id','checkpoint_ms'])
    by={}
    for mid,x in te.groupby('market_id',sort=False):
        xx=x[pd.to_numeric(x.checkpoint_ms,errors='coerce').notna()].copy(); by[int(mid)]=(pd.to_numeric(xx.checkpoint_ms,errors='coerce').astype('int64').tolist(),xx.label_effect.astype(str).tolist())
    eff=[]
    for r in d[['market_id','checkpoint_ms']].itertuples(index=False):
        b=by.get(int(r.market_id)); val=None
        if b:
            ts,es=b; j=bisect.bisect_right(ts,int(r.checkpoint_ms))
            if j<len(ts) and 0<ts[j]-int(r.checkpoint_ms)<=3000: val=es[j]
        eff.append(val)
    d['taker_effect_next3s']=eff
    d['taker_gate_label']=np.where(d.taker_effect_next3s.eq('REPAIR_EFFECT'),'ACTIVE_REPAIR','ACTIVE_OTHER')

    markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=markets.market_id.astype(int).tolist(); a=int(len(ids)*.70); b=int(len(ids)*.85); splits={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}
    parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}
    results={}; models={}
    for variant,feats in features.items():
        trm=parts['train'][parts['train'].option_mode.eq('MAKER')].copy(); trt=parts['train'][parts['train'].option_mode.eq('TAKER')].copy()
        mm=fit_bin(trm,feats,'maker_gate_label'); tm=fit_bin(trt,feats,'taker_gate_label'); models[variant]={'maker':mm,'taker':tm,'features':feats}
        results[variant]={}
        for s in ('validation','test'):
            xm=parts[s][parts[s].option_mode.eq('MAKER')].copy(); xt=parts[s][parts[s].option_mode.eq('TAKER')].copy()
            results[variant][s]={'makerRepair':metric(mm,xm,feats,'maker_gate_label','PASSIVE_REPAIR'),'takerRepair':metric(tm,xt,feats,'taker_gate_label','ACTIVE_REPAIR'),'takerSubtypeCounts':xt.taker_effect_next3s.value_counts(dropna=False).to_dict()}
    artifact={'version':'SUPERVISOR_CONDITIONAL_GATES_V0','researchOnly':True,'runtimePromotion':False,'models':models['currentPlusMemory'],'guards':['No winner/PnL.','No special 2026-08-16 data.','Future Target action is label only, never feature.']}; joblib.dump(artifact,ART)
    lift={s:{'makerRepairAuc':results['currentPlusMemory'][s]['makerRepair']['auc']-results['currentOnly'][s]['makerRepair']['auc'],'takerRepairAuc':results['currentPlusMemory'][s]['takerRepair']['auc']-results['currentOnly'][s]['takerRepair']['auc']} for s in ('validation','test')}
    rep={'reportVersion':'SUPERVISOR_CONDITIONAL_GATES_V0','researchOnly':True,'sourceRows':len(d),'sourceMarkets':int(d.market_id.nunique()),'splitMarkets':{k:len(v) for k,v in splits.items()},'labels':{'maker':'PASSIVE_REPAIR iff next1s Maker placement includes minority side relative to strict-past Maker net; else NORMAL_MAKER','taker':'ACTIVE_REPAIR iff earliest next3s Taker event has REPAIR_EFFECT; ADD_EFFECT/BUILD_FROM_FLAT collapsed to ACTIVE_OTHER'},'results':results,'memoryLift':lift,'artifact':str(ART),'decisionRule':'Shadow research only. If stable, compose with SUPERVISOR_MODE_V0; do not replace R2 repair/taker triggers yet.','guards':['No winner/PnL.','Ordinary Level-1 only.','No PnL threshold sweep.','8784/8786 untouched.','No Echtgeld changes.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
