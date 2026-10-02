from __future__ import annotations
import argparse,json,lzma,math,tempfile,zipfile,shutil,sys,bisect
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9

def k(p): return round(float(p),10)

def apply(book,u):
    if int(u[3]):
        book['bids']={float(a):float(b) for a,b in (u[4] or {}).items()};book['asks']={float(a):float(b) for a,b in (u[5] or {}).items()};return
    for side in ('bids','asks'):
        for r in (u[6] or {}).get(side,[]) or []:
            p=float(r[0]);after=float(r[2])
            if after<=EPS:book[side].pop(p,None)
            else:book[side][p]=after

def outcome(row):
    ev=row.get('events') or []
    pl=next((e for e in ev if e.get('event')=='EXACT_T_PRIORITY_LOSS_CANCEL'),None)
    if pl:return 'PRIORITY_LOSS_CANCEL',int(pl['t'])-int(row['baselineCancelT'])
    te=next((e for e in ev if e.get('event')=='EXACT_T_MANAGED_TERMINAL'),None)
    if te:
        return ('FILLED_TERMINAL' if float(te.get('cum') or 0)>EPS else 'ZERO_FILL_TERMINAL'),int(te['t'])-int(row['baselineCancelT'])
    return 'UNKNOWN',None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--clean',required=True);ap.add_argument('--lifecycle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    clean=json.loads(Path(a.clean).read_text(encoding='utf-8'))['rows']
    lc=json.loads(Path(a.lifecycle).read_text(encoding='utf-8'))['rows']
    lmap={(int(r['marketId']),int(r['t']),str(r['key'])):r for r in lc}
    by={}
    for r in clean:by.setdefault(int(r['marketId']),[]).append(r)
    tmp=Path(tempfile.mkdtemp(prefix='qmicro_'));outrows=[]
    try:
        with zipfile.ZipFile(a.bundle) as z:
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        for m,rs in sorted(by.items()):
            payload=json.loads(lzma.decompress((tmp/f'{m}.json.xz').read_bytes()).decode('utf-8'))
            ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
            ut=[int(u[1]) for u in ups]
            feed.ARCHIVE_DIR=tmp; events,_,_=feed.build_archive_events(m,trade_offset='mid')
            # zero feed-latency was prereg/audited for this cohort; retain explicit check.
            feed_latency_nonzero=int(((events['local_ts']-events['exch_ts'])!=0).sum())
            book={'bids':{},'asks':{}}; ui=0
            work=[]
            for r in rs:
                t=int(r['baselineCancelT']); f=lmap.get((m,t,str(r['targetKey'])))
                if f is None:raise RuntimeError(f'missing lifecycle {m} {t} {r["targetKey"]}')
                placed=t-int(f['ageAtDecisionMs']); accept=placed+250
                work.append((accept,t,r,f))
            for accept,t,r,f in sorted(work,key=lambda x:(x[0],x[1])):
                while ui<len(ups) and int(ups[ui][1])<=accept:
                    apply(book,ups[ui]);ui+=1
                side=str(f['side']); price=float(f['ownPrice']); native_side,native_price=ex.native_order(side,price); native_price=k(native_price)
                bside='bids' if native_side=='BUY' else 'asks'
                initial=float(book[bside].get(native_price,0.0))
                q=initial; current_level=initial; trade_qty=0.0; depth_updates=0; trade_events=0; min_level=initial
                lo=int(accept)*1_000_000; hi=int(t)*1_000_000
                for e in events:
                    ts=int(e['local_ts'])
                    if ts<=lo:continue
                    if ts>hi:break
                    if abs(float(e['px'])-native_price)>1e-9:continue
                    ev=int(e['ev']); qty=float(e['qty'])
                    if ev & int(ex.DEPTH_EVENT):
                        same=(native_side=='BUY' and (ev&int(ex.BUY_EVENT))) or (native_side=='SELL' and (ev&int(ex.SELL_EVENT)))
                        if same:
                            current_level=max(0.0,qty);q=min(q,current_level);depth_updates+=1;min_level=min(min_level,current_level)
                    if ev & int(ex.TRADE_EVENT):
                        contra=(native_side=='BUY' and (ev&int(ex.SELL_EVENT))) or (native_side=='SELL' and (ev&int(ex.BUY_EVENT)))
                        if contra:
                            q-=qty;trade_qty+=qty;trade_events+=1
                oi=bisect.bisect_right(ut,accept)-1;di=bisect.bisect_right(ut,t)-1
                oc_accept=float(ups[oi][2]) if oi>=0 else math.nan;oc_dec=float(ups[di][2]) if di>=0 else math.nan
                rem=float(f['remainingQtyAtDecision']); typ,lag=outcome(r)
                row={
                    'marketId':m,'t':t,'key':str(r['targetKey']),'side':side,'ownPrice':price,'nativeSide':native_side,'nativePrice':native_price,
                    'placedT':placed,'exchangeAcceptT':accept,'acceptToDecisionMs':t-accept,'feedLatencyNonzeroEvents':feed_latency_nonzero,
                    'orderQty':float(f['orderQty']),'remainingQty':rem,'ageAtDecisionMs':float(f['ageAtDecisionMs']),
                    'queueInitial':initial,'queueFrontRemaining':q,'queueFrontNonnegative':max(0.0,q),'queueConsumed':initial-q,
                    'queueProgressFrac':(initial-max(0.0,q))/initial if initial>EPS else (1.0 if q<=EPS else 0.0),
                    'queueInitialToOrder':initial/max(rem,EPS),'queueRemainingToOrder':max(0.0,q)/max(rem,EPS),'queueAtOrPastFront':int(q<=EPS),
                    'currentLevelQty':current_level,'minLevelQtySinceAccept':min_level,'samePriceTradeQtySinceAccept':trade_qty,
                    'samePriceTradeEventsSinceAccept':trade_events,'samePriceDepthUpdatesSinceAccept':depth_updates,
                    'orderCountAtAccept':oc_accept,'orderCountAtDecision':oc_dec,'orderCountDeltaSinceAccept':oc_dec-oc_accept,
                    'currentBestPrice':float(f['currentBestPrice']),'currentSecondPrice':float(f['currentSecondPrice']),'currentBestDepth':float(f['currentBestDepth']),'currentSecondDepth':float(f['currentSecondDepth']),
                    'currentGapTicks':float(f['currentGapTicks']),'sameSideLiveCountAtDecision':float(f['sameSideLiveCountAtDecision']),
                    'scopeDebtQtyAtDecision':float(f['scopeDebtQtyAtDecision']),'ownRepairQuotaRemaining':float(f['ownRepairQuotaRemaining']),
                    'unreservedDebtIfOwnReleased':float(f['unreservedDebtIfOwnReleased']),'physicalFloorAtDecision':float(f['physicalFloorAtDecision']),'physicalBestAtDecision':float(f['physicalBestAtDecision']),'physicalGapAtDecision':float(f['physicalGapAtDecision']),
                    'outcome':typ,'eventLagMs':lag,'filled':int(typ=='FILLED_TERMINAL'),'priorityLoss':int(typ=='PRIORITY_LOSS_CANCEL'),
                    'terminalPnlDelta':float(r['terminalDelta']['pnl'])
                }
                outrows.append(row)
        counts=Counter(r['outcome'] for r in outrows);neg=[r for r in outrows if r['queueFrontRemaining']<-1e-8]
        out={'version':'LANE_G_QUEUE67_EXECUTION_MICROSTATE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':outrows,
             'coverage':{'rows':len(outrows),'markets':len({r['marketId'] for r in outrows}),'outcomeCounts':dict(counts)},
             'invariants':{'allFeedLatencyZero':all(r['feedLatencyNonzeroEvents']==0 for r in outrows),'negativeQueueFrontRows':len(neg),'negativeQueueExamples':[{k:r[k] for k in ('marketId','t','key','queueInitial','queueFrontRemaining','samePriceTradeQtySinceAccept','outcome')} for r in neg[:10]]},
             'boundary':['exact clean (market,key,t) decisions only','all features strict-past through exact decision receipt','queue reconstruction matches HftBacktest RiskAdverseQueueModel semantics: initial depth at exchange acceptance; same-price contra trades subtract; same-side depth updates cap front queue','250ms entry latency frozen by R2.64 substrate','no future-window features','no winner/Target future/runtime authority/no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'coverage':out['coverage'],'invariants':out['invariants']},ensure_ascii=False,indent=2))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
