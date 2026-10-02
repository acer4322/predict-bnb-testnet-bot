from __future__ import annotations
import json,zlib,sqlite3,math,statistics
from collections import defaultdict,deque
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/wallet_maker_book_inference.db'
OUTDIR=ROOT/'data/research/r4_v0/hourly'
CSV=OUTDIR/'r4_target_depth_native_hazard_v1.csv'
OUT=OUTDIR/'r4_target_depth_native_hazard_20260826_v1.json'
TICK=.01
TIME_PRICE=['seconds_left','best_bid','best_ask','mid','spread_ticks']
DEPTH=['bid_l1','ask_l1','bid_top3','ask_top3','bid_top5','ask_top5','bid_top10','ask_top10','sum_l1','min_l1','max_l1','abs_l1_imbalance','book_imb_l1','book_imb_top3','book_imb_top5','book_imb_top10','bid_levels','ask_levels','bid_l1_share_top5','ask_l1_share_top5','bid_add_1s','ask_add_1s','bid_remove_1s','ask_remove_1s','bid_add_3s','ask_add_3s','bid_remove_3s','ask_remove_3s','remove_imb_1s','remove_imb_3s','add_imb_1s','add_imb_3s']

def dec(v): return json.loads(zlib.decompress(v).decode('utf-8')) if v else None

def imb(a,b): return (a-b)/(a+b) if a+b>1e-12 else 0.
def qsum(d,ps): return sum(float(d.get(p,0.)) for p in ps)

def apply(book,changes):
    for k in ('bids','asks'):
        for ch in (changes or {}).get(k,[]) or []:
            p=float(ch['price']); a=float(ch['after'])
            if a<=1e-12: book[k].pop(p,None)
            else: book[k][p]=a

def feat(book,flow,t,end):
    bids,asks=book['bids'],book['asks']
    if not bids or not asks:return None
    bp=sorted(bids,reverse=True);ap=sorted(asks);bb,ba=bp[0],ap[0]
    z={'seconds_left':(end-t)/1000.,'best_bid':bb,'best_ask':ba,'mid':(bb+ba)/2,'spread_ticks':(ba-bb)/TICK,'bid_levels':len(bp),'ask_levels':len(ap)}
    for n in (1,3,5,10):
        bd=qsum(bids,bp[:n]);ad=qsum(asks,ap[:n]);
        z['bid_l1' if n==1 else f'bid_top{n}']=bd;z['ask_l1' if n==1 else f'ask_top{n}']=ad;z['book_imb_l1' if n==1 else f'book_imb_top{n}']=imb(bd,ad)
    z['sum_l1']=z['bid_l1']+z['ask_l1'];z['min_l1']=min(z['bid_l1'],z['ask_l1']);z['max_l1']=max(z['bid_l1'],z['ask_l1']);z['abs_l1_imbalance']=abs(z['book_imb_l1'])
    z['bid_l1_share_top5']=z['bid_l1']/z['bid_top5'] if z['bid_top5']>0 else 0.;z['ask_l1_share_top5']=z['ask_l1']/z['ask_top5'] if z['ask_top5']>0 else 0.
    for ms,s in ((1000,'1s'),(3000,'3s')):
        cutoff=t-ms;bad=aad=br=ar=0.
        for tt,x1,x2,x3,x4 in flow:
            if cutoff<=tt<=t:bad+=x1;aad+=x2;br+=x3;ar+=x4
        z[f'bid_add_{s}']=bad;z[f'ask_add_{s}']=aad;z[f'bid_remove_{s}']=br;z[f'ask_remove_{s}']=ar;z[f'remove_imb_{s}']=imb(br,ar);z[f'add_imb_{s}']=imb(bad,aad)
    return z

def build(max_markets=350):
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
    latest=int(c.execute('select max(source_timestamp_ms) from maker_book_inference_updates').fetchone()[0] or 0)
    markets=[dict(r) for r in c.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null and window_end_ms<=? order by window_end_ms desc',(latest-15000,))]
    markets=markets[:max_markets]
    mids=[int(x['market_id']) for x in markets]; ends={int(x['market_id']):int(x['window_end_ms']) for x in markets}
    placements=defaultdict(list)
    if mids:
        qs=','.join('?'*len(mids))
        for r in c.execute(f'''select market_id,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by market_id,placement_first_ms''',mids):placements[int(r['market_id'])].append(int(r['placement_first_ms']))
    rows=[]
    for ix,mid in enumerate(reversed(mids),1):
        ups=c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
        if not ups:continue
        book={'bids':{},'asks':{}};flow=deque();last_bucket=None;last_sample=None
        for u in ups:
            t=int(u['source_timestamp_ms']);chg=dec(u['changes_z']) or {}
            if int(u['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
            else:
                ba=aa=br=ar=0.
                for ch in chg.get('bids',[]) or []:d=float(ch['delta']);ba+=max(0.,d);br+=max(0.,-d)
                for ch in chg.get('asks',[]) or []:d=float(ch['delta']);aa+=max(0.,d);ar+=max(0.,-d)
                apply(book,chg);flow.append((t,ba,aa,br,ar))
            while flow and flow[0][0]<t-3000:flow.popleft()
            bucket=t//1000
            if last_bucket is None:last_bucket=bucket
            # overwrite within second; emitted later
            ff=feat(book,flow,t,ends[mid]);
            if ff is not None:last_sample=(t,ff)
            # emit only when next iteration enters new bucket, handled by peeking impossible; use bucket change before overwrite below
            # Instead preserve latest row per bucket in temporary dict.
            if bucket!=last_bucket:last_bucket=bucket
        # replay second pass with compact latest-per-second snapshots for correctness
        book={'bids':{},'asks':{}};flow=deque();latest_by_bucket={}
        for u in ups:
            t=int(u['source_timestamp_ms']);chg=dec(u['changes_z']) or {}
            if int(u['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
            else:
                ba=aa=br=ar=0.
                for ch in chg.get('bids',[]) or []:d=float(ch['delta']);ba+=max(0.,d);br+=max(0.,-d)
                for ch in chg.get('asks',[]) or []:d=float(ch['delta']);aa+=max(0.,d);ar+=max(0.,-d)
                apply(book,chg);flow.append((t,ba,aa,br,ar))
            while flow and flow[0][0]<t-3000:flow.popleft()
            ff=feat(book,flow,t,ends[mid])
            if ff is not None:latest_by_bucket[t//1000]=(t,ff)
        ps=placements.get(mid,[])
        import bisect
        for t,ff in latest_by_bucket.values():
            if ff['seconds_left']<0 or ff['seconds_left']>310:continue
            j=bisect.bisect_right(ps,t);nxt=ps[j] if j<len(ps) else None
            row={'market_id':mid,'sample_ms':t,'next_placement_ms':nxt,**ff}
            for h in (1,2,5):row[f'y{h}']=int(nxt is not None and t<nxt<=t+h*1000)
            rows.append(row)
        if ix%50==0:print(json.dumps({'marketsDone':ix,'rows':len(rows)}),flush=True)
    c.close();df=pd.DataFrame(rows).sort_values(['sample_ms','market_id']);CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False);return df

def safe_auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def folds(df):
    mm=df.groupby('market_id').sample_ms.min().sort_values();ms=[int(x) for x in mm.index];n=len(ms);test=max(15,min(30,n//8));mintr=max(50,n//3);last=max(mintr,n-test);starts=sorted(set(int(round(mintr+i*(last-mintr)/3)) for i in range(4)));return [(ms[:s],ms[s:min(n,s+test)]) for s in starts if len(ms[s:min(n,s+test)])>=10]
def evalset(df,label,features):
    out=[]
    for i,(trm,tem) in enumerate(folds(df)):
        tr=df[df.market_id.isin(trm)];te=df[df.market_id.isin(tem)];ytr=tr[label].astype(int);yte=te[label].astype(int)
        Xtr=tr[features].replace([np.inf,-np.inf],np.nan);Xte=te[features].replace([np.inf,-np.inf],np.nan)
        m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=180,l2_regularization=3.,class_weight='balanced',random_state=42+i).fit(Xtr,ytr);p=m.predict_proba(Xte)[:,1];prior=np.full(len(yte),float(ytr.mean()));bl=float(log_loss(yte,prior,labels=[0,1]));ml=float(log_loss(yte,p,labels=[0,1]));out.append({'fold':i,'testMarkets':len(tem),'testN':len(te),'positiveRate':float(yte.mean()),'auc':safe_auc(yte,p),'ap':float(average_precision_score(yte,p)),'logLoss':ml,'priorLogLoss':bl,'logLossLift':bl-ml})
    def av(k):
        z=[r[k] for r in out if r[k] is not None];return float(np.mean(z)) if z else None
    return {'features':features,'folds':out,'meanAuc':av('auc'),'meanAP':av('ap'),'meanLogLoss':av('logLoss'),'meanLogLossLift':av('logLossLift')}
def describe(df,h):
    pos=df[df[f'y{h}']==1];neg=df[df[f'y{h}']==0]
    keys=['sum_l1','min_l1','abs_l1_imbalance','book_imb_l1','bid_top5','ask_top5','bid_remove_1s','ask_remove_1s','bid_add_1s','ask_add_1s','spread_ticks']
    return {k:{'positiveMedian':float(pos[k].median()),'negativeMedian':float(neg[k].median()),'positiveMean':float(pos[k].mean()),'negativeMean':float(neg[k].mean())} for k in keys}
def main():
    df=build();sets={'TIME_PRICE':TIME_PRICE,'DEPTH_ONLY':DEPTH,'TIME_PRICE_DEPTH':TIME_PRICE+DEPTH};tasks={}
    for h in (1,2,5):
        rr={k:evalset(df,f'y{h}',v) for k,v in sets.items()};b=rr['TIME_PRICE'];x=rr['TIME_PRICE_DEPTH'];rr['incrementDepthOverTimePrice']={'deltaMeanAuc':x['meanAuc']-b['meanAuc'],'deltaMeanAP':x['meanAP']-b['meanAP'],'deltaMeanLogLossLift':x['meanLogLossLift']-b['meanLogLossLift']};rr['descriptive']=describe(df,h);tasks[f'next{h}s']=rr
    report={'version':'R4_TARGET_DEPTH_NATIVE_HAZARD_V1','researchOnly':True,'question':'Does native public depth/flow predict inferred Target Maker placement in the next 1/2/5s beyond time + native touch price?','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'placementMarkets':int(df[df.next_placement_ms.notna()].market_id.nunique()),'positiveRates':{f'next{h}s':float(df[f'y{h}'].mean()) for h in (1,2,5)}},'guards':{'inferredPlacementNotPrivateGroundTruth':True,'sourceTimestampClockUsedForBookAndPlacement':True,'winnerUnused':True,'futurePlacementSidePriceUnused':True,'noSignalDbJoin':True,'noThresholdSweep':True,'noLiveChanges':True},'featureSets':sets,'tasks':tasks,'interpretationRule':'Positive blocked-walk-forward lift from TIME_PRICE to TIME_PRICE_DEPTH supports public depth/queue state as an incremental placement trigger. If lift is weak while placement-depth choice remains predictable, depth is better interpreted as quote-level selector.'};OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':report['coverage'],'increments':{k:v['incrementDepthOverTimePrice'] for k,v in tasks.items()}},ensure_ascii=False))
if __name__=='__main__':main()
