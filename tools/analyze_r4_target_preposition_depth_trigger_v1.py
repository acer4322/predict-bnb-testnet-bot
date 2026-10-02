from __future__ import annotations
import csv,json,zlib,sqlite3,math,statistics
from collections import defaultdict,deque
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
HAZ=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
BOOK=ROOT/'data/wallet_maker_book_inference.db'
AUG=ROOT/'data/research/r4_v0/hourly/r4_target_preposition_depth_hazard_v1.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_preposition_depth_trigger_20260826_v1.json'
TICK=.01
PRICE=['predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
TIME_PRICE=['seconds_left']+PRICE
DEPTH=['native_best_bid','native_best_ask','native_spread_ticks','native_mid','bid_l1','ask_l1','bid_top3','ask_top3','bid_top5','ask_top5','bid_top10','ask_top10','sum_l1','min_l1','max_l1','abs_l1_imbalance','book_imb_l1','book_imb_top3','book_imb_top5','book_imb_top10','bid_levels','ask_levels','book_age_ms','bid_add_1s','ask_add_1s','bid_remove_1s','ask_remove_1s','bid_add_3s','ask_add_3s','bid_remove_3s','ask_remove_3s','remove_imb_1s','remove_imb_3s','add_imb_1s','add_imb_3s']

def dec(v): return json.loads(zlib.decompress(v).decode('utf-8')) if v else None

def apply(book,changes):
    for k in ('bids','asks'):
        side=book[k]
        for ch in (changes or {}).get(k,[]) or []:
            p=float(ch['price']); a=float(ch['after'])
            if a<=1e-12: side.pop(p,None)
            else: side[p]=a

def imb(a,b): return (a-b)/(a+b) if a+b>1e-12 else 0.
def fsum(d,prices): return sum(float(d.get(p,0.)) for p in prices)

def book_features(book,last_recv,decision,flow):
    bids=book['bids']; asks=book['asks']
    if not bids or not asks: return None
    bp=sorted(bids,reverse=True); ap=sorted(asks)
    bb,ba=bp[0],ap[0]
    vals={}
    vals['native_best_bid']=bb; vals['native_best_ask']=ba; vals['native_spread_ticks']=(ba-bb)/TICK; vals['native_mid']=(bb+ba)/2
    for n in (1,3,5,10):
        bd=fsum(bids,bp[:n]); ad=fsum(asks,ap[:n]); vals[f'bid_top{n}' if n>1 else 'bid_l1']=bd; vals[f'ask_top{n}' if n>1 else 'ask_l1']=ad
        vals[f'book_imb_top{n}' if n>1 else 'book_imb_l1']=imb(bd,ad)
    vals['sum_l1']=vals['bid_l1']+vals['ask_l1']; vals['min_l1']=min(vals['bid_l1'],vals['ask_l1']); vals['max_l1']=max(vals['bid_l1'],vals['ask_l1']); vals['abs_l1_imbalance']=abs(vals['book_imb_l1'])
    vals['bid_levels']=len(bids); vals['ask_levels']=len(asks); vals['book_age_ms']=decision-last_recv if last_recv is not None else None
    for ms in (1000,3000):
        cutoff=decision-ms; ba=aa=br=ar=0.
        for t,xba,xaa,xbr,xar in flow:
            if t>=cutoff and t<decision: ba+=xba; aa+=xaa; br+=xbr; ar+=xar
        s='1s' if ms==1000 else '3s'
        vals[f'bid_add_{s}']=ba;vals[f'ask_add_{s}']=aa;vals[f'bid_remove_{s}']=br;vals[f'ask_remove_{s}']=ar
        vals[f'remove_imb_{s}']=imb(br,ar); vals[f'add_imb_{s}']=imb(ba,aa)
    return vals

def augment():
    h=pd.read_csv(HAZ); h['market_id']=pd.to_numeric(h.market_id).astype(int);h['decision_sampled_at_ms']=pd.to_numeric(h.decision_sampled_at_ms).astype('int64')
    c=sqlite3.connect(f'file:{BOOK.as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
    out=[]; covered=0; missing=0
    for mid,g in h.groupby('market_id',sort=False):
        gs=g.sort_values('decision_sampled_at_ms'); times=gs.decision_sampled_at_ms.tolist(); i=0; book={'bids':{},'asks':{}}; last_recv=None; flow=deque()
        ups=c.execute('select received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by received_at_ms,id',(int(mid),)).fetchall()
        for _,row in gs.iterrows():
            d=int(row['decision_sampled_at_ms'])
            while i<len(ups) and int(ups[i]['received_at_ms'])<d:
                u=ups[i];t=int(u['received_at_ms']);chg=dec(u['changes_z']) or {}
                if int(u['is_checkpoint']):
                    book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
                else:
                    ba=aa=br=ar=0.
                    for ch in chg.get('bids',[]) or []:
                        delta=float(ch['delta']);ba+=max(0.,delta);br+=max(0.,-delta)
                    for ch in chg.get('asks',[]) or []:
                        delta=float(ch['delta']);aa+=max(0.,delta);ar+=max(0.,-delta)
                    apply(book,chg); flow.append((t,ba,aa,br,ar))
                last_recv=t;i+=1
                while flow and flow[0][0]<d-3000: flow.popleft()
            feat=book_features(book,last_recv,d,flow)
            rr=row.to_dict()
            if feat is None: missing+=1
            else: rr.update(feat);covered+=1
            out.append(rr)
    c.close(); df=pd.DataFrame(out); AUG.parent.mkdir(parents=True,exist_ok=True);df.to_csv(AUG,index=False);return df,covered,missing

def auc(y,p):
    return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def ll(y,p): return float(log_loss(y,p,labels=[0,1]))
def folds(df):
    mm=df.groupby('market_id').decision_sampled_at_ms.min().sort_values(); markets=[int(x) for x in mm.index]
    # expanding chronological folds, 18-market test windows spread from market 60 to latest
    starts=sorted(set(int(round(60+i*(max(60,len(markets)-18)-60)/3)) for i in range(4)))
    return [(markets[:s],markets[s:min(len(markets),s+18)]) for s in starts if len(markets[s:min(len(markets),s+18)])>=9]
def evaluate(df,label,features):
    rs=[]
    for fi,(trm,tem) in enumerate(folds(df)):
        tr=df[df.market_id.isin(trm)].copy();te=df[df.market_id.isin(tem)].copy()
        Xtr=tr[features].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan);Xte=te[features].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan)
        ytr=tr[label].astype(int);yte=te[label].astype(int)
        m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=180,l2_regularization=3.0,class_weight='balanced',random_state=100+fi).fit(Xtr,ytr)
        p=m.predict_proba(Xte)[:,1]; prior=np.full(len(yte),float(ytr.mean()))
        rs.append({'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainN':len(tr),'testN':len(te),'positiveRate':float(yte.mean()),'auc':auc(yte,p),'ap':float(average_precision_score(yte,p)),'logLoss':ll(yte,p),'priorLogLoss':ll(yte,prior),'logLossLiftVsPrior':ll(yte,prior)-ll(yte,p)})
    def mean(k):
        a=[r[k] for r in rs if r[k] is not None];return float(np.mean(a)) if a else None
    return {'features':features,'folds':rs,'meanAuc':mean('auc'),'meanAP':mean('ap'),'meanLogLoss':mean('logLoss'),'meanLogLossLiftVsPrior':mean('logLossLiftVsPrior')}

def main():
    df,covered,missing=augment();usable=df[df['native_best_bid'].notna()].copy()
    fs={'TIME_PRICE':TIME_PRICE,'DEPTH_ONLY':DEPTH,'TIME_PRICE_DEPTH':TIME_PRICE+DEPTH}
    tasks={}
    for h in (1,2,5):
        label=f'label_next_inferred_placement_any_{h}s';tasks[f'next{h}s']={k:evaluate(usable,label,v) for k,v in fs.items()}
        base=tasks[f'next{h}s']['TIME_PRICE'];full=tasks[f'next{h}s']['TIME_PRICE_DEPTH']
        tasks[f'next{h}s']['incrementDepthOverTimePrice']={'deltaMeanAuc':full['meanAuc']-base['meanAuc'],'deltaMeanAP':full['meanAP']-base['meanAP'],'deltaMeanLogLossLift':full['meanLogLossLiftVsPrior']-base['meanLogLossLiftVsPrior']}
    report={'version':'R4_TARGET_PREPOSITION_DEPTH_TRIGGER_V1','researchOnly':True,'question':'Does strict-past public book depth add predictive value for inferred Target Maker placement hazard after controlling Predict price/time?','guards':{'inferredPlacementNotPrivateGroundTruth':True,'bookUpdatesStrictlyReceivedBeforeDecision':True,'futureSideOrPlacementPriceUsedAsFeature':False,'winnerUsed':False,'noLiveChange':True,'noThresholdSweep':True},'coverage':{'hazardRows':len(df),'bookCoveredRows':len(usable),'missingBookRows':missing,'markets':int(usable.market_id.nunique())},'featureSets':fs,'tasks':tasks,'interpretationRule':'Meaningful positive OOF lift of TIME_PRICE_DEPTH over TIME_PRICE supports depth/queue state as an incremental pre-position trigger. Little/no lift means depth is more likely a level/quote selector than the primary decision-to-place trigger.'}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':report['coverage'],'summary':{k:v.get('incrementDepthOverTimePrice') for k,v in tasks.items()}},ensure_ascii=False))
if __name__=='__main__':main()
