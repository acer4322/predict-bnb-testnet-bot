import argparse,json,os,sqlite3

def clamp(x,a,b): return max(a,min(b,x))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    mend={(r['asset'],r['market_id']):r['window_end_ms'] for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH')")}
    rows=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"))
    rec=[];cur=None;up=dn=0.;upCost=dnCost=0.;mn=tn=weakN=domN=0;prev=None
    for r in rows:
        k=(r['asset'],r['market_id'])
        if k!=cur:
            cur=k;up=dn=upCost=dnCost=0.;mn=tn=weakN=domN=0;prev=None
        total=up+dn;cost=upCost+dnCost;paired=min(up,dn);gap=abs(up-dn);paircov=2*paired/total if total else 1.;absnet=gap/total if total else 0.;floor=paired-cost;floor_ratio=floor/max(cost,1.)
        end=mend.get(k);sl=(end-r['first_event_ms'])/1000. if end else 0.
        if total<=1e-9 or gap<=1e-9: rel='BAL';weakSide=None;domSide=None
        else:
            weakSide='UP' if up<dn else 'DOWN';domSide='DOWN' if weakSide=='UP' else 'UP';rel='WEAK' if r['side']==weakSide else 'DOM'
        avgUp=upCost/up if up>1e-9 else 0.;avgDn=dnCost/dn if dn>1e-9 else 0.;weakAvg=avgUp if weakSide=='UP' else avgDn if weakSide=='DOWN' else 0.;domAvg=avgDn if weakSide=='UP' else avgUp if weakSide=='DOWN' else 0.
        base=[clamp(sl,-30,330)/300.,paircov,absnet,clamp(floor_ratio,-5,5),tn/max(mn+tn,1)]
        mem={'prev_role':0.,'prev_rel':0.,'prev_side_abs':0.,'age':0.,'d_absnet':0.,'d_paircov':0.,'d_floor':0.,'repair_effect':0.,'expand_effect':0.,'prior_weak_frac':weakN/max(weakN+domN,1),'weak_dom_cost_diff':weakAvg-domAvg,'weak_orientation_abs':1. if weakSide=='UP' else -1. if weakSide=='DOWN' else 0.}
        if prev:
            mem.update({'prev_role':1. if prev['role']=='TAKER' else 0.,'prev_rel':1. if prev['rel']=='WEAK' else -1. if prev['rel']=='DOM' else 0.,'prev_side_abs':1. if prev['side']=='UP' else -1.,'age':clamp((r['first_event_ms']-prev['t'])/10000.,0,10),'d_absnet':clamp(prev['d_absnet'],-1,1),'d_paircov':clamp(prev['d_paircov'],-1,1),'d_floor':clamp(prev['d_floor'],-2,2),'repair_effect':1. if prev['d_absnet']< -1e-6 else 0.,'expand_effect':1. if prev['d_absnet']>1e-6 else 0.})
        if rel in ('WEAK','DOM'):rec.append({'asset':r['asset'],'end':end,'y':1 if rel=='WEAK' else 0,'base':base,'mem':mem})
        preTotal=total;preAbs=gap/preTotal if preTotal else 0.;prePair=paircov;preFloor=floor
        sh=float(r['shares']);px=float(r['average_price'])
        if r['side']=='UP':up+=sh;upCost+=sh*px
        else:dn+=sh;dnCost+=sh*px
        postTotal=up+dn;postAbs=abs(up-dn)/postTotal if postTotal else 0.;postPair=2*min(up,dn)/postTotal if postTotal else 1.;postFloor=min(up,dn)-(upCost+dnCost)
        prev={'t':int(r['first_event_ms']),'role':r['role'],'side':r['side'],'rel':rel,'d_absnet':postAbs-preAbs,'d_paircov':postPair-prePair,'d_floor':(postFloor-preFloor)/max(upCost+dnCost,1.)}
        if r['role']=='MAKER':mn+=1
        else:tn+=1
        if rel=='WEAK':weakN+=1
        elif rel=='DOM':domN+=1
    btc={v for (aa,_),v in mend.items() if aa=='BTC' and v is not None};eth={v for (aa,_),v in mend.items() if aa=='ETH' and v is not None};common=sorted(btc&eth);cutoff=common[int(len(common)*.70)]
    families={
      'BASE':[],
      'ECON_TRANSITION':['age','d_absnet','d_paircov','d_floor','repair_effect','expand_effect'],
      'RELATIVE_MEMORY':['age','d_absnet','d_paircov','d_floor','repair_effect','expand_effect','prev_role','prev_rel','prior_weak_frac'],
      'RELATIVE_ECON_MEMORY':['age','d_absnet','d_paircov','d_floor','repair_effect','expand_effect','prev_role','prev_rel','prior_weak_frac','weak_dom_cost_diff'],
      'FULL_WITH_ABSOLUTE_SIDE':['age','d_absnet','d_paircov','d_floor','repair_effect','expand_effect','prev_role','prev_rel','prior_weak_frac','weak_dom_cost_diff','prev_side_abs','weak_orientation_abs']
    }
    def ds(asset,period,fam):
        z=[r for r in rec if r['asset']==asset and r['end'] is not None and ((r['end']<cutoff) if period=='early' else (r['end']>=cutoff))];keys=families[fam]
        X=np.asarray([r['base']+[r['mem'][k] for k in keys] for r in z],float);y=np.asarray([r['y'] for r in z],int)
        if len(X)>180000:
            idx=np.linspace(0,len(X)-1,180000).astype(int);X=X[idx];y=y[idx]
        return X,y
    def score(tr,te,fam):
        Xtr,ytr=ds(tr,'early',fam);Xte,yte=ds(te,'late',fam);m=HistGradientBoostingClassifier(max_iter=120,max_leaf_nodes=19,learning_rate=.06,l2_regularization=2.,random_state=17).fit(Xtr,ytr);p=m.predict_proba(Xte)[:,1]
        return {'auc':float(roc_auc_score(yte,p)),'trainN':len(ytr),'testN':len(yte),'testPositiveRate':float(yte.mean())}
    results={}
    for fam in families:
        results[fam]={k:score(*pair,fam) for k,pair in {'BTC_to_BTC':('BTC','BTC'),'ETH_to_ETH':('ETH','ETH'),'BTC_to_ETH':('BTC','ETH'),'ETH_to_BTC':('ETH','BTC')}.items()}
    base=results['BASE'];deltas={fam:{k:results[fam][k]['auc']-base[k]['auc'] for k in base} for fam in families if fam!='BASE'}
    keep=[fam for fam,d in deltas.items() if d['BTC_to_ETH']>=.03 and d['ETH_to_BTC']>=.03]
    out={'version':'TARGET_ETH_BTC_TRANSITION_MEMORY_ABLATION_V4','strictPast':True,'actualParentFillsOnly':True,'placement18PriorUsed':False,'commonWindows':len(common),'cutoffWindowEndMs':cutoff,'families':families,'results':results,'deltaVsBase':deltas,'crossAssetKeepFamilies':keep}
    os.makedirs(os.path.dirname(a.output),exist_ok=True);json.dump(out,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2);print(json.dumps({'ok':True,'aucs':{f:{k:round(v['auc'],4) for k,v in rr.items()} for f,rr in results.items()},'crossDeltas':{f:{k:round(v,4) for k,v in d.items() if k in ('BTC_to_ETH','ETH_to_BTC')} for f,d in deltas.items()},'keep':keep},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
