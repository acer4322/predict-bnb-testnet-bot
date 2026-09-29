from __future__ import annotations
import argparse,bisect,json,lzma,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9; GRID=0.01; ENTRY_MS=250

def k(p):return round(float(p),10)
def finite(x,d=0.0):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except Exception:return d

def book_features(book,native_side,native_price):
    bids=book['bids'];asks=book['asks'];bb=max(bids) if bids else math.nan;ba=min(asks) if asks else math.nan
    same=bids if native_side=='BUY' else asks;opp=asks if native_side=='BUY' else bids
    sb=bb if native_side=='BUY' else ba;ob=ba if native_side=='BUY' else bb
    same_top=float(same.get(k(sb),0.0)) if math.isfinite(sb) else 0.0;opp_top=float(opp.get(k(ob),0.0)) if math.isfinite(ob) else 0.0
    if native_side=='BUY':dist=(sb-native_price)/GRID if math.isfinite(sb) else math.nan; same_prices=sorted(bids,reverse=True)[:3];opp_prices=sorted(asks)[:3]
    else:dist=(native_price-sb)/GRID if math.isfinite(sb) else math.nan; same_prices=sorted(asks)[:3];opp_prices=sorted(bids,reverse=True)[:3]
    b3=sum(float(bids[p]) for p in sorted(bids,reverse=True)[:3]);a3=sum(float(asks[p]) for p in sorted(asks)[:3]);den=b3+a3
    return {'sameBestNative':finite(sb,math.nan),'oppositeBestNative':finite(ob,math.nan),'distanceFromSameBestTicks':finite(dist,math.nan),
            'sameTopQty':same_top,'oppositeTopQty':opp_top,'sameTop3Qty':sum(float(same[p]) for p in same_prices),'oppositeTop3Qty':sum(float(opp[p]) for p in opp_prices),
            'nativeSpreadTicks':(ba-bb)/GRID if math.isfinite(bb) and math.isfinite(ba) else math.nan,'nativeBookImbalance':(b3-a3)/den if den>EPS else 0.0,
            'sameBookLevels':float(len(same)),'oppositeBookLevels':float(len(opp)),'currentOwnLevelQty':float(same.get(k(native_price),0.0))}
def apply_depth(book,ev,px,qty):
    side='bids' if ev&int(ex.BUY_EVENT) else 'asks';p=k(px);q=max(0.0,float(qty))
    if q<=EPS:book[side].pop(p,None)
    else:book[side][p]=q

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--structural-rows',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[json.loads(x) for x in Path(a.structural_rows).read_text(encoding='utf-8').splitlines() if x.strip()]
    by=defaultdict(list)
    for r in rows:
        if str(r.get('route'))=='PASSIVE' and int(r.get('firstEventObserved') or 0)==1:by[int(r['marketId'])].append(r)
    tmp=Path(tempfile.mkdtemp(prefix='frontq_h100_'));out=[];audit=[]
    try:
        with zipfile.ZipFile(a.bundle) as z:
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        for mi,(m,mrows) in enumerate(sorted(by.items()),1):
            payload=json.loads(lzma.decompress((tmp/f'{m}.json.xz').read_bytes()).decode('utf-8'));ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])));uts=[int(u[1]) for u in ups];ocs=[float(u[2]) for u in ups]
            feed.ARCHIVE_DIR=tmp;events,_,meta=feed.build_archive_events(m,trade_offset='mid')
            latency_nonzero=int(((events['local_ts']-events['exch_ts'])!=0).sum())
            specs=[]
            for r in mrows:
                submit=int(r['t']);accept=submit+ENTRY_MS;first_t=submit+int(r['firstEventLagMs']);native_side,native_price=ex.native_order(str(r['side']),float(r['price']))
                specs.append({'row':r,'submit':submit,'accept':accept,'first':first_t,'nativeSide':native_side,'price':k(native_price),'q':None,'initial':None,'minLevel':None,'tradeQty':0.0,'tradeEvents':0,'depthEvents':0,'cross':None})
            specs.sort(key=lambda x:x['accept']); ai=0;active=[];book={'bids':{},'asks':{}}
            def oc_at(t):
                j=bisect.bisect_right(uts,int(t))-1;return ocs[j] if j>=0 else math.nan
            def capture(s,t,cause):
                if int(t)>=int(s['first']) or s['cross'] is not None:return
                bf=book_features(book,s['nativeSide'],s['price']);r=s['row'];q0=float(s['initial'] or 0.0);age=int(t)-int(s['accept'])
                x={'marketId':m,'key':str(r['key']),'role':str(r['role']),'side':str(r['side']),'submitT':s['submit'],'exchangeAcceptT':s['accept'],'frontStateT':int(t),'frontStateCause':cause,
                   'firstStructuralT':s['first'],'timeToFrontMs':age,'timeFrontToFirstEventMs':int(s['first'])-int(t),'price':float(r['price']),'nativePrice':s['price'],'qty':float(r['qty']),
                   'queueInitial':q0,'queueFrontAtState':max(0.0,float(s['q'])),'queueProgressFrac':(q0-max(0.0,float(s['q'])))/q0 if q0>EPS else 1.0,
                   'minOwnLevelSinceAccept':float(s['minLevel'] or 0.0),'samePriceContraTradeQtyBeforeFront':float(s['tradeQty']),'samePriceContraTradeEventsBeforeFront':int(s['tradeEvents']),'samePriceDepthEventsBeforeFront':int(s['depthEvents']),
                   'orderCountAtAccept':oc_at(s['accept']),'orderCountAtFront':oc_at(t),'orderCountDeltaToFront':oc_at(t)-oc_at(s['accept']),
                   'fillFirst':int(r['fillFirst']),'terminalFirst':int(r['terminalFirst']),'firstEventLagMs':int(r['firstEventLagMs']),'firstFillQty':float(r['firstFillQty']),'firstRepairPayQty':float(r['firstRepairPayQty']),'isRepairRole':int(float(r.get('isRepairRole') or 0)>0.5),'isExpandRole':int(float(r.get('isExpandRole') or 0)>0.5),
                   **bf}
                s['cross']=x;out.append(x)
            evs=list(events); idx=0
            while idx<len(evs):
                e=evs[idx];ts=int(e['local_ts']//1_000_000)
                # activate orders strictly before/equal this market event, using book just before event at same clock.
                while ai<len(specs) and specs[ai]['accept']<=ts:
                    s=specs[ai];same=book['bids'] if s['nativeSide']=='BUY' else book['asks'];q=float(same.get(s['price'],0.0));s['q']=q;s['initial']=q;s['minLevel']=q;active.append(s)
                    if q<=EPS:capture(s,s['accept'],'INITIAL_ZERO')
                    ai+=1
                active=[s for s in active if s['cross'] is None and ts<s['first']]
                ev=int(e['ev']);px=k(e['px']);qty=float(e['qty'])
                if ev&int(ex.DEPTH_EVENT) or ev&int(ex.DEPTH_SNAPSHOT_EVENT):
                    apply_depth(book,ev,px,qty)
                    for s in active:
                        same=(s['nativeSide']=='BUY' and (ev&int(ex.BUY_EVENT))) or (s['nativeSide']=='SELL' and (ev&int(ex.SELL_EVENT)))
                        if same and abs(px-s['price'])<=1e-9:
                            s['q']=min(float(s['q']),max(0.0,qty));s['minLevel']=min(float(s['minLevel']),max(0.0,qty));s['depthEvents']+=1
                            if s['q']<=EPS:capture(s,ts,'DEPTH_ZERO')
                elif ev&int(ex.TRADE_EVENT):
                    for s in active:
                        contra=(s['nativeSide']=='BUY' and (ev&int(ex.SELL_EVENT))) or (s['nativeSide']=='SELL' and (ev&int(ex.BUY_EVENT)))
                        if contra and abs(px-s['price'])<=1e-9:
                            s['q']=float(s['q'])-qty;s['tradeQty']+=qty;s['tradeEvents']+=1
                            # Deliberately do NOT capture trade-caused crossing: it leaks the fill-driving event.
                idx+=1
            # activate/capture any initial-zero orders after last market event only if still before their first event; rare.
            for s in specs[ai:]:
                if s['accept']<s['first']:
                    same=book['bids'] if s['nativeSide']=='BUY' else book['asks'];q=float(same.get(s['price'],0.0));s['q']=q;s['initial']=q;s['minLevel']=q
                    if q<=EPS:capture(s,s['accept'],'INITIAL_ZERO')
            audit.append({'marketId':m,'inputRows':len(specs),'frontStates':sum(1 for s in specs if s['cross'] is not None),'feedLatencyNonzero':latency_nonzero})
            if mi%20==0:print(json.dumps({'progress':mi,'of':len(by),'frontStates':len(out)}),flush=True)
        cnt=Counter((x['fillFirst'],x['frontStateCause']) for x in out);markets=sorted({x['marketId'] for x in out});
        report={'version':'LANE_G_FRONT_OF_QUEUE_H100_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':out,'audit':audit,
                'coverage':{'inputRows':len(rows),'frontStates':len(out),'markets':len(markets),'fillFirst':sum(x['fillFirst'] for x in out),'terminalFirst':sum(x['terminalFirst'] for x in out),'causeCounts':{f'{k[0]}_{k[1]}':v for k,v in cnt.items()}},
                'invariants':{'allFeedLatencyZero':all(x['feedLatencyNonzero']==0 for x in audit),'allFrontBeforeFirstEvent':all(x['frontStateT']<x['firstStructuralT'] for x in out),'noTradeCausedFrontStates':all(x['frontStateCause']!='TRADE' for x in out),'noNegativeFront':all(x['queueFrontAtState']>=-EPS for x in out)},
                'boundary':['H100 consumed PASSIVE carriers only','front state retained only if RiskAdverse queue reaches zero at acceptance or same-price depth update strictly before carrier first structural event','trade-caused zero crossing excluded to prevent label leakage','event-driven path since exchange acceptance; no fixed policy window','execution/book features only at front state; no stale portfolio interpolation','no fresh/no Target future/no winner input/no 8781']}
        Path(a.output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'coverage':report['coverage'],'invariants':report['invariants']},ensure_ascii=False,indent=2))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
