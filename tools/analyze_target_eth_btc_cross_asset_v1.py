import argparse, json, math, os, sqlite3, statistics
from collections import defaultdict, Counter


def corr(xs, ys):
    n=min(len(xs),len(ys))
    if n<3:return None
    xs=xs[:n]; ys=ys[:n]
    mx=sum(xs)/n; my=sum(ys)/n
    vx=sum((x-mx)**2 for x in xs); vy=sum((y-my)**2 for y in ys)
    if vx<=0 or vy<=0:return None
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(vx*vy)


def qcut(v, qs=(0.1,0.25,0.5,0.75,0.9)):
    if not v:return []
    s=sorted(v); n=len(s)
    out=[]
    for q in qs:
        p=(n-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p));
        out.append(s[lo] if lo==hi else s[lo]*(hi-p)+s[hi]*(p-lo))
    return out


def auc(y, score):
    pairs=sorted(zip(score,y), key=lambda z:z[0])
    n1=sum(y); n0=len(y)-n1
    if not n1 or not n0:return None
    rank=1; rsum=0.0; i=0
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and pairs[j][0]==pairs[i][0]: j+=1
        avgr=(rank+(rank+(j-i)-1))/2.0
        rsum += avgr*sum(v for _,v in pairs[i:j])
        rank += j-i; i=j
    return (rsum - n1*(n1+1)/2)/(n1*n0)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',required=True); args=ap.parse_args()
    c=sqlite3.connect(args.db)
    c.row_factory=sqlite3.Row
    markets={}
    for r in c.execute("select market_id,asset,window_end_ms from target_markets where asset in ('BTC','ETH')"):
        markets[(r['asset'],r['market_id'])]=r['window_end_ms']
    rows=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares,fill_legs from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"))
    states=[]; by_asset=defaultdict(list); by_market=defaultdict(list)
    curkey=None; up=down=cost=0.0; prev_role=None; maker_n=taker_n=0
    for r in rows:
        key=(r['asset'],r['market_id'])
        if key!=curkey:
            curkey=key; up=down=cost=0.0; prev_role=None; maker_n=taker_n=0
        sh=float(r['shares']); px=float(r['average_price']); total=up+down
        paired=min(up,down); gap=abs(up-down)
        paircov=(2*paired/total) if total>1e-9 else 1.0
        absnet=(gap/total) if total>1e-9 else 0.0
        floor=paired-cost
        floor_ratio=(floor/max(cost,1.0))
        sec_left=None
        end=markets.get(key)
        if end is not None: sec_left=(end-int(r['first_event_ms']))/1000.0
        if total<=1e-9 or gap<1e-9: rel='BAL'
        elif (r['side']=='UP' and up<down) or (r['side']=='DOWN' and down<up): rel='WEAK'
        else: rel='DOM'
        rec={
          'asset':r['asset'],'market_id':r['market_id'],'role':r['role'],'side':r['side'],'rel':rel,
          'sec_left':sec_left,'paircov':paircov,'absnet':absnet,'floor_ratio':floor_ratio,
          'log_total':math.log1p(total),'log_gap':math.log1p(gap),'prev_role':prev_role or 'NONE',
          'prior_taker_frac':taker_n/max(maker_n+taker_n,1),'shares':sh,'price':px,
          'pre_up':up,'pre_down':down,'pre_cost':cost
        }
        states.append(rec); by_asset[r['asset']].append(rec); by_market[key].append(rec)
        if r['side']=='UP': up+=sh
        else: down+=sh
        cost+=px*sh
        if r['role']=='MAKER':maker_n+=1
        else:taker_n+=1
        prev_role=r['role']

    # descriptive invariant/calibration profiles
    summary={}
    for asset, rr in by_asset.items():
        n=len(rr); maker=sum(x['role']=='MAKER' for x in rr); weak=sum(x['rel']=='WEAK' for x in rr); dom=sum(x['rel']=='DOM' for x in rr)
        summary[asset]={
          'orders':n,'makerFrac':maker/n,'takerFrac':1-maker/n,
          'weakFracAmongImbalanced':weak/max(weak+dom,1),
          'medianSharesByRole':{},'medianSecLeftByRole':{},
        }
        for role in ('MAKER','TAKER'):
            z=[x for x in rr if x['role']==role]
            summary[asset]['medianSharesByRole'][role]=statistics.median(x['shares'] for x in z) if z else None
            ts=[x['sec_left'] for x in z if x['sec_left'] is not None]
            summary[asset]['medianSecLeftByRole'][role]=statistics.median(ts) if ts else None

    # bins: compare response curves across normalized state
    bins_abs=[0,0.05,0.1,0.2,0.35,0.55,0.8,1.000001]
    bins_pc=[0,0.25,0.5,0.7,0.85,0.95,1.000001]
    def profile(field,bins):
        out={}
        for asset in ('BTC','ETH'):
            vals=[]
            for lo,hi in zip(bins[:-1],bins[1:]):
                z=[x for x in by_asset[asset] if lo<=x[field]<hi and x['rel']!='BAL']
                vals.append({'lo':lo,'hi':hi,'n':len(z),'takerFrac':sum(x['role']=='TAKER' for x in z)/len(z) if z else None,'weakFrac':sum(x['rel']=='WEAK' for x in z)/len(z) if z else None})
            out[asset]=vals
        return out
    profiles={'absnet':profile('absnet',bins_abs),'paircov':profile('paircov',bins_pc)}

    # cross-transfer models using only normalized state. Avoid asset id/absolute share sizing.
    model_result={}
    try:
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import balanced_accuracy_score, roc_auc_score, log_loss
        feats=['sec_left','paircov','absnet','floor_ratio','prior_taker_frac']
        def Xy(asset, task):
            rr=[x for x in by_asset[asset] if x['sec_left'] is not None and -30<=x['sec_left']<=330]
            if task=='weak': rr=[x for x in rr if x['rel'] in ('WEAK','DOM')]
            X=np.array([[max(-30,min(330,x['sec_left']))/300.0,x['paircov'],x['absnet'],max(-5,min(5,x['floor_ratio'])),x['prior_taker_frac']] for x in rr],dtype=float)
            if task=='role': y=np.array([1 if x['role']=='TAKER' else 0 for x in rr],dtype=int)
            else:y=np.array([1 if x['rel']=='WEAK' else 0 for x in rr],dtype=int)
            return X,y
        for task in ('role','weak'):
            model_result[task]={}
            for train_asset,test_asset in (('BTC','ETH'),('ETH','BTC')):
                Xtr,ytr=Xy(train_asset,task); Xte,yte=Xy(test_asset,task)
                # deterministic chronological thinning to keep compute bounded
                if len(Xtr)>180000:
                    idx=np.linspace(0,len(Xtr)-1,180000).astype(int); Xtr=Xtr[idx]; ytr=ytr[idx]
                if len(Xte)>180000:
                    idx=np.linspace(0,len(Xte)-1,180000).astype(int); Xte=Xte[idx]; yte=yte[idx]
                m=HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=15,learning_rate=0.08,l2_regularization=1.0,random_state=7)
                m.fit(Xtr,ytr); p=m.predict_proba(Xte)[:,1]; pred=(p>=0.5).astype(int)
                base=max(float(yte.mean()),1-float(yte.mean()))
                model_result[task][f'{train_asset}_to_{test_asset}']={
                  'trainN':len(ytr),'testN':len(yte),'testPositiveRate':float(yte.mean()),
                  'auc':float(roc_auc_score(yte,p)) if len(set(yte))>1 else None,
                  'balancedAccuracy':float(balanced_accuracy_score(yte,pred)),
                  'logLoss':float(log_loss(yte,p,labels=[0,1])),
                  'majorityAccuracy':base,
                  'features':feats
                }
    except Exception as e:
        model_result={'error':repr(e)}

    # same-window cross-asset coupling after removing deterministic phase effect.
    # Build 30s bins of counts/shares/role shares for paired window_end_ms.
    win_asset=defaultdict(lambda:defaultdict(lambda:defaultdict(lambda:{'n':0,'shares':0.0,'taker':0,'weak':0,'imb':0})))
    for x in states:
        end=markets.get((x['asset'],x['market_id'])); sl=x['sec_left']
        if end is None or sl is None or sl<0 or sl>300: continue
        b=min(9,max(0,int((300-sl)//30)))
        d=win_asset[end][x['asset']][b]; d['n']+=1; d['shares']+=x['shares']; d['taker']+=x['role']=='TAKER'; d['weak']+=x['rel']=='WEAK'; d['imb']+=x['rel'] in ('WEAK','DOM')
    common=[e for e,v in win_asset.items() if 'BTC' in v and 'ETH' in v]
    coupling={}
    for metric in ('n','shares','takerFrac','weakFrac'):
        xb=[]; xe=[]; perbin=defaultdict(lambda:[[],[]])
        raw=[]
        for e in common:
            for b in range(10):
                db=win_asset[e]['BTC'].get(b,{}); de=win_asset[e]['ETH'].get(b,{})
                def val(d):
                    if metric=='n':return float(d.get('n',0))
                    if metric=='shares':return math.log1p(float(d.get('shares',0.0)))
                    if metric=='takerFrac':return d.get('taker',0)/max(d.get('n',0),1)
                    return d.get('weak',0)/max(d.get('imb',0),1)
                vb,ve=val(db),val(de); raw.append((b,vb,ve)); perbin[b][0].append(vb);perbin[b][1].append(ve)
        # residualize by bin means
        bm={b:(sum(z[0])/len(z[0]) if z[0] else 0,sum(z[1])/len(z[1]) if z[1] else 0) for b,z in perbin.items()}
        for b,vb,ve in raw:
            xb.append(vb-bm[b][0]); xe.append(ve-bm[b][1])
        coupling[metric]={'residualCorrSame30s':corr(xb,xe),'nPoints':len(xb)}
    # shifted-window placebo: BTC window t vs ETH next window, same 30s phase residualized crudely
    ends=sorted(common); place={}
    for metric in ('n','shares'):
        a=[];b=[]
        for e1,e2 in zip(ends[:-1],ends[1:]):
            if e2-e1!=300000:continue
            for binx in range(10):
                d1=win_asset[e1]['BTC'].get(binx,{}); d2=win_asset[e2]['ETH'].get(binx,{})
                v1=float(d1.get('n',0)) if metric=='n' else math.log1p(float(d1.get('shares',0)))
                v2=float(d2.get('n',0)) if metric=='n' else math.log1p(float(d2.get('shares',0)))
                a.append(v1);b.append(v2)
        place[metric]={'nextWindowCorr':corr(a,b),'nPoints':len(a)}
    coupling['placebo']=place; coupling['commonWindows']=len(common)

    # paired terminal geometry and cross-asset correlations
    term={}
    res=list(c.execute("select asset,market_id,net_roi,up_position_shares,down_position_shares,buy_notional_usdt,net_pnl_usdt,winner from target_market_results where asset in ('BTC','ETH')"))
    by_end_res=defaultdict(dict)
    for r in res:
        end=markets.get((r['asset'],r['market_id']))
        up=float(r['up_position_shares']); dn=float(r['down_position_shares']); cost=float(r['buy_notional_usdt']); total=up+dn
        geom={'roi':r['net_roi'],'paircov':2*min(up,dn)/total if total else 1.0,'absnet':abs(up-dn)/total if total else 0.0,'floorRatio':(min(up,dn)-cost)/max(cost,1.0),'pnl':r['net_pnl_usdt'],'winner':r['winner']}
        if end is not None:by_end_res[end][r['asset']]=geom
    commonr=[v for v in by_end_res.values() if 'BTC'in v and 'ETH'in v]
    for k in ('roi','paircov','absnet','floorRatio'):
        xs=[float(v['BTC'][k]) for v in commonr if v['BTC'][k] is not None and v['ETH'][k] is not None]; ys=[float(v['ETH'][k]) for v in commonr if v['BTC'][k] is not None and v['ETH'][k] is not None]
        term[k+'Corr']=corr(xs,ys)
    term['sameWinnerRate']=sum(v['BTC']['winner']==v['ETH']['winner'] for v in commonr)/len(commonr) if commonr else None
    term['nCommon']=len(commonr)

    result={
      'version':'TARGET_ETH_BTC_CROSS_ASSET_ANATOMY_V1',
      'sourceDb':os.path.abspath(args.db),
      'scope':{'orders':len(states),'marketsByAsset':{a:len({x['market_id'] for x in rr}) for a,rr in by_asset.items()}},
      'summary':summary,'normalizedProfiles':profiles,'crossTransfer':model_result,
      'sameWindowCoupling':coupling,'terminalGeometryCoupling':term,
      'interpretationBoundary':[
        'Descriptive and predictive anatomy only; no causal claim.',
        'Parent average_price*shares is used as an inventory cost proxy for pre-order floor ratio.',
        'Cross-transfer excludes asset identity and absolute share size from features.',
        'Do not promote any action policy from this artifact alone.'
      ]
    }
    os.makedirs(os.path.dirname(args.output),exist_ok=True)
    with open(args.output,'w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'ok':True,'output':args.output,'crossTransfer':model_result,'coupling':coupling,'terminal':term},ensure_ascii=False))

if __name__=='__main__':main()
