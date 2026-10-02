from __future__ import annotations
import argparse,bisect,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,balanced_accuracy_score,roc_auc_score

TICK=0.01
EPS=1e-9

def fit_eval(X,y,mid,cols,seed):
    ums=sorted(set(map(int,mid.tolist())));n=len(ums);a=max(1,int(.70*n));b=max(a+1,int(.85*n))
    tr=set(ums[:a]);ca=set(ums[a:b]);te=set(ums[b:])
    it=np.where(np.isin(mid,list(tr)))[0];ic=np.where(np.isin(mid,list(ca)))[0];ie=np.where(np.isin(mid,list(te)))[0]
    m=HistGradientBoostingClassifier(max_iter=240,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=15,l2_regularization=3.,class_weight='balanced',random_state=seed).fit(X[it][:,cols],y[it])
    pc=m.predict_proba(X[ic][:,cols])[:,1];pt=m.predict_proba(X[ie][:,cols])[:,1]
    def auc(yy,p): return float(roc_auc_score(yy,p)) if len(set(map(int,yy.tolist())))>1 else None
    ths=np.unique(np.quantile(pc,np.linspace(.05,.95,91)));best=(.5,-1.)
    for th in ths:
        ba=balanced_accuracy_score(y[ic],pc>=th)
        if ba>best[1]:best=(float(th),float(ba))
    th=best[0]
    def pack(yy,p):
        return {'n':int(len(yy)),'positiveRate':float(np.mean(yy)) if len(yy) else None,'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)) if len(yy) and np.sum(yy)>0 else None,'threshold':th,'predPositiveRate':float(np.mean(p>=th)) if len(yy) else None,'balancedAccuracy':float(balanced_accuracy_score(yy,p>=th)) if len(set(map(int,yy.tolist())))>1 else None}
    return m,{'calibration':pack(y[ic],pc),'test':pack(y[ie],pt),'marketSplit':{'train':len(tr),'calibration':len(ca),'test':len(te)}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--db',required=True);ap.add_argument('--max-markets',type=int,default=100);ap.add_argument('--output',required=True);a=ap.parse_args()
    z=np.load(a.dataset);meta=json.load(open(a.meta,encoding='utf-8'));X=z['X'];yh=z['y_hazard'];ys=z['y_side_up'];yo=z['y_offset_ticks'];yq=z['y_log_qty'];mid=z['market_id'];ts=z['timestamp_ms'];F=list(meta['features']);ix={k:i for i,k in enumerate(F)}
    ums=sorted(set(map(int,mid.tolist())));allowed=set(ums[:a.max_markets]) if a.max_markets>0 else set(ums)
    idx_by=defaultdict(list);ts_by=defaultdict(list)
    for i,m0 in enumerate(mid):
        m=int(m0)
        if m in allowed:idx_by[m].append(i);ts_by[m].append(int(ts[i]))
    c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;ev_by=defaultdict(list);mids=sorted(allowed)
    if mids:
        ph=','.join('?'*len(mids));q=f"select market_id,side,first_event_ms,average_price from target_parent_orders where asset='ETH' and role='MAKER' and first_event_ms is not null and market_id in ({ph}) order by market_id,first_event_ms,parent_id"
        for r in c.execute(q,mids):ev_by[int(r['market_id'])].append((int(r['first_event_ms']),str(r['side']),float(r['average_price'] or 0.)))
    c.close()
    public_static=['seconds_left','up_bid','up_ask','down_bid','down_ask','up_spread','down_spread','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','book_order_count','book_depth_imbalance']
    dynamic=['update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_d250','up_ask_d250','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_d1','up_ask_d1','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1']
    engineered=['first_leg_side_up','second_leg_side_up','first_leg_price','candidate_offset_ticks','candidate_qty','candidate_price','economic_ceiling','pair_sum','ceiling_slack','candidate_at_best','candidate_improves','candidate_deeper','queue_at_best_proxy','first_fill_to_attempt_state_ms']
    rows=[];lab5=[];lab10=[];rmid=[];dedup=set();attempt_lags=[];offsets=[]
    for m in mids:
        arr=ts_by[m];inds=idx_by[m];events=ev_by.get(m,[])
        if not arr or not events:continue
        for ft,fs,fp in events:
            pos=bisect.bisect_left(arr,ft);opp='DOWN' if fs=='UP' else 'UP';k=None
            q=pos
            while q<len(arr) and arr[q]-ft<=10000:
                i=inds[q]
                if int(yh[i])==1 and np.isfinite(ys[i]) and (('UP' if float(ys[i])>=.5 else 'DOWN')==opp):k=i;break
                q+=1
            if k is None:continue
            key=(m,int(k),opp)
            if key in dedup:continue
            dedup.add(key);kt=int(ts[k]);row=X[k]
            off=float(yo[k]) if np.isfinite(yo[k]) else math.nan;qty=float(np.expm1(yq[k])) if np.isfinite(yq[k]) else math.nan
            if opp=='UP':
                bid=float(row[ix['up_bid']]);best_depth=float(row[ix['up_bid_depth']])
            else:
                bid=float(row[ix['down_bid']]);best_depth=float(row[ix['up_ask_depth']])
            cand=bid+off*TICK if np.isfinite(off) else math.nan;ceiling=max(.01,min(.99,1.0-fp-.01));pair=fp+cand if np.isfinite(cand) else math.nan;slack=ceiling-cand if np.isfinite(cand) else math.nan
            atbest=1.0 if np.isfinite(off) and abs(off)<=1e-9 else 0.0;improve=1.0 if np.isfinite(off) and off>0 else 0.0;deeper=1.0 if np.isfinite(off) and off<0 else 0.0;queue=best_depth if atbest else (0.0 if improve else math.nan)
            vals=[float(row[ix[n]]) for n in public_static]+[1. if fs=='UP' else 0.,1. if opp=='UP' else 0.,fp,off,qty,cand,ceiling,pair,slack,atbest,improve,deeper,queue,float(kt-ft)]+[float(row[ix[n]]) for n in dynamic]
            f5=f10=False
            for tt,ss,pp in events:
                if tt<=kt:continue
                if tt-kt>10500:break
                if ss!=opp:continue
                if tt-kt<=5500:f5=True
                f10=True;break
            rows.append(vals);lab5.append(int(f5));lab10.append(int(f10));rmid.append(m);attempt_lags.append(kt-ft);offsets.append(off)
    X2=np.asarray(rows,np.float32);M=np.asarray(rmid,np.int32);names=public_static+engineered+dynamic
    if len(X2)==0:raise RuntimeError('no attempt-conditioned episodes')
    idx_market=list(range(len(public_static)));idx_action=list(range(len(public_static)+len(engineered)));idx_dynamic=list(range(len(names)))
    tasks={};models={}
    for label_name,label in [('fill5p5s',np.asarray(lab5,np.int8)),('fill10p5s',np.asarray(lab10,np.int8))]:
        mm,mres=fit_eval(X2,label,M,idx_market,31);ma,ares=fit_eval(X2,label,M,idx_action,32);md,dres=fit_eval(X2,label,M,idx_dynamic,33)
        tasks[label_name]={'marketOnly':mres,'actionGeometry':ares,'actionDynamic':dres,'dynamicLiftVsMarket':(dres['test']['auc']-mres['test']['auc']) if dres['test']['auc'] is not None and mres['test']['auc'] is not None else None,'dynamicLiftVsAction':(dres['test']['auc']-ares['test']['auc']) if dres['test']['auc'] is not None and ares['test']['auc'] is not None else None};models[label_name]=md
    test=tasks['fill5p5s']['actionDynamic']['test'];lift=tasks['fill5p5s']['dynamicLiftVsMarket'];checks={'testSupport':test['n']>=50,'positiveRateHealthy':test['positiveRate'] is not None and .15<=test['positiveRate']<=.85,'dynamicAucGe062':test['auc'] is not None and test['auc']>=.62,'dynamicLiftVsMarketGe003':lift is not None and lift>=.03,'thresholdNondegenerate':.02<test['threshold']<.98}
    out={'version':'ETH_TARGET_SECOND_LEG_ATTEMPT_CONDITIONED_FILLABILITY_V3','researchOnly':True,'rows':int(len(X2)),'markets':int(len(set(map(int,M.tolist())))),'maxMarkets':a.max_markets,'attemptLagMs':{'mean':float(np.mean(attempt_lags)),'median':float(np.median(attempt_lags)),'max':int(max(attempt_lags))},'offsetTicks':{'median':float(np.nanmedian(offsets)),'q25':float(np.nanquantile(offsets,.25)),'q75':float(np.nanquantile(offsets,.75))},'tasks':tasks,'checks':checks,'representationPass':all(checks.values()),'featureSets':{'marketOnly':public_static,'actionGeometry':public_static+engineered,'actionDynamic':names},'boundary':['ETH-only gradients','consumed markets only','conditioned on observed opposite placement attempt after first-leg actual fill','candidate action geometry is known-at-submit input, not future market information','winner/PnL/future market price absent','V20 deterministic responsibility unaffected','no BTC numeric transfer']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'model':models['fill5p5s'],'features':names,'threshold':tasks['fill5p5s']['actionDynamic']['calibration']['threshold'],'representationPass':out['representationPass']},op.with_suffix('.joblib'))
    print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':out['rows'],'markets':out['markets'],'attemptLagMs':out['attemptLagMs'],'fill5p5s':tasks['fill5p5s'],'fill10p5s':tasks['fill10p5s'],'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
