from __future__ import annotations
import argparse,json,lzma,math,sys
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_execution_shift_audit_v0 as ex
from predict_bot.core import taker_fee
SRC=ROOT/'data/hft_forward_paper_v1/markets'; OUT=ROOT/'data/research/r4_v0/hourly'
LEADS=(500,1000,3000,5000); EPS=1e-9; FEE_BPS=200

def choose_files(max_markets:int):
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True); seen=set(); out=[]
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception: continue
        mid=int(d.get('marketId') or 0); student=str(d.get('student') or '')
        if not mid or mid in seen or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}): continue
        if not (ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz').exists(): continue
        seen.add(mid); out.append(d)
        if len(out)>=max_markets: break
    return out

def geom(s):
    up,down,cost,fees=s; pu=up-cost-fees; pd=down-cost-fees
    return {'up':up,'down':down,'cost':cost,'fees':fees,'floor':min(pu,pd),'upside':max(pu,pd),'paired':min(up,down),'absNet':abs(up-down)}

def apply(s,side,px,q,fee=False):
    up,down,cost,fees=s
    if side=='UP': up+=q
    else: down+=q
    cost+=q*px
    if fee: fees+=float(taker_fee(q,px,FEE_BPS))
    return (up,down,cost,fees)

def durable(trace):
    for i,x in enumerate(trace):
        if x['floor']<0: continue
        w=[y for y in trace[i:] if y['t']<=x['t']+15000]
        if w and min(y['floor'] for y in w)>=-5.0 and w[-1]['floor']>=0: return x
    return None

def prep(d,meta,lead,policy):
    canc={str(x.get('orderId')):int(x.get('atMs') or 0) for x in (d.get('cancelEvents') or []) if x.get('orderId')}
    dec=sorted([x for x in (d.get('decisionRows') or []) if int(x.get('decisionMs') or 0)>0],key=lambda x:int(x['decisionMs']))
    orders=[]
    for j,(oid,o0) in enumerate(sorted((d.get('orderMeta') or {}).items(),key=lambda kv:int(kv[1].get('placedAtMs') or 0)),start=1):
        o=dict(o0); need=int(o.get('placedAtMs') or 0); side=str(o.get('side') or '').upper(); qty=float(o.get('shares') or 0); px=float(o.get('price') or 0)
        if not need or side not in {'UP','DOWN'} or qty<=0: continue
        start=max(int(meta['firstReceivedMs']),need-lead) if policy!='REACTIVE' else need
        eligible=True
        prune_t=None
        if policy!='REACTIVE':
            prior=[r for r in dec if int(r['decisionMs'])<=start]
            r0=prior[-1] if prior else None
            def probs(r):
                m=(r or {}).get('models') or {}; return float(m.get('pMakerUp') or 0.0),float(m.get('pMakerDown') or 0.0)
            if r0 is None: eligible=False
            else:
                pu,pd=probs(r0); eligible=(pu>=pd if side=='UP' else pd>=pu)
            if eligible and policy=='SUPPORTED_PRUNE':
                for r in dec:
                    t=int(r['decisionMs'])
                    if t<=start or t>=need: continue
                    pu,pd=probs(r); supported=(pu>=pd if side=='UP' else pd>=pu)
                    if not supported: prune_t=t; break
        if policy!='REACTIVE' and not eligible: start=need
        orders.append({'logical':j,'oid':oid,'side':side,'px':px,'qty':qty,'need':need,'start':start,'eligible':eligible,'prune':prune_t,'cancel':canc.get(oid)})
    takers=[]
    for x in d.get('takerEvents') or []:
        q=float(x.get('shares') or x.get('filledShares') or x.get('deltaShares') or x.get('qty') or 0); px=float(x.get('price') or x.get('fillPrice') or x.get('avgPrice') or 0); side=str(x.get('side') or '').upper(); t=int(x.get('observedAtMs') or x.get('atMs') or x.get('fillMs') or 0)
        if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'} and t: takers.append({'t':t,'side':side,'px':px,'q':q})
    return orders,takers

def simulate(d,lead,policy):
    mid=int(d['marketId']); events,times,meta=tape.build_archive_events(mid,trade_offset='mid'); orders,takers=prep(d,meta,lead,policy)
    bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
    state=(0.,0.,0.,0.); trace=[]; early=0.; prearmed=0; pruned=0; fallback=0; hids={}; last_exec={}; total_fill=0.
    actions={}
    def add(t,kind,obj): actions.setdefault(int(t),[]).append((kind,obj))
    for o in orders:
        add(o['start'],'SUBMIT_PRE' if o['start']<o['need'] else 'SUBMIT_MAIN',o)
        if o['start']<o['need']: prearmed+=1
        if o['prune'] is not None: add(o['prune'],'PRUNE',o)
        if o['cancel'] is not None: add(o['cancel'],'CANCEL_ORIG',o)
        if o['start']<o['need'] and o['prune'] is not None: add(o['need'],'FALLBACK',o)
    for x in takers: add(x['t'],'TAKER',x)
    timeline=sorted(set([t for t in times if int(meta['firstReceivedMs'])<=t<=int(meta['lastReceivedMs'])]+list(actions)))
    next_id=1
    logical_filled={o['logical']:0.0 for o in orders}; logical_active={o['logical']:None for o in orders}; logical_pruned={o['logical']:False for o in orders}
    def harvest(t):
        nonlocal state,total_fill,early
        for hid,(o,qty_sub) in list(hids.items()):
            s=ex.order_snapshot(bt,hid); cur=float(s.get('cumExecQty') or 0.0); prev=last_exec.get(hid,0.0); dq=max(0.0,cur-prev)
            if dq>EPS:
                state=apply(state,o['side'],o['px'],dq,False); total_fill+=dq; logical_filled[o['logical']]+=dq
                if t<o['need']: early+=dq
                g=geom(state); trace.append({'t':t,'role':'MAKER','side':o['side'],'q':dq,'px':o['px'],**g})
            last_exec[hid]=cur
    try:
        for t in timeline:
            if int(bt.current_timestamp)<=t*1_000_000:
                if not ex.advance_to(bt,t): break
            harvest(t)
            for kind,obj in actions.get(t,[]):
                if kind=='TAKER':
                    state=apply(state,obj['side'],obj['px'],obj['q'],True); g=geom(state); trace.append({'t':t,'role':'TAKER','side':obj['side'],'q':obj['q'],'px':obj['px'],**g}); continue
                o=obj; lid=o['logical']; hid=logical_active.get(lid)
                if kind in {'PRUNE','CANCEL_ORIG'}:
                    if hid is not None:
                        cur=bt.orders(0).get(hid)
                        if cur is not None and int(cur.status) in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable): bt.cancel(0,hid,False)
                    if kind=='PRUNE': logical_pruned[lid]=True; pruned+=1
                    continue
                if kind=='FALLBACK' and not logical_pruned[lid]: continue
                rem=max(0.0,o['qty']-logical_filled[lid])
                if rem<=EPS: continue
                hid=next_id; next_id+=1; ex.submit_native(bt,hid,o['side'],o['px'],rem); hids[hid]=(o,rem); last_exec[hid]=0.; logical_active[lid]=hid
                if kind=='FALLBACK': fallback+=1
        if int(bt.current_timestamp)<int(meta['lastReceivedMs'])*1_000_000:
            ex.advance_to(bt,int(meta['lastReceivedMs'])); harvest(int(meta['lastReceivedMs']))
    finally: bt.close()
    tr=sorted(trace,key=lambda x:x['t']); first_safe=next((x for x in tr if x['floor']>=0),None); dur=durable(tr); final=geom(state)
    return {'marketId':mid,'policy':policy,'leadMs':lead,'orders':len(orders),'prearmedOrders':prearmed,'prunedOrders':pruned,'fallbackOrders':fallback,'makerFilledShares':total_fill,'earlyMakerFillShares':early,'earlyMakerFillRateOfMakerFills':early/total_fill if total_fill>0 else 0.0,'events':len(tr),'everSafe':first_safe is not None,'durableBase':dur is not None,'firstSafeMs':None if first_safe is None else first_safe['t'],'firstDurableMs':None if dur is None else dur['t'],'final':final}

def md(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]; return median(xs) if xs else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--max-markets',type=int,default=10); a=ap.parse_args(); ds=choose_files(a.max_markets); rows=[]; errors=[]
    configs=[(0,'REACTIVE')]+[(l,p) for l in LEADS for p in ('SUPPORTED_KEEP','SUPPORTED_PRUNE')]
    for d in ds:
        mid=int(d['marketId'])
        for lead,pol in configs:
            try: rows.append(simulate(d,lead,pol))
            except Exception as e: errors.append({'marketId':mid,'leadMs':lead,'policy':pol,'error':f'{type(e).__name__}: {e}'})
        print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
    agg={}
    for lead,pol in configs:
        rr=[r for r in rows if r['leadMs']==lead and r['policy']==pol]; key=f'{pol}_{lead}'
        agg[key]={'markets':len(rr),'makerFilledShares':sum(r['makerFilledShares'] for r in rr),'earlyMakerFillShares':sum(r['earlyMakerFillShares'] for r in rr),'earlyFillShareOfMakerFills':sum(r['earlyMakerFillShares'] for r in rr)/sum(r['makerFilledShares'] for r in rr) if sum(r['makerFilledShares'] for r in rr)>0 else 0.0,'prearmedOrders':sum(r['prearmedOrders'] for r in rr),'prunedOrders':sum(r['prunedOrders'] for r in rr),'fallbackOrders':sum(r['fallbackOrders'] for r in rr),'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr) if rr else None,'medianFinalFloor':md([r['final']['floor'] for r in rr]),'medianFinalAbsNet':md([r['final']['absNet'] for r in rr])}
    report={'version':'R4_PREPOSITION_PRUNE_MARKET_GEOMETRY_V1','researchOnly':True,'causalStatus':'SUPPORTED-SIDE PREARM EXECUTION COUNTERFACTUAL; future frozen order timing/price still used, so not runtime promotion evidence','guards':{'dreamFill':False,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','entryLatencyMs':1092,'responseLatencyMs':273,'takerPathFrozenExogenous':True,'winnerUsed':False,'liveTradingChanges':False,'prearmEligibility':'latest strict-past decision at prearm time must have same-side pMaker >= opposite pMaker','pruneRule':'cancel prearmed remainder at first strict-past decision before original need where side pMaker loses argmax; fallback remaining qty at original frozen need time'},'aggregate':agg,'errors':errors,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/'r4_preposition_prune_market_geometry_v1.json'; path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'aggregate':agg,'errors':errors[:5]},ensure_ascii=False))
if __name__=='__main__':main()
