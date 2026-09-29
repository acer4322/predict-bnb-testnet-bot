from __future__ import annotations
import argparse,bisect,json,lzma,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9;GRID=0.01;ENTRY_MS=250

def k(p):return round(float(p),10)
def finite(x,d=0.0):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except Exception:return d
def apply_depth(book,ev,px,qty):
    side='bids' if ev&int(ex.BUY_EVENT) else 'asks';p=k(px);q=max(0.0,float(qty))
    if q<=EPS:book[side].pop(p,None)
    else:book[side][p]=q
def book_features(book,native_side,native_price):
    bids=book['bids'];asks=book['asks'];bb=max(bids) if bids else math.nan;ba=min(asks) if asks else math.nan;same=bids if native_side=='BUY' else asks;opp=asks if native_side=='BUY' else bids;sb=bb if native_side=='BUY' else ba;ob=ba if native_side=='BUY' else bb
    if native_side=='BUY':dist=(sb-native_price)/GRID if math.isfinite(sb) else math.nan;sps=sorted(bids,reverse=True)[:3];ops=sorted(asks)[:3]
    else:dist=(native_price-sb)/GRID if math.isfinite(sb) else math.nan;sps=sorted(asks)[:3];ops=sorted(bids,reverse=True)[:3]
    b3=sum(float(bids[p]) for p in sorted(bids,reverse=True)[:3]);a3=sum(float(asks[p]) for p in sorted(asks)[:3]);den=b3+a3
    return {'distanceFromSameBestTicks':finite(dist,math.nan),'sameTopQty':float(same.get(k(sb),0.0)) if math.isfinite(sb) else 0.0,'oppositeTopQty':float(opp.get(k(ob),0.0)) if math.isfinite(ob) else 0.0,'sameTop3Qty':sum(float(same[p]) for p in sps),'oppositeTop3Qty':sum(float(opp[p]) for p in ops),'nativeSpreadTicks':(ba-bb)/GRID if math.isfinite(bb) and math.isfinite(ba) else math.nan,'nativeBookImbalance':(b3-a3)/den if den>EPS else 0.0,'sameBookLevels':float(len(same)),'oppositeBookLevels':float(len(opp))}
def outcome(row):
    return str(row.get('outcome') or '')
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--microstate',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=json.loads(Path(a.microstate).read_text(encoding='utf-8'))['rows'];by=defaultdict(list)
    for r in src:by[int(r['marketId'])].append(r)
    tmp=Path(tempfile.mkdtemp(prefix='q67transfer_'));out=[]
    try:
        with zipfile.ZipFile(a.bundle) as z:
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        for m,rs in sorted(by.items()):
            payload=json.loads(lzma.decompress((tmp/f'{m}.json.xz').read_bytes()).decode('utf-8'));ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])));uts=[int(u[1]) for u in ups];ocs=[float(u[2]) for u in ups]
            feed.ARCHIVE_DIR=tmp;events,_,_=feed.build_archive_events(m,trade_offset='mid')
            def oc_at(t):
                j=bisect.bisect_right(uts,int(t))-1;return ocs[j] if j>=0 else math.nan
            for r in rs:
                t=int(r['t']);accept=int(r['exchangeAcceptT']);side=str(r['side']);price=float(r['ownPrice']);native_side,native_price=ex.native_order(side,price);native_price=k(native_price);book={'bids':{},'asks':{}};q=None;q0=None;minlevel=None;tradeQty=0.0;tradeEvents=0;depthEvents=0;frontT=None;frontCause=None;frontBook=None
                for e in events:
                    ts=int(e['local_ts']//1_000_000);ev=int(e['ev']);px=k(e['px']);qty=float(e['qty'])
                    if ts>t:break
                    if (ev&int(ex.DEPTH_EVENT)) or (ev&int(ex.DEPTH_SNAPSHOT_EVENT)):apply_depth(book,ev,px,qty)
                    if q is None and ts>=accept:
                        same=book['bids'] if native_side=='BUY' else book['asks'];q=float(same.get(native_price,0.0));q0=q;minlevel=q
                        if q<=EPS:frontT=accept;frontCause='INITIAL_ZERO';frontBook=dict(bids=dict(book['bids']),asks=dict(book['asks']))
                    if q is None or ts<=accept:continue
                    if abs(px-native_price)>1e-9:continue
                    if (ev&int(ex.DEPTH_EVENT)) or (ev&int(ex.DEPTH_SNAPSHOT_EVENT)):
                        same=(native_side=='BUY' and (ev&int(ex.BUY_EVENT))) or (native_side=='SELL' and (ev&int(ex.SELL_EVENT)))
                        if same:
                            q=min(float(q),max(0.0,qty));minlevel=min(float(minlevel),max(0.0,qty));depthEvents+=1
                            if frontT is None and q<=EPS:frontT=ts;frontCause='DEPTH_ZERO';frontBook=dict(bids=dict(book['bids']),asks=dict(book['asks']))
                    elif ev&int(ex.TRADE_EVENT):
                        contra=(native_side=='BUY' and (ev&int(ex.SELL_EVENT))) or (native_side=='SELL' and (ev&int(ex.BUY_EVENT)))
                        if contra:
                            q=float(q)-qty;tradeQty+=qty;tradeEvents+=1
                            if frontT is None and q<=EPS:frontT=ts;frontCause='TRADE_ZERO';frontBook=dict(bids=dict(book['bids']),asks=dict(book['asks']))
                if q is None:
                    same=book['bids'] if native_side=='BUY' else book['asks'];q=float(same.get(native_price,0.0));q0=q;minlevel=q
                if frontT is None and q<=EPS:frontT=t;frontCause='UNKNOWN_ZERO';frontBook=dict(bids=dict(book['bids']),asks=dict(book['asks']))
                if frontBook is None:frontBook=dict(bids=dict(book['bids']),asks=dict(book['asks']))
                common={'marketId':m,'key':str(r['key']),'side':side,'role':'SATELLITE_REPAIR','price':price,'qty':float(r['orderQty']),'frontStateCause':frontCause,'frontCauseComparable':int(frontCause in ('INITIAL_ZERO','DEPTH_ZERO')),'exchangeAcceptT':accept,'frontStateT':frontT,'decisionT':t,'timeToFrontMs':int(frontT-accept) if frontT is not None else None,'queueInitial':float(q0 or 0.0),'minOwnLevelSinceAccept':float(minlevel or 0.0),'samePriceContraTradeQtyBeforeFront':float(tradeQty if frontT is None else 0.0),'samePriceContraTradeEventsBeforeFront':int(tradeEvents if frontT is None else 0),'samePriceDepthEventsBeforeFront':int(depthEvents),'orderCountAtFront':oc_at(frontT if frontT is not None else t),'orderCountDeltaToFront':oc_at(frontT if frontT is not None else t)-oc_at(accept),'fillFirst':int(outcome(r)=='FILLED_TERMINAL'),'priorityLoss':int(outcome(r)=='PRIORITY_LOSS_CANCEL'),'outcome':outcome(r)}
                # Recompute path-to-front precisely for trade/depth counters by second pass ending at frontT.
                if frontT is not None:
                    tq=0.0;te=0;de=0
                    for e in events:
                        ts=int(e['local_ts']//1_000_000)
                        if ts<=accept:continue
                        if ts>frontT:break
                        if abs(k(e['px'])-native_price)>1e-9:continue
                        ev=int(e['ev']);qty=float(e['qty'])
                        if (ev&int(ex.DEPTH_EVENT)) or (ev&int(ex.DEPTH_SNAPSHOT_EVENT)):
                            same=(native_side=='BUY' and (ev&int(ex.BUY_EVENT))) or (native_side=='SELL' and (ev&int(ex.SELL_EVENT)))
                            if same:de+=1
                        elif ev&int(ex.TRADE_EVENT):
                            contra=(native_side=='BUY' and (ev&int(ex.SELL_EVENT))) or (native_side=='SELL' and (ev&int(ex.BUY_EVENT)))
                            if contra:tq+=qty;te+=1
                    common['samePriceContraTradeQtyBeforeFront']=tq;common['samePriceContraTradeEventsBeforeFront']=te;common['samePriceDepthEventsBeforeFront']=de
                fview=dict(common);fview['view']='FRONT_SNAPSHOT';fview.update(book_features(frontBook,native_side,native_price))
                dview=dict(common);dview['view']='DECISION_SNAPSHOT';dview.update(book_features(book,native_side,native_price));dview['orderCountAtFront']=oc_at(t);dview['orderCountDeltaToFront']=oc_at(t)-oc_at(accept)
                out.extend([fview,dview])
        cnt=Counter((x['view'],x['frontStateCause'],x['fillFirst'],x['priorityLoss']) for x in out)
        rep={'version':'LANE_G_QUEUE67_FRONT_TRANSFER_FEATURES_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':out,'coverage':{'decisionRows':len(src),'views':len(out),'markets':len(by),'comparableDecisionRows':sum(1 for x in out if x['view']=='DECISION_SNAPSHOT' and x['frontCauseComparable'])},'causeOutcomeCounts':{str(k):v for k,v in cnt.items()},'boundary':['clean exact-T 67 only','features strict-past through exact reanchor receipt','front snapshot is diagnostic memory; decision snapshot primary runtime view','no future window/no terminal PnL/winner/Target input/no 8781']}
        Path(a.output).write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'coverage':rep['coverage'],'causeOutcomeCounts':rep['causeOutcomeCounts']},indent=2,ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
