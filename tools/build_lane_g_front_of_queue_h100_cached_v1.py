from __future__ import annotations
import argparse,bisect,json,math,os,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
try:
    from tools.build_lane_g_front_of_queue_h100_v1 import k,book_features,apply_depth,ex,EPS,ENTRY_MS
except ImportError:
    from build_lane_g_front_of_queue_h100_v1 import k,book_features,apply_depth,ex,EPS,ENTRY_MS
try:
    from tools.execution_tape_bundle_cache_v1 import ExecutionTapeBundleCache
except ImportError:
    from execution_tape_bundle_cache_v1 import ExecutionTapeBundleCache

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--structural-rows',required=True);ap.add_argument('--output',required=True);ap.add_argument('--cache-root');ap.add_argument('--event-cache-dir');a=ap.parse_args()
    rows=[json.loads(x) for x in Path(a.structural_rows).read_text(encoding='utf-8').splitlines() if x.strip()]
    by=defaultdict(list)
    for r in rows:
        if str(r.get('route'))=='PASSIVE' and int(r.get('firstEventObserved') or 0)==1: by[int(r['marketId'])].append(r)
    out=[];audit=[]
    bc=ExecutionTapeBundleCache(a.bundle,cache_root=a.cache_root) if a.cache_root else ExecutionTapeBundleCache(a.bundle)
    bc.ensure_tapes(by.keys()); ec=bc.event_cache(trade_offset='mid',event_cache_dir=a.event_cache_dir) if a.event_cache_dir else bc.event_cache(trade_offset='mid')
    for mi,(m,mrows) in enumerate(sorted(by.items()),1):
        uts_arr,ocs_arr=bc.update_clock(m);uts=uts_arr.tolist();ocs=ocs_arr.tolist()
        events,_,_=ec.load(m) if ec.has_valid(m) else ec.build(m)
        latency_nonzero=int(((events['local_ts']-events['exch_ts'])!=0).sum())
        specs=[]
        for r in mrows:
            submit=int(r['t']);accept=submit+ENTRY_MS;first_t=submit+int(r['firstEventLagMs']);native_side,native_price=ex.native_order(str(r['side']),float(r['price']))
            specs.append({'row':r,'submit':submit,'accept':accept,'first':first_t,'nativeSide':native_side,'price':k(native_price),'q':None,'initial':None,'minLevel':None,'tradeQty':0.0,'tradeEvents':0,'depthEvents':0,'cross':None})
        specs.sort(key=lambda x:x['accept']);ai=0;active=[];book={'bids':{},'asks':{}}
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
               'fillFirst':int(r['fillFirst']),'terminalFirst':int(r['terminalFirst']),'firstEventLagMs':int(r['firstEventLagMs']),'firstFillQty':float(r['firstFillQty']),'firstRepairPayQty':float(r['firstRepairPayQty']),'isRepairRole':int(float(r.get('isRepairRole') or 0)>0.5),'isExpandRole':int(float(r.get('isExpandRole') or 0)>0.5),**bf}
            s['cross']=x;out.append(x)
        for e in events:
            ts=int(e['local_ts']//1_000_000)
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
        for s in specs[ai:]:
            if s['accept']<s['first']:
                same=book['bids'] if s['nativeSide']=='BUY' else book['asks'];q=float(same.get(s['price'],0.0));s['q']=q;s['initial']=q;s['minLevel']=q
                if q<=EPS:capture(s,s['accept'],'INITIAL_ZERO')
        audit.append({'marketId':m,'inputRows':len(specs),'frontStates':sum(1 for s in specs if s['cross'] is not None),'feedLatencyNonzero':latency_nonzero})
        if mi%20==0:print(json.dumps({'progress':mi,'of':len(by),'frontStates':len(out)}),flush=True)
    cnt=Counter((x['fillFirst'],x['frontStateCause']) for x in out);markets=sorted({x['marketId'] for x in out})
    report={'version':'LANE_G_FRONT_OF_QUEUE_H100_CACHED_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':out,'audit':audit,
            'coverage':{'inputRows':len(rows),'frontStates':len(out),'markets':len(markets),'fillFirst':sum(x['fillFirst'] for x in out),'terminalFirst':sum(x['terminalFirst'] for x in out),'causeCounts':{f'{k0[0]}_{k0[1]}':v for k0,v in cnt.items()}},
            'invariants':{'allFeedLatencyZero':all(x['feedLatencyNonzero']==0 for x in audit),'allFrontBeforeFirstEvent':all(x['frontStateT']<x['firstStructuralT'] for x in out),'noTradeCausedFrontStates':all(x['frontStateCause']!='TRADE' for x in out),'noNegativeFront':all(x['queueFrontAtState']>=-EPS for x in out)},
            'dataCache':{'bundle':bc.summary(),'eventCache':dict(ec.stats)},
            'boundary':['H100 consumed PASSIVE carriers only','front state retained only if RiskAdverse queue reaches zero at acceptance or same-price depth update strictly before carrier first structural event','trade-caused zero crossing excluded to prevent label leakage','event-driven path since exchange acceptance; no fixed policy window','execution/book features only at front state; no stale portfolio interpolation','no fresh/no Target future/no winner input/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'coverage':report['coverage'],'invariants':report['invariants']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
