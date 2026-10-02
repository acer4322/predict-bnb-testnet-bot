from __future__ import annotations
import argparse,json,sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def fit_eval(X,y,mid,cols,seed=1):
    ums=sorted(set(map(int,mid.tolist())));n=len(ums);a=max(1,int(.7*n));b=max(a+1,int(.85*n));tr=set(ums[:a]);ca=set(ums[a:b]);te=set(ums[b:]);it=np.where(np.isin(mid,list(tr)))[0];ic=np.where(np.isin(mid,list(ca)))[0];ie=np.where(np.isin(mid,list(te)))[0]
    m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=15,l2_regularization=3.,class_weight='balanced',random_state=seed).fit(X[it][:,cols],y[it])
    pc=m.predict_proba(X[ic][:,cols])[:,1];pt=m.predict_proba(X[ie][:,cols])[:,1]
    def auc(yy,p):return float(roc_auc_score(yy,p)) if len(set(map(int,yy.tolist())))>1 else None
    # calibration threshold maximizes balanced accuracy; no HFT/PnL use
    ths=np.unique(np.quantile(pc,np.linspace(0.05,.95,91)));best=(.5,-1.)
    for th in ths:
        ba=balanced_accuracy_score(y[ic],pc>=th)
        if ba>best[1]:best=(float(th),float(ba))
    th=best[0]
    def pack(yy,p):
        return {'n':int(len(yy)),'positiveRate':float(np.mean(yy)) if len(yy) else None,'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)) if len(yy) and np.sum(yy)>0 else None,'threshold':th,'predPositiveRate':float(np.mean(p>=th)) if len(yy) else None,'balancedAccuracy':float(balanced_accuracy_score(yy,p>=th)) if len(set(map(int,yy.tolist())))>1 else None}
    return m,{'calibration':pack(y[ic],pc),'test':pack(y[ie],pt),'marketSplit':{'train':len(tr),'calibration':len(ca),'test':len(te)}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--db',required=True);ap.add_argument('--max-markets',type=int,default=100);ap.add_argument('--output');a=ap.parse_args()
    z=np.load(a.dataset);meta=json.load(open(a.meta,encoding='utf-8'));X=z['X'];yh=z['y_hazard'];ys=z['y_side_up'];mid=z['market_id'];ts=z['timestamp_ms'];F=list(meta['features']);ix={k:i for i,k in enumerate(F)}
    ums=sorted(set(map(int,mid.tolist())));allowed=set(ums[:a.max_markets]) if a.max_markets>0 else set(ums)
    c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;by=defaultdict(list)
    mids=sorted(allowed)
    if mids:
        ph=','.join('?'*len(mids));q=f"select market_id,side,first_event_ms,average_price from target_parent_orders where asset='ETH' and role='MAKER' and first_event_ms is not null and market_id in ({ph}) order by market_id,first_event_ms,parent_id"
        for r in c.execute(q,mids):by[int(r['market_id'])].append((int(r['first_event_ms']),str(r['side']),float(r['average_price'] or 0.)))
    c.close()
    rows=[];labels=[];rmid=[]
    for i in range(len(X)):
        m=int(mid[i])
        if m not in allowed or int(yh[i])!=1 or not np.isfinite(ys[i]):continue
        side='UP' if float(ys[i])>=.5 else 'DOWN';t=int(ts[i]);ev=by.get(m,[]);first=None
        for tt,ss,pp in ev:
            if tt<=t:continue
            if tt-t>5000:break
            if ss==side:first=(tt,pp);break
        if first is None:continue
        opp='DOWN' if side=='UP' else 'UP';dt=None
        for tt,ss,pp in ev:
            if tt<=first[0]:continue
            if tt-first[0]>10000:break
            if ss==opp:dt=tt-first[0];break
        side_up=1.0 if side=='UP' else 0.0
        rows.append(np.concatenate([X[i],[side_up]]).astype(np.float32));labels.append(1 if dt is not None else 0);rmid.append(m)
    X2=np.asarray(rows,np.float32);y=np.asarray(labels,np.int8);M=np.asarray(rmid,np.int32);names=F+['candidate_side_up']
    static_names=['seconds_left','up_bid','up_ask','down_bid','down_ask','up_spread','down_spread','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','book_order_count','book_depth_imbalance','last_placement_age_ms','placement_events_2s','placement_events_10s','candidate_side_up']
    dyn_extra=['update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_d250','up_ask_d250','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_d1','up_ask_d1','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1']
    sidx=[names.index(k) for k in static_names];dnames=static_names+dyn_extra;didx=[names.index(k) for k in dnames]
    sm,sres=fit_eval(X2,y,M,sidx,1);dm,dres=fit_eval(X2,y,M,didx,2);lift=None if sm is None else (dres['test']['auc']-sres['test']['auc'] if dres['test']['auc'] is not None and sres['test']['auc'] is not None else None)
    test=dres['test'];checks={'testSupport':test['n']>=80,'positiveRateHealthy':test['positiveRate'] is not None and .15<=test['positiveRate']<=.85,'dynamicAucGe062':test['auc'] is not None and test['auc']>=.62,'dynamicLiftGe002':lift is not None and lift>=.02,'thresholdNondegenerate':.02<test['threshold']<.98};out={'version':'ETH_TARGET_SECOND_LEG_FILLABILITY_V1','researchOnly':True,'rows':int(len(X2)),'markets':int(len(set(map(int,M.tolist())))),'maxMarkets':a.max_markets,'task':{'staticTopbook':sres,'dynamicMicrostructure':dres,'aucLiftDynamic':lift},'checks':checks,'representationPass':all(checks.values()),'featureSets':{'static':static_names,'dynamic':dnames},'boundary':['ETH-only gradients','receipt-clock strict-past public microstructure','first leg actual fill within5s is training conditioning','label opposite Maker actual fill within10s after first fill','winner/PnL/future price absent','no BTC numeric transfer']}
    if a.output:
        import joblib,os
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);joblib.dump({'version':out['version'],'model':dm,'features':dnames,'threshold':dres['calibration']['threshold'],'representationPass':out['representationPass']},op.with_suffix('.joblib'));op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':out['rows'],'markets':out['markets'],'task':out['task'],'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
