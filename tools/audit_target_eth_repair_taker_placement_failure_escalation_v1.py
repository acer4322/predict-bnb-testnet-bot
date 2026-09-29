from __future__ import annotations
import argparse,json,math,sqlite3,zlib,joblib,os
from pathlib import Path
from collections import defaultdict,deque
import bisect,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
from tools import build_eth_target_teacher_policy_v1_dataset as base

BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
DS=BASE/'eth_maker_placement_eventclock_v3/dataset.npz'
META=BASE/'eth_maker_placement_eventclock_v3/dataset.meta.json'
FRESH=BASE/'eth_fresh150_inference_compact_v1.db'
OVER=BASE/'TARGET_ETH_REPAIR_TAKER_MAKER_PLACEMENT_OVERLAP_V1.json'
ANAT=BASE/'TARGET_ETH_REPAIR_TAKER_PASSIVE_OPTION_EXHAUSTION_ANATOMY_V1.json'
OUT=BASE/'TARGET_ETH_REPAIR_TAKER_PLACEMENT_FAILURE_ESCALATION_V1.json'
MODEL=BASE/'target_eth_repair_taker_portable_placement_teacher_v1.joblib'
OFFSETS=[10000,5000,3000,1000,0]
EXCLUDE={'last_placement_age_ms','last_placement_side_up','last_placement_qty','placement_events_2s','placement_events_10s'}

# update-dynamic features copied semantically from ETH_MAKER_PLACEMENT_EVENTCLOCK_V3 builder
EXTRA=['update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_d250','up_ask_d250','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_d1','up_ask_d1','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1']

def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None

def prior(hist,t,h,key):
    # hist sorted by receipt time
    for tt,v in reversed(hist):
        if tt<=t-h:return float(v.get(key,0.0))
    return float(hist[0][1].get(key,0.0)) if hist else 0.0

def qsum(a):
    a=[float(x) for x in a if x is not None and math.isfinite(float(x))]
    if not a:return {'n':0}
    x=np.asarray(a,float)
    return {'n':int(len(x)),'mean':float(np.mean(x)),'p25':float(np.quantile(x,.25)),'median':float(np.median(x)),'p75':float(np.quantile(x,.75)),'p90':float(np.quantile(x,.90))}

def train_portable():
    if MODEL.exists():
        d=joblib.load(MODEL)
        if isinstance(d,dict) and d.get('version')=='TARGET_ETH_PORTABLE_PLACEMENT_TEACHER_V1' and d.get('oldTest'):
            return d['oldTest'],d['features'],d['hazardModel'],d['weakModel'],d['priceOffsetModel'],float(d['hazardThreshold'])
    z=np.load(DS);meta=json.load(open(META,encoding='utf-8'));F=list(meta['features']);ix={k:i for i,k in enumerate(F)}
    names=[f for f in F if f not in EXCLUDE];cols=[ix[f] for f in names]
    X=z['X'][:,cols];mid=z['market_id'];ts=z['timestamp_ms'];yh=z['y_hazard'].astype(int);yw=z['y_weak'];yo=z['y_offset_ticks']
    markets=sorted(set(map(int,mid)),key=lambda m:int(ts[mid==m].min()));n=len(markets);a1=int(.70*n);a2=int(.85*n)
    sets={'train':set(markets[:a1]),'validation':set(markets[a1:a2]),'test':set(markets[a2:])}
    ind={k:np.where(np.isin(mid,list(v)))[0] for k,v in sets.items()}
    tr=ind['train'];va=ind['validation'];te=ind['test']
    hz=HistGradientBoostingClassifier(max_iter=280,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=80,l2_regularization=2.,class_weight='balanced',random_state=20260902).fit(X[tr],yh[tr])
    pva=hz.predict_proba(X[va])[:,1];val_rate=float(np.mean(yh[va]));th=float(np.quantile(pva,1-min(.999,max(.001,val_rate))))
    pos=np.where((yh==1)&np.isfinite(yw))[0];ptr=np.asarray([i for i in pos if int(mid[i]) in sets['train']],int)
    weak=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.,class_weight='balanced',random_state=20260903).fit(X[ptr],yw[ptr].astype(int))
    pg=np.where((yh==1)&np.isfinite(yo))[0];prtr=np.asarray([i for i in pg if int(mid[i]) in sets['train']],int)
    off=HistGradientBoostingRegressor(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=2.,random_state=20260904).fit(X[prtr],yo[prtr])
    def met(ii):
        p=hz.predict_proba(X[ii])[:,1];return {'n':int(len(ii)),'positiveRate':float(np.mean(yh[ii])),'rocAuc':float(roc_auc_score(yh[ii],p)),'averagePrecision':float(average_precision_score(yh[ii],p)),'aboveFrozenValRateThreshold':float(np.mean(p>=th))}
    wte=np.asarray([i for i in pos if int(mid[i]) in sets['test']],int);pw=weak.predict_proba(X[wte])[:,1]
    rep={'features':names,'excludedFeatures':sorted(EXCLUDE),'markets':{k:len(v) for k,v in sets.items()},'hazard':{'validation':met(va),'test':met(te),'frozenThresholdFromValidationRate':th},'weakConditionalTest':{'n':int(len(wte)),'rocAuc':float(roc_auc_score(yw[wte].astype(int),pw)) if len(np.unique(yw[wte]))>1 else None},'usable':bool(met(te)['rocAuc']>=.70)}
    joblib.dump({'version':'TARGET_ETH_PORTABLE_PLACEMENT_TEACHER_V1','features':names,'hazardModel':hz,'weakModel':weak,'priceOffsetModel':off,'hazardThreshold':th,'oldTest':rep},MODEL)
    return rep,names,hz,weak,off,th

def aggregate_wallet_events(con,mid):
    rows=con.execute('select role,side,order_hash,observed_at_ms,price,shares from maker_book_inference_wallet_events where market_id=? order by observed_at_ms,order_hash',(mid,)).fetchall()
    # aggregate only rows observed at same receipt for same order; do not pull later partial fills backward
    by={}
    for r in rows:
        k=(int(r['observed_at_ms']),str(r['order_hash']),str(r['role']),str(r['side']))
        q=float(r['shares'] or 0);px=float(r['price'] or 0)
        if k not in by:by[k]=[0.,0.]
        by[k][0]+=q;by[k][1]+=q*px
    out=[]
    for (t,oh,role,side),(q,n) in by.items():out.append({'t':t,'orderHash':oh,'role':role,'side':side,'shares':q,'price':n/q if q>0 else 0.})
    return sorted(out,key=lambda x:(x['t'],x['orderHash']))

def build_market_states(con,mid,query_times,feature_names):
    if not query_times:return {}
    earliest=int(min(query_times))-1600;latest=int(max(query_times))
    cp=con.execute('select received_at_ms from maker_book_inference_updates where market_id=? and is_checkpoint=1 and received_at_ms<=? order by received_at_ms desc,id desc limit 1',(mid,earliest)).fetchone()
    start=int(cp[0]) if cp else earliest-5000
    ups=list(con.execute('select id,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms>=? and received_at_ms<=? order by received_at_ms,id',(mid,start,latest)))
    if not ups:return {}
    mr=con.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone();end=int(mr[0] or 0) if mr else 0
    ev=aggregate_wallet_events(con,mid);ei=0;up=down=cost=0.;hist=deque();book={'bids':{},'asks':{}};chist=deque();shist=deque();states=[]
    for ur in ups:
        t=int(ur['received_at_ms'])
        while ei<len(ev) and int(ev[ei]['t'])<t:
            e=ev[ei];q=float(e['shares']);px=float(e['price']);sd=e['side'];role=e['role']
            if sd=='UP':up+=q
            else:down+=q
            cost+=q*px;hist.append((int(e['t']),role,sd,q,px));ei+=1
        while hist and t-hist[0][0]>30000:hist.popleft()
        cur={'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.,'levels':0}
        if int(ur['is_checkpoint']):
            book={'bids':{float(k):float(v) for k,v in (dec(ur['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(ur['native_asks_z']) or {}).items()}}
        else:
            ch=dec(ur['changes_z']) or {};base.apply_changes(book,ch)
            for sidekey in ('bids','asks'):
                for x in ch.get(sidekey,[]) or []:
                    d=float(x.get('delta') or 0);cur['levels']+=1
                    if d>=0:cur['bidadd' if sidekey=='bids' else 'askadd']+=d
                    else:cur['bidcut' if sidekey=='bids' else 'askcut']+=-d
        chist.append((t,cur.copy()))
        while chist and t-chist[0][0]>1500:chist.popleft()
        bf=base.book_features(book,int(ur['order_count'] or 0))
        if bf is None:continue
        bf['seconds_left']=(end-t)/1000 if end else 999.
        f,weakside=base.state_features(up,down,cost,hist,t,ei,len(ev),bf)
        now={'up_bid':bf['up_bid'],'up_ask':bf['up_ask'],'up_bid_depth':bf['up_bid_depth'],'up_ask_depth':bf['up_ask_depth'],'imbalance':bf['book_depth_imbalance']}
        f.update(update_add_qty=cur['bidadd']+cur['askadd'],update_cut_qty=cur['bidcut']+cur['askcut'],update_bid_add_qty=cur['bidadd'],update_ask_add_qty=cur['askadd'],update_bid_cut_qty=cur['bidcut'],update_ask_cut_qty=cur['askcut'],update_level_changes=float(cur['levels']))
        for h,nm in [(250,'250ms'),(1000,'1s')]:
            rr=[x for x in chist if t-x[0]<=h];f[f'updates_{nm}']=float(len(rr));f[f'add_qty_{nm}']=float(sum(x[1]['bidadd']+x[1]['askadd'] for x in rr));f[f'cut_qty_{nm}']=float(sum(x[1]['bidcut']+x[1]['askcut'] for x in rr));suf='250' if h==250 else '1';f[f'up_bid_d{suf}']=now['up_bid']-prior(shist,t,h,'up_bid') if shist else 0.;f[f'up_ask_d{suf}']=now['up_ask']-prior(shist,t,h,'up_ask') if shist else 0.;f[f'up_bid_depth_d{suf}']=now['up_bid_depth']-prior(shist,t,h,'up_bid_depth') if shist else 0.;f[f'up_ask_depth_d{suf}']=now['up_ask_depth']-prior(shist,t,h,'up_ask_depth') if shist else 0.;f[f'imbalance_d{suf}']=now['imbalance']-prior(shist,t,h,'imbalance') if shist else 0.
        states.append((t,[float(f.get(k,0.) or 0.) for k in feature_names],weakside,float(f['seconds_left']),float(f['weak_bid']),float(f['weak_ask'])))
        shist.append((t,now));
        while shist and t-shist[0][0]>1500:shist.popleft()
    times=[x[0] for x in states];out={}
    for qt in query_times:
        j=bisect.bisect_right(times,int(qt))-1
        if j>=0:out[int(qt)]=states[j]
    return out

def main():
    rep,names,hz,weakm,offm,th=train_portable()
    overlap=json.load(open(OVER,encoding='utf-8'));orows=overlap['rows'];anat=json.load(open(ANAT,encoding='utf-8'));arep=[r for r in anat['rows'] if r.get('role')=='REPAIR'];amap={(int(r['marketId']),str(r['orderHash'])):r for r in arep};an_by_evt=defaultdict(list)
    for a in arep:an_by_evt[(int(a['marketId']),int(a['t']),str(a['side']))].append(a)
    con=sqlite3.connect(f'file:{FRESH.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    # overlap artifact omits orderHash; resolve it deterministically against full Repair-Taker anatomy using event time/side and closest shares+price.
    end_by_mid={int(r[0]):int(r[1] or 0) for r in con.execute('select market_id,window_end_ms from maker_book_inference_markets')}
    bymid=defaultdict(list)
    for r in orows:
        cands=an_by_evt.get((int(r['marketId']),int(r['takerAt']),str(r['takerSide'])),[])
        if not cands:continue
        ar=min(cands,key=lambda a:abs(float(a.get('shares') or 0)-float(r.get('takerShares') or 0))+100.0*abs(float(a.get('price') or 0)-float(r.get('takerAvgPrice') or 0)))
        oh=str(ar['orderHash'])
        z=con.execute('select min(observed_at_ms) from maker_book_inference_wallet_events where market_id=? and order_hash=? and role="TAKER"',(int(r['marketId']),oh)).fetchone();obs=int(z[0]) if z and z[0] is not None else None
        if obs is None:continue
        end=end_by_mid.get(int(r['marketId']),0)
        if not end or (end-obs)/1000.0<=180.0:continue
        x=dict(r);x['orderHash']=oh;x['takerObservedAtMs']=obs; bymid[int(r['marketId'])].append(x)
    scored=[]
    all_items=sorted(bymid.items())
    batch_start=int(os.environ.get('BTC5M_BATCH_START','0'))
    batch_count=int(os.environ.get('BTC5M_BATCH_COUNT','0'))
    batch_items=all_items[batch_start:(batch_start+batch_count) if batch_count>0 else None]
    for mi,(mid,rows) in enumerate(batch_items,1):
        qs=[]
        for r in rows:
            for o in OFFSETS:qs.append(int(r['takerObservedAtMs'])-o)
        smap=build_market_states(con,mid,sorted(set(qs)),names)
        ev=aggregate_wallet_events(con,mid)
        for r in rows:
            anchor=int(r['takerObservedAtMs']);sr={'marketId':mid,'orderHash':r['orderHash'],'state':r['state'],'takerSide':r['takerSide'],'takerObservedAtMs':anchor,'pendingPlacementAgeMs':r.get('pendingPlacementAgeMs'),'pendingFirstFillLeadAfterTakerMs':r.get('pendingFirstFillLeadAfterTakerMs'),'pendingBehindBestTicks':r.get('pendingBehindBestTicks')}
            ar=amap.get((mid,str(r['orderHash']))) or {};sr.update({k:ar.get(k) for k in ['firstRepairTakerInMarket','activeDistinct_pre','parentStarts_1s','parentEnds_1s','parentStarts_3s','parentEnds_3s','parentStarts_10s','parentEnds_10s']})
            for w in [1000,3000,5000,10000]:
                zz=[e for e in ev if anchor-w<e['t']<anchor and e['role']=='MAKER' and e['side']==r['takerSide']]
                sr[f'makerFillCount_{w//1000}s']=len(zz);sr[f'makerFillShares_{w//1000}s']=float(sum(e['shares'] for e in zz))
            for o in OFFSETS:
                qt=anchor-o;s=smap.get(qt)
                pref=f'm{o//1000}s' if o else 'pre'
                if s is None:continue
                rt,vec,weakside,sec,wb,wa=s;X=np.asarray(vec,float).reshape(1,-1);ph=float(hz.predict_proba(X)[0,1]);pw=float(weakm.predict_proba(X)[0,1]);po=float(offm.predict(X)[0])
                sr[f'{pref}_receiptAgeMs']=int(qt-rt);sr[f'{pref}_secondsLeft']=sec;sr[f'{pref}_placementHazard']=ph;sr[f'{pref}_weakGivenPlacement']=pw;sr[f'{pref}_weakPlacementPropensity']=ph*pw;sr[f'{pref}_predOffsetTicks']=po;sr[f'{pref}_weakSide']=weakside;sr[f'{pref}_takerMatchesWeak']=bool(weakside==r['takerSide']) if weakside else None;sr[f'{pref}_hazardAboveFrozenOldValThreshold']=bool(ph>=th)
            scored.append(sr)
        if mi%25==0:print(json.dumps({'progressMarkets':mi,'of':len(batch_items),'batchStart':batch_start,'totalMarkets':len(all_items),'scored':len(scored)}),flush=True)
    con.close()
    def summarize(rows):
        out={'n':len(rows),'markets':len(set(r['marketId'] for r in rows))}
        for pref in ['m10s','m5s','m3s','m1s','pre']:
            for k in ['placementHazard','weakPlacementPropensity','predOffsetTicks']:
                out[f'{pref}_{k}']=qsum([r.get(f'{pref}_{k}') for r in rows])
            vals=[r.get(f'{pref}_hazardAboveFrozenOldValThreshold') for r in rows if r.get(f'{pref}_hazardAboveFrozenOldValThreshold') is not None];out[f'{pref}_aboveFrozenThresholdRate']=float(np.mean(vals)) if vals else None
        for w in [1,3,5,10]:
            out[f'makerFillCount_{w}s']=qsum([r.get(f'makerFillCount_{w}s') for r in rows]);out[f'zeroMakerFill_{w}sRate']=float(np.mean([r.get(f'makerFillCount_{w}s',0)==0 for r in rows])) if rows else None
        for k in ['pendingPlacementAgeMs','pendingFirstFillLeadAfterTakerMs','pendingBehindBestTicks','activeDistinct_pre','parentStarts_1s','parentEnds_1s','parentStarts_3s','parentEnds_3s','parentStarts_10s','parentEnds_10s']:
            out[k]=qsum([r.get(k) for r in rows])
        return out
    states=sorted(set(r['state'] for r in scored));groups={s:summarize([r for r in scored if r['state']==s]) for s in states};groups['ALL']=summarize(scored);groups['FIRST_REPAIR_PER_MARKET']=summarize([r for r in scored if r.get('firstRepairTakerInMarket')])
    # Opportunity-unpaid diagnostic: continuous scores, no fresh threshold fitting
    primary=[r for r in scored if r.get('m1s_secondsLeft',0)>180]
    unpaid=[r for r in primary if r.get('makerFillCount_5s',0)==0]
    paid=[r for r in primary if r.get('makerFillCount_5s',0)>0]
    comparison={'primaryDomainRows':len(primary),'unpaid5s':summarize(unpaid),'paid5s':summarize(paid)}
    # direct lifecycle facts independent of predictor
    pending=[r for r in scored if r['state'] in ('REPAIR_MAKER_PENDING_AT_TAKER','BOTH_FILLED_AND_PENDING')]
    direct={'pendingAtTakerRows':len(pending),'pendingRate':len(pending)/len(scored) if scored else None,'pendingAge':qsum([r.get('pendingPlacementAgeMs') for r in pending]),'pendingFirstFillLeadAfterTaker':qsum([r.get('pendingFirstFillLeadAfterTakerMs') for r in pending]),'pendingBehindBestTicks':qsum([r.get('pendingBehindBestTicks') for r in pending]),'pendingAtOrNear1TickRate':float(np.mean([float(r['pendingBehindBestTicks'])<=1.0+1e-9 for r in pending if r.get('pendingBehindBestTicks') is not None])) if any(r.get('pendingBehindBestTicks') is not None for r in pending) else None}
    out={'version':'TARGET_ETH_REPAIR_TAKER_PLACEMENT_FAILURE_ESCALATION_V1','researchOnly':True,'actionAuthority':False,'preRegistration':str(BASE/'TARGET_ETH_REPAIR_TAKER_PLACEMENT_FAILURE_ESCALATION_V1_PREREGISTERED.json'),'portableTeacherOldChronology':rep,'freshCoverage':{'scoredRepairTakerParents':len(scored),'markets':len(set(r['marketId'] for r in scored))},'directLifecycle':direct,'groups':groups,'opportunityVsPaymentComparison':comparison,'rows':scored,'boundary':['fresh scores use public maker_book_inference_updates at received_at_ms <= fixed pre-Taker receipt clocks','current Taker excluded from state because wallet events must be observed strictly before scored public receipt','portable teacher trained only on old 269-market event-clock placement dataset and frozen before fresh scoring','placement-history features excluded because fresh150 does not contain complete unfilled/cancel placement history','no winner/PnL/future price','prediction is diagnostic readiness/opportunity only; never Taker authority']}
    suffix=os.environ.get('BTC5M_BATCH_SUFFIX','').strip(); out_path=OUT if not suffix else OUT.with_name(OUT.stem+'_'+suffix+OUT.suffix); out_path.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'oldTestAuc':rep['hazard']['test']['rocAuc'],'portableUsable':rep['usable'],'freshRows':len(scored),'markets':out['freshCoverage']['markets'],'directLifecycle':direct,'unpaid5s_m1Hazard':comparison['unpaid5s'].get('m1s_placementHazard'),'paid5s_m1Hazard':comparison['paid5s'].get('m1s_placementHazard'),'output':str(out_path)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
