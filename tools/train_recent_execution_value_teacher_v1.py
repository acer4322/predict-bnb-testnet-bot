from __future__ import annotations
import json, math, warnings
from pathlib import Path
import joblib, numpy as np, pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier, ExplainableBoostingRegressor
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, mean_absolute_error, mean_squared_error

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ART=D/'r2_recent_execution_value_teacher_v1.joblib'
REP=D/'r2_recent_execution_value_teacher_v1_report.json'
SEED=20260822
FEATURES=['side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial','cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count','quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion','public_depletion_ratio','public_any_depletion','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']

def load():
    states=[]; marks=[]
    for p in sorted(D.glob('recent_execution_horizon_b*.json')):
        d=json.loads(p.read_text(encoding='utf-8')); states.extend(d.get('stateRows',[])); marks.extend(d.get('markoutRows',[]))
    s=pd.DataFrame(states); m=pd.DataFrame(marks)
    by=s.groupby('market_id')['checkpoint_ms'].max().sort_values(); mids=by.index.astype(int).tolist(); a=int(len(mids)*.70); b=int(len(mids)*.85)
    splits={'train':mids[:a],'validation':mids[a:b],'test':mids[b:]}
    return s,m,splits

def fill_metrics(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float)
    return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}

def reg_metrics(y,p):
    y=np.asarray(y,float); p=np.asarray(p,float)
    return {'n':len(y),'mae':float(mean_absolute_error(y,p)),'rmse':float(mean_squared_error(y,p)**.5),'meanTarget':float(y.mean()),'meanPred':float(p.mean()),'signAccuracy':float(np.mean(np.sign(y)==np.sign(p))),'negativeRateTarget':float(np.mean(y<0)),'negativeRatePred':float(np.mean(p<0))}

def main():
    warnings.filterwarnings('ignore'); s,m,splits=load(); models={}; metrics={}; tops={}
    X=s[FEATURES].apply(pd.to_numeric,errors='coerce'); tr=s['market_id'].astype(int).isin(splits['train'])
    for hi,h in enumerate((1,3,5)):
        y=s[f'label_fill{h}s'].astype(int)
        fill=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=60,min_samples_leaf=18,n_jobs=-2,random_state=SEED+hi)
        fill.fit(X.loc[tr],y.loc[tr]); models[f'fill{h}s']=fill; metrics[f'fill{h}s']={}
        for part,mids in splits.items():
            z=s['market_id'].astype(int).isin(mids); metrics[f'fill{h}s'][part]=fill_metrics(y.loc[z],fill.predict_proba(X.loc[z])[:,1])
    mm=m[m['label_markout1s_ticks'].notna()].copy(); Xm=mm[FEATURES].apply(pd.to_numeric,errors='coerce'); ym=mm['label_markout1s_ticks'].astype(float); trm=mm['market_id'].astype(int).isin(splits['train'])
    mark=ExplainableBoostingRegressor(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=60,min_samples_leaf=10,n_jobs=-2,random_state=SEED+1)
    mark.fit(Xm.loc[trm],ym.loc[trm]); models['markout1s']=mark; metrics['markout1s']={}
    for part,mids in splits.items():
        z=mm['market_id'].astype(int).isin(mids); metrics['markout1s'][part]=reg_metrics(ym.loc[z],mark.predict(Xm.loc[z]))
    for name,model in models.items():
        imp=list(model.term_importances()); names=list(model.term_names_); tops[name]=sorted([{'term':str(names[i]),'importance':float(imp[i])} for i in range(len(imp))],key=lambda x:x['importance'],reverse=True)[:12]
    art={'version':'R2_RECENT_EXECUTION_VALUE_TEACHER_V1_MULTI_HORIZON','features':FEATURES,'models':models,'splits':splits,'trainingMarkets':splits['train'],'runtimeTargetDataAllowed':False,'dreamFillAllowed':False,'winnerRuntimeInput':False}
    joblib.dump(art,ART)
    rep={'version':art['version'],'researchOnly':True,'states':len(s),'markouts':len(mm),'markets':len(set(s.market_id.astype(int))),'splitMarkets':{k:len(v) for k,v in splits.items()},'splits':splits,'metrics':metrics,'topTerms':tops,'artifact':str(ART),'guardrails':['Frozen R2 intent tape only','Actual HftBacktest fills only','Strict-past runtime state','Future fill/markout used only as offline labels','No winner/Target/settlement/PnL runtime feature','Chronological market split 70/15/15','No threshold sweep']}
    REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(REP),'metrics':metrics,'splits':{k:v for k,v in splits.items()}},ensure_ascii=False))
if __name__=='__main__': main()
