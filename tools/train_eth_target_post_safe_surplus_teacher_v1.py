from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def met(y,p,th=.5):
    y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int);both=len(np.unique(y))==2
    return {'n':int(len(y)),'positiveRate':float(np.mean(y)) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'threshold':float(th),'predPositiveRate':float(np.mean(z)) if len(z) else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None}
def best_th(y,p):
    qs=np.linspace(.05,.95,37);cand=np.unique(np.quantile(p,qs));best=(.5,-1.)
    for th in cand:
        z=(p>=th).astype(int);b=balanced_accuracy_score(y,z)
        if b>best[1]:best=(float(th),float(b))
    return best[0]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
    z=np.load(a.dataset);X=z['X'];yv=z['y_value'];ys=z['y_side'];vm=z['value_mask'].astype(bool);sm=z['side_mask'].astype(bool);mid=z['market_id'];end=z['end_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={k:i for i,k in enumerate(F)}
    markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(np.min(end[mid==m])));n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};ms={k:np.isin(mid,list(v)) for k,v in sets.items()}
    bal_names=[x for x in ['seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_relation','candidate_qty_log'] if x in ix];bal=[ix[x] for x in bal_names];econ=list(range(len(F)))
    side_exclude={'candidate_relation','candidate_qty_log','candidate_side_up','candidate_price','candidate_notional_ratio','match_fraction','opp_unmatched_ratio','matched_opposite_avg_price','candidate_pair_sum','pair_edge','projected_floor_delta_ratio','projected_best_delta_ratio','post_pair_coverage','post_absnet_ratio'};side_names=[f for f in F if f not in side_exclude];side_cols=[ix[f] for f in side_names]
    out={'version':'ETH_TARGET_POST_SAFE_SURPLUS_TEACHER_V1','researchOnly':True,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'tasks':{},'featureSets':{'valueBalance':bal_names,'valueEconomic':F,'sidePreCandidate':side_names}};models={}
    # surplus value
    it=np.where(ms['train']&vm)[0];ic=np.where(ms['calibration']&vm)[0];ie=np.where(ms['test']&vm)[0]
    for name,cols in [('balanceOnly',bal),('economicFull',econ)]:
        m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=3.,class_weight='balanced',random_state=20260901).fit(X[it][:,cols],yv[it].astype(int));pc=m.predict_proba(X[ic][:,cols])[:,1];th=best_th(yv[ic].astype(int),pc);pt=m.predict_proba(X[ie][:,cols])[:,1];out['tasks'].setdefault('surplus_value_30s',{})[name]={'calibration':met(yv[ic].astype(int),pc,th),'test':met(yv[ie].astype(int),pt,th)};models[f'surplus_value_30s:{name}']={'model':m,'cols':cols,'threshold':th}
    sv=out['tasks']['surplus_value_30s'];sv['aucLiftEconomic']=float(sv['economicFull']['test']['auc']-sv['balanceOnly']['test']['auc']) if sv['economicFull']['test']['auc'] is not None and sv['balanceOnly']['test']['auc'] is not None else None
    # balanced safe-base side preference
    it=np.where(ms['train']&sm)[0];ic=np.where(ms['calibration']&sm)[0];ie=np.where(ms['test']&sm)[0]
    if len(it)>=100 and len(ic)>=20 and len(ie)>=20 and len(np.unique(ys[it].astype(int)))==2 and len(np.unique(ys[ie].astype(int)))==2:
        m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=30,l2_regularization=3.,class_weight='balanced',random_state=20260902).fit(X[it][:,side_cols],ys[it].astype(int));pc=m.predict_proba(X[ic][:,side_cols])[:,1];th=best_th(ys[ic].astype(int),pc);pt=m.predict_proba(X[ie][:,side_cols])[:,1];out['tasks']['balanced_surplus_side']={'calibration':met(ys[ic].astype(int),pc,th),'test':met(ys[ie].astype(int),pt,th)};models['balanced_surplus_side']={'model':m,'cols':side_cols,'threshold':th}
    else:out['tasks']['balanced_surplus_side']={'status':'INSUFFICIENT_SUPPORT','trainN':int(len(it)),'calibrationN':int(len(ic)),'testN':int(len(ie))}
    side=out['tasks']['balanced_surplus_side'];value=out['tasks']['surplus_value_30s'];checks={'sideSupport':bool(side.get('test',{}).get('n',0)>=50),'sideAucGe058':bool(side.get('test',{}).get('auc') is not None and side['test']['auc']>=.58),'valueSupport':bool(value['economicFull']['test']['n']>=100),'valueAucGe068':bool(value['economicFull']['test']['auc'] is not None and value['economicFull']['test']['auc']>=.68),'valueEconomicLiftGe002':bool((value.get('aucLiftEconomic') or -9)>=.02),'valueThresholdNondegenerate':bool(.02<value['economicFull']['test']['threshold']<.98),'frontierRespected':bool(int(meta.get('cutoff',0))<=1823545)};out['passChecks']=checks;out['representationPass']=all(checks.values());out['boundary']=['marketId<=1823545','Target actual Maker fills only','side teacher uses pre-candidate state only','value teacher may use candidate side/price economics','winner/PnL absent','no runtime authority yet']
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'featureSets':out['featureSets'],'models':models,'representationPass':out['representationPass']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'tasks':out['tasks'],'checks':checks,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
