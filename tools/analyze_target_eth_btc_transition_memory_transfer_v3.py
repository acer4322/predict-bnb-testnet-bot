import argparse,json,os,sqlite3,math
from collections import defaultdict

def clamp(x,a,b): return max(a,min(b,x))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score,balanced_accuracy_score,log_loss
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    mend={(r['asset'],r['market_id']):r['window_end_ms'] for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH')")}
    rows=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"))
    rec=[];cur=None
    up=dn=cost=0.;mn=tn=0;weakN=domN=0
    prev=None
    for r in rows:
        k=(r['asset'],r['market_id'])
        if k!=cur:
            cur=k;up=dn=cost=0.;mn=tn=0;weakN=domN=0;prev=None
        total=up+dn;paired=min(up,dn);gap=abs(up-dn);paircov=2*paired/total if total else 1.;absnet=gap/total if total else 0.;floor=(paired-cost);floor_ratio=floor/max(cost,1.)
        end=mend.get(k);sl=(end-r['first_event_ms'])/1000. if end else None
        if total<=1e-9 or gap<=1e-9: rel='BAL';weakSide=None;domSide=None
        else:
            weakSide='UP' if up<dn else 'DOWN';domSide='DOWN' if weakSide=='UP' else 'UP';rel='WEAK' if r['side']==weakSide else 'DOM'
        avgup=cost*0
        # reconstruct side average costs from state carried in prev snapshots below
        upCost=prev['postUpCost'] if prev else 0.0;dnCost=prev['postDnCost'] if prev else 0.0
        avgUp=upCost/up if up>1e-9 else 0.;avgDn=dnCost/dn if dn>1e-9 else 0.
        weakAvg=(avgUp if weakSide=='UP' else avgDn if weakSide=='DOWN' else 0.)
        domAvg=(avgDn if weakSide=='UP' else avgUp if weakSide=='DOWN' else 0.)
        base=[clamp(sl if sl is not None else 0.,-30,330)/300.,paircov,absnet,clamp(floor_ratio,-5,5),tn/max(mn+tn,1)]
        mem=[0.]*12
        if prev is not None:
            age=max(0.,r['first_event_ms']-prev['t'])
            mem=[
                1. if prev['role']=='TAKER' else 0.,
                1. if prev['rel']=='WEAK' else -1. if prev['rel']=='DOM' else 0.,
                1. if prev['side']=='UP' else -1.,
                clamp(age/10000.,0,10),
                clamp(prev['deltaAbsNetRatio'],-1,1),
                clamp(prev['deltaPairCov'],-1,1),
                clamp(prev['deltaFloorRatio'],-2,2),
                1. if prev['deltaAbsNetRatio']< -1e-6 else 0.,
                1. if prev['deltaAbsNetRatio']> 1e-6 else 0.,
                weakN/max(weakN+domN,1),
                weakAvg-domAvg,
                1. if weakSide=='UP' else -1. if weakSide=='DOWN' else 0.,
            ]
        if rel in ('WEAK','DOM'):
            rec.append({'asset':r['asset'],'market':r['market_id'],'end':end,'y':1 if rel=='WEAK' else 0,'base':base,'mem':mem})
        # apply current event and compute transition outcome for next decision
        preTotal=total;preGap=gap;prePairCov=paircov;preFloor=floor
        sh=float(r['shares']);px=float(r['average_price'])
        # side costs must persist independently
        if prev is None: preUpCost=0.;preDnCost=0.
        else: preUpCost=prev['postUpCost'];preDnCost=prev['postDnCost']
        if r['side']=='UP': up+=sh;postUpCost=preUpCost+sh*px;postDnCost=preDnCost
        else: dn+=sh;postDnCost=preDnCost+sh*px;postUpCost=preUpCost
        cost=postUpCost+postDnCost
        postTotal=up+dn;postGap=abs(up-dn);postPairCov=2*min(up,dn)/postTotal if postTotal else 1.;postFloor=min(up,dn)-cost
        prev={
            't':int(r['first_event_ms']),'role':r['role'],'side':r['side'],'rel':rel,
            'deltaAbsNetRatio':(postGap/max(postTotal,1e-9))-(preGap/max(preTotal,1e-9) if preTotal else 0.),
            'deltaPairCov':postPairCov-prePairCov,
            'deltaFloorRatio':(postFloor-preFloor)/max(abs(cost),1.),
            'postUpCost':postUpCost,'postDnCost':postDnCost,
        }
        if r['role']=='MAKER':mn+=1
        else:tn+=1
        if rel=='WEAK':weakN+=1
        elif rel=='DOM':domN+=1
    btc_ends={v for (aa,_),v in mend.items() if aa=='BTC' and v is not None};eth_ends={v for (aa,_),v in mend.items() if aa=='ETH' and v is not None};common=sorted(btc_ends&eth_ends);cutoff=common[int(len(common)*.70)]
    def dataset(asset,period,kind):
        z=[r for r in rec if r['asset']==asset and r['end'] is not None and ((r['end']<cutoff) if period=='early' else (r['end']>=cutoff))]
        X=np.asarray([r['base']+(r['mem'] if kind=='memory' else []) for r in z],float);y=np.asarray([r['y'] for r in z],int)
        if len(X)>180000:
            idx=np.linspace(0,len(X)-1,180000).astype(int);X=X[idx];y=y[idx]
        return X,y
    def one(tr,te,kind):
        Xtr,ytr=dataset(tr,'early',kind);Xte,yte=dataset(te,'late',kind)
        m=HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=19,learning_rate=.06,l2_regularization=2.,random_state=13).fit(Xtr,ytr)
        p=m.predict_proba(Xte)[:,1];pred=(p>=.5).astype(int)
        return {'trainAsset':tr,'testAsset':te,'trainN':len(ytr),'testN':len(yte),'auc':float(roc_auc_score(yte,p)),'balancedAccuracy':float(balanced_accuracy_score(yte,pred)),'logLoss':float(log_loss(yte,p,labels=[0,1])),'positiveRate':float(yte.mean())}
    out={'version':'TARGET_ETH_BTC_TRANSITION_MEMORY_TRANSFER_V3','sourceDb':os.path.abspath(a.db),'commonWindows':len(common),'cutoffWindowEndMs':cutoff,'strictPast':True,'usesActualParentFillsOnly':True,'placement18PriorUsed':False,'featureSets':{'baseline':['seconds_left_norm','paircov','absnet','floor_ratio','prior_taker_frac'],'memory':['baseline + prev_role','prev_relation','prev_side','age','prev_delta_absnet','prev_delta_paircov','prev_delta_floor','prev_repair_effect','prev_expansion_effect','prior_weak_frac','weak_minus_dom_avg_cost','current_weak_orientation']},'results':{}}
    for kind in ('baseline','memory'):
        rr={}
        for tr,te in [('BTC','BTC'),('ETH','ETH'),('BTC','ETH'),('ETH','BTC')]:rr[f'{tr}_to_{te}']=one(tr,te,kind)
        out['results'][kind]=rr
    out['deltaAuc']={k:out['results']['memory'][k]['auc']-out['results']['baseline'][k]['auc'] for k in out['results']['baseline']}
    out['keepSignal']=bool(out['deltaAuc']['BTC_to_ETH']>=0.03 and out['deltaAuc']['ETH_to_BTC']>=0.03)
    os.makedirs(os.path.dirname(a.output),exist_ok=True);json.dump(out,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2);print(json.dumps({'ok':True,'deltaAuc':out['deltaAuc'],'baseline':{k:v['auc'] for k,v in out['results']['baseline'].items()},'memory':{k:v['auc'] for k,v in out['results']['memory'].items()},'keepSignal':out['keepSignal']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
