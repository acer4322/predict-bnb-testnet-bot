from __future__ import annotations
import argparse,json,sqlite3,zlib,math,statistics
from pathlib import Path
from collections import defaultdict,Counter

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'data/target_wallet_official_v1.db'
BOOKS={'BTC':ROOT/'data/wallet_maker_book_inference.db','ETH':ROOT/'data/wallet_maker_book_inference_eth5m.db'}
EPS=1e-9; LOOKBACK_MS=60_000; POST_MS=5_000; GRID=.01

def dec(b):
    if not b:return None
    try:return json.loads(zlib.decompress(b).decode('utf-8'))
    except Exception:return None

def qtile(xs,q):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    z=(len(ys)-1)*q; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.90)}

def parse_hashes(payload):
    out=[]
    if isinstance(payload,list):items=payload
    elif isinstance(payload,dict):
        items=[]
        for k in ('matches','data','results'):
            if isinstance(payload.get(k),list):items.extend(payload[k])
        if not items and any(k in payload for k in ('maker','makers','priceExecuted','amountFilled')):items=[payload]
    else:items=[]
    for m in items:
        if not isinstance(m,dict):continue
        makers=[]
        if isinstance(m.get('maker'),dict):makers.append(m['maker'])
        if isinstance(m.get('makers'),list):makers.extend(x for x in m['makers'] if isinstance(x,dict))
        for maker in makers:
            h=str(maker.get('hash') or maker.get('orderHash') or '').lower()
            if h:out.append(h)
    return out

def load_adds(bookc,mid):
    adds=[]
    for r in bookc.execute('select source_timestamp_ms,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)):
        ch=dec(r['changes_z']) or {}; t=int(r['source_timestamp_ms'])
        if not isinstance(ch,dict):continue
        for key,side in (('bids','BID'),('asks','ASK')):
            vals=ch.get(key)
            if not isinstance(vals,list):continue
            for x in vals:
                if not isinstance(x,dict):continue
                try:p=float(x.get('price'));d=float(x.get('delta'))
                except Exception:continue
                if not math.isfinite(p) or not math.isfinite(d) or d<=EPS:continue
                adds.append({'t':t,'side':side,'price':round(p,8),'qty':d,'key':f'{t}:{side}:{p:.8f}:{len(adds)}'})
    return adds

def reconstruct_parent_placements(bookc,tc,asset,mid,adds):
    parents=[dict(r) for r in tc.execute("select parent_id,order_hash,side,average_price,shares,first_event_ms,last_event_ms,fill_legs from target_parent_orders where asset=? and market_id=? and role='MAKER' and quote_type='BID' order by first_event_ms,parent_id",(asset,mid))]
    rem={e['key']:float(e['qty']) for e in adds}
    maker_hashes=set()
    try:
        for rr in bookc.execute('select raw_json_z from maker_execution_matches_v1 where market_id=?',(mid,)):
            for h in parse_hashes(dec(rr[0])):maker_hashes.add(h)
    except sqlite3.OperationalError:pass
    out={}
    for p in parents:
        px=float(p['average_price']); obs=float(p['shares']); first=int(p['first_event_ms']); side=str(p['side'])
        native_side='BID' if side=='UP' else 'ASK'; native_price=round(px if side=='UP' else 1.0-px,8)
        legal=1.0/max(px,1e-9); lb=max(obs,legal)
        cand=[e for e in adds if e['side']==native_side and abs(e['price']-native_price)<=1e-8 and first-LOOKBACK_MS<=e['t']<first and rem[e['key']]>EPS]
        cand.sort(key=lambda e:e['t'],reverse=True);sel=[];need=lb
        for e in cand:
            if need<=EPS:break
            z=min(need,rem[e['key']])
            if z<=EPS:continue
            rem[e['key']]-=z;need-=z;sel.append((e['t'],z))
        alloc=lb-max(0.0,need);cov=min(1.,alloc/lb) if lb>EPS else 0.;ready=min((x[0] for x in sel),default=None)
        oh=str(p.get('order_hash') or '').lower(); hash_ok=bool(oh and oh in maker_hashes)
        rec={**p,'placementReadyMs':ready,'placementCoverage':cov,'hashConfirmed':hash_ok,'highConfidencePlacement':bool(hash_ok and cov>=.999 and ready is not None),'nativeSide':native_side,'nativePrice':native_price}
        out[str(p['parent_id'])]=rec
        if oh:out[oh]=rec
    return out

def post_action_proxy(adds,second_t,side,price):
    ns='BID' if side=='UP' else 'ASK'; np=price if side=='UP' else 1.0-price
    fut=[e for e in adds if second_t<e['t']<=second_t+POST_MS and e['side']==ns]
    if not fut:return 'NO_ADD_5S',None
    fut.sort(key=lambda e:e['t'])
    e=fut[0]; ticks=abs(float(e['price'])-float(np))/GRID
    if ticks<.5:a='SAME_PRICE_REFILL'
    elif ticks<=3.5:a='REPRICE_1_3T'
    else:a='REPRICE_4T_PLUS'
    return a,int(e['t'])-int(second_t)

def asset_run(asset,max_markets,cutoff):
    tc=sqlite3.connect(f'file:{TARGET.resolve().as_posix()}?mode=ro',uri=True);tc.row_factory=sqlite3.Row
    bp=BOOKS[asset];bc=sqlite3.connect(f'file:{bp.resolve().as_posix()}?mode=ro',uri=True);bc.row_factory=sqlite3.Row
    try:
        # Avoid a full DISTINCT scan of the large receipt-clock book table.
        cand=[int(r[0]) for r in tc.execute("select distinct market_id from wallet_shadow_target_events where asset=? and role='MAKER' and quote_type='BID' and market_id<=? order by market_id desc limit ?",(asset,cutoff,max_markets*5))]
        t_mids=[]
        for mid0 in cand:
            if bc.execute('select 1 from maker_book_inference_updates where market_id=? limit 1',(mid0,)).fetchone() is not None:
                t_mids.append(mid0)
                if len(t_mids)>=max_markets:break
        mids=sorted(t_mids);episodes=[];market_summ=[]
        for k,mid in enumerate(mids,1):
            fills=[dict(r) for r in tc.execute("select id,leg_id,order_hash,event_ms,side,price,shares from wallet_shadow_target_events where asset=? and market_id=? and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') order by event_ms,id",(asset,mid))]
            if len(fills)<2:continue
            # Cheap precheck on official fills: only touch the large receipt-clock book if this market actually contains an eligible first->opposite episode.
            pu=pd=pcost=0.;has_episode=False
            for ii,ff in enumerate(fills):
                gg=pu+pd;pp=min(pu,pd);pfl=pp-pcost;par=abs(pu-pd)/gg if gg>EPS else 0.;pfr=pfl/max(pcost,1.)
                if gg>EPS and pfl>=-EPS and par<=.02+EPS and pfr<.05-EPS:
                    ft=int(ff['event_ms']);fs=str(ff['side'])
                    for gg0 in fills[ii+1:]:
                        if int(gg0['event_ms'])-ft>30000:break
                        if str(gg0['side'])!=fs:has_episode=True;break
                sh0=float(ff['shares']);px0=float(ff['price']);pu+=sh0 if ff['side']=='UP' else 0.;pd+=sh0 if ff['side']=='DOWN' else 0.;pcost+=sh0*px0
                if has_episode:break
            if not has_episode:continue
            adds=load_adds(bc,mid); placements=reconstruct_parent_placements(bc,tc,asset,mid,adds)
            up=down=cost=0.;m_eps=0
            for i,f in enumerate(fills):
                gross=up+down;paired=min(up,down);floor=paired-cost;absr=abs(up-down)/gross if gross>EPS else 0.;fr=floor/max(cost,1.)
                eligible=(gross>EPS and floor>=-EPS and absr<=.02+EPS and fr<.05-EPS)
                if eligible:
                    first_t=int(f['event_ms']);first_side=str(f['side']);second=None
                    for g in fills[i+1:]:
                        dt=int(g['event_ms'])-first_t
                        if dt>30000:break
                        if str(g['side'])!=first_side:second=g;break
                    if second is not None:
                        second_t=int(second['event_ms']);oh=str(second.get('order_hash') or '').lower();pr=placements.get(oh)
                        # parent_id is usually hash; fallback by parent/order hash lookup already mapped.
                        ready=pr.get('placementReadyMs') if pr else None;hi=bool(pr and pr.get('highConfidencePlacement'))
                        parent_first=int(pr['first_event_ms']) if pr else None
                        if hi:
                            mech='PREPOSITIONED' if int(ready)<=first_t else 'POSTFILL_NEW'
                            post_lag=int(ready)-first_t;rest=second_t-int(ready)
                        else:mech='PLACEMENT_UNRESOLVED';post_lag=rest=None
                        carrier='EXISTING_PARENT' if parent_first is not None and parent_first<first_t else 'NEW_PARENT_AFTER_FIRST' if parent_first is not None and parent_first>=first_t else 'PARENT_UNRESOLVED'
                        pair=float(f['price'])+float(second['price']);pa,pad=post_action_proxy(adds,second_t,str(second['side']),float(second['price']))
                        episodes.append({'asset':asset,'marketId':mid,'firstEventMs':first_t,'firstSide':first_side,'firstPrice':float(f['price']),'firstShares':float(f['shares']),'secondEventMs':second_t,'secondSide':str(second['side']),'secondPrice':float(second['price']),'secondShares':float(second['shares']),'secondFillLagMs':second_t-first_t,'pairSum':pair,'cheapPair':pair<1.0-EPS,'placementMechanism':mech,'placementReadyMs':ready,'postFirstPlacementLagMs':post_lag,'secondRestMs':rest,'placementHighConfidence':hi,'placementCoverage':float(pr['placementCoverage']) if pr else None,'hashConfirmed':bool(pr and pr['hashConfirmed']),'parentCarrier':carrier,'secondParentFirstFillMs':parent_first,'postSecondActionProxy':pa,'postSecondActionDelayMs':pad,'preFloor':floor,'preFloorRatio':fr,'preAbsNetRatio':absr})
                        m_eps+=1
                sh=float(f['shares']);px=float(f['price'])
                if f['side']=='UP':up+=sh
                else:down+=sh
                cost+=sh*px
            market_summ.append({'marketId':mid,'fills':len(fills),'episodes':m_eps})
            if k%25==0:print(json.dumps({'asset':asset,'progress':k,'markets':len(mids),'episodes':len(episodes)}),flush=True)
        return mids,episodes,market_summ
    finally:tc.close();bc.close()

def summarize(rows):
    n=len(rows);hi=[r for r in rows if r['placementHighConfidence']];cheap=[r for r in rows if r['cheapPair']];non=[r for r in rows if not r['cheapPair']]
    def block(rr):
        nn=len(rr);hh=[r for r in rr if r['placementHighConfidence']];cnt=Counter(r['placementMechanism'] for r in hh);car=Counter(r['parentCarrier'] for r in rr);pa=Counter(r['postSecondActionProxy'] for r in rr)
        return {'episodes':nn,'markets':len({r['marketId'] for r in rr}),'cheapRate':sum(r['cheapPair'] for r in rr)/nn if nn else None,'placementResolvedRate':len(hh)/nn if nn else None,'prepositionedRateResolved':cnt['PREPOSITIONED']/len(hh) if hh else None,'postfillNewRateResolved':cnt['POSTFILL_NEW']/len(hh) if hh else None,'existingParentRate':car['EXISTING_PARENT']/nn if nn else None,'newParentAfterFirstRate':car['NEW_PARENT_AFTER_FIRST']/nn if nn else None,'secondFillLagMs':stats([r['secondFillLagMs'] for r in rr]),'postFirstPlacementLagMsResolved':stats([r['postFirstPlacementLagMs'] for r in hh]),'secondRestMsResolved':stats([r['secondRestMs'] for r in hh]),'postSecondActionProxyRates':{k:v/nn for k,v in pa.items()} if nn else {}}
    return {'overall':block(rows),'cheapPair':block(cheap),'nonCheapPair':block(non)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=150);ap.add_argument('--cutoff',type=int,default=1823545);ap.add_argument('--output',required=True);a=ap.parse_args()
    allrows=[];coverage={};summ={}
    for asset in ('BTC','ETH'):
        mids,rows,ms=asset_run(asset,a.max_markets,a.cutoff);allrows+=rows;coverage[asset]={'selectedMarkets':len(mids),'marketsWithEpisodes':len({r['marketId'] for r in rows}),'episodes':len(rows)};summ[asset]=summarize(rows)
    shared={}
    for metric in ('prepositionedRateResolved','existingParentRate'):
        vals={asset:summ[asset]['overall'].get(metric) for asset in ('BTC','ETH')};shared[metric]=vals
    out={'version':'TARGET_BTC_ETH_SECOND_LEG_EXECUTION_MECHANISM_V1','researchOnly':True,'cutoffExclusiveOrEqual':a.cutoff,'maxMarketsPerAsset':a.max_markets,'coverage':coverage,'summary':summ,'crossAssetNormalized':shared,'rows':allrows,'boundary':['Official Target Maker BID actual fills define execution chronology and inventory accounting.','Placement reconstruction uses only positive public depth adds, observed parent fill shares, legal minimum 1/price, and raw maker hash confirmation; no TARGET_UNIT=18, placement_supports_18, expected_parent_shares, winner or PnL.','Placement ownership is still probabilistic even when high-confidence; mechanism claims must be qualitative and cross-asset stable, not copied numeric thresholds.','Post-second-fill action is an anonymous public +depth proxy, not private cancel/replace ground truth.']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'coverage':coverage,'summary':summ,'crossAssetNormalized':shared},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
