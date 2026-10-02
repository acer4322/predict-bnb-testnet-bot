from __future__ import annotations
import argparse,json,lzma,math,sys
from collections import Counter
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_execution_shift_audit_v0 as ex
from predict_bot.core import taker_fee

SRC=ROOT/'data/hft_forward_paper_v1/markets'; OUT=ROOT/'data/research/r4_v0/hourly'
LEADS=(1000,3000,5000); EPS=1e-9; FEE_BPS=200
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}


def choose_files(max_markets:int):
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True); seen=set(); out=[]
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception: continue
        mid=int(d.get('marketId') or 0); student=str(d.get('student') or '')
        if not mid or mid in seen or mid in FROZEN or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}): continue
        if not (ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz').exists(): continue
        seen.add(mid); out.append(d)
        if len(out)>=max_markets: break
    return out


def geom(s):
    up,down,cu,cd,fees=s; cost=cu+cd+fees; pu=up-cost; pd=down-cost; gross=up+down
    au=cu/up if up>EPS else 0.; ad=cd/down if down>EPS else 0.
    return {'up':up,'down':down,'cu':cu,'cd':cd,'fees':fees,'cost':cost,'floor':min(pu,pd),'upside':max(pu,pd),
            'paired':min(up,down),'absNet':abs(up-down),'gross':gross,'coverage':2*min(up,down)/gross if gross>EPS else 0.,
            'edge':1-(au+ad) if up>EPS and down>EPS else 0.}


def apply(s,side,px,q,fee=False):
    up,down,cu,cd,fees=s
    if side=='UP': up+=q; cu+=q*px
    else: down+=q; cd+=q*px
    if fee: fees+=float(taker_fee(q,px,FEE_BPS))
    return (up,down,cu,cd,fees)


def weak_side(s):
    g=geom(s)
    if g['gross']<=EPS or abs(g['up']-g['down'])<=EPS:return None
    return 'UP' if g['up']<g['down'] else 'DOWN'


def relation(s,side):
    w=weak_side(s)
    if w is None:return 'FLAT'
    return 'WEAK' if side==w else 'SURPLUS'


def mpq_base_break(s,side,px,q):
    if q<=EPS:return False
    pre=geom(s); post=geom(apply(s,side,px,q,False)); reserve=max(0.,pre['floor']-post['floor'])
    return bool(pre['floor']>0 and reserve>EPS and post['floor']<=0 and post['edge']<0)


def durable(trace):
    for i,x in enumerate(trace):
        if x['floor']<0: continue
        w=[y for y in trace[i:] if y['t']<=x['t']+15000]
        if w and min(y['floor'] for y in w)>=-5.0 and w[-1]['floor']>=0:return x
    return None


def path_stats(trace):
    if not trace:return {'positiveDurationSec':0.0,'maxFloorDrawdown':0.0,'minFloor':None}
    tr=sorted(trace,key=lambda x:x['t']); pos=0.; peak=-1e99; dd=0.
    for i,x in enumerate(tr):
        peak=max(peak,x['floor']); dd=max(dd,peak-x['floor'])
        if i+1<len(tr) and x['floor']>0:pos+=max(0,tr[i+1]['t']-x['t'])/1000.
    return {'positiveDurationSec':pos,'maxFloorDrawdown':dd,'minFloor':min(x['floor'] for x in tr)}


def prep(d,meta,lead):
    canc={str(x.get('orderId')):int(x.get('atMs') or 0) for x in (d.get('cancelEvents') or []) if x.get('orderId')}
    dec=sorted([x for x in (d.get('decisionRows') or []) if int(x.get('decisionMs') or 0)>0],key=lambda x:int(x['decisionMs']))
    orders=[]
    for j,(oid,o0) in enumerate(sorted((d.get('orderMeta') or {}).items(),key=lambda kv:int(kv[1].get('placedAtMs') or 0)),start=1):
        o=dict(o0); need=int(o.get('placedAtMs') or 0); side=str(o.get('side') or '').upper(); qty=float(o.get('shares') or 0); px=float(o.get('price') or 0)
        if not need or side not in {'UP','DOWN'} or qty<=0 or not (0<=px<=1.05):continue
        start=max(int(meta['firstReceivedMs']),need-lead)
        prior=[r for r in dec if int(r['decisionMs'])<=start]
        r0=prior[-1] if prior else None
        m=(r0 or {}).get('models') or {}; pu=float(m.get('pMakerUp') or 0.0); pd=float(m.get('pMakerDown') or 0.0)
        model_supported=bool(r0 is not None and (pu>=pd if side=='UP' else pd>=pu))
        orders.append({'logical':j,'oid':oid,'side':side,'px':px,'qty':qty,'need':need,'start':start,'cancel':canc.get(oid),'modelSupported':model_supported})
    takers=[]
    for x in d.get('takerEvents') or []:
        q=float(x.get('shares') or x.get('filledShares') or x.get('deltaShares') or x.get('qty') or 0); px=float(x.get('price') or x.get('fillPrice') or x.get('avgPrice') or 0); side=str(x.get('side') or '').upper(); t=int(x.get('observedAtMs') or x.get('atMs') or x.get('fillMs') or 0)
        if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'} and t:takers.append({'t':t,'side':side,'px':px,'q':q})
    return orders,takers,dec


def simulate(d,lead,policy):
    mid=int(d['marketId']); events,times,meta=tape.build_archive_events(mid,trade_offset='mid'); orders,takers,dec=prep(d,meta,lead)
    bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
    state=(0.,0.,0.,0.,0.); trace=[]; next_id=1; hids={}; last_exec={}; logical_filled={o['logical']:0. for o in orders}
    cancel_requested=set(); pruned_logicals=set(); flat_wait={}; prune_reasons=Counter(); prearm_relations=Counter(); prearmed=pruned=fallback=0; prearmed_shares=0.; total_fill=early=early_surplus=early_damage=0.
    actions={}
    def add(t,prio,kind,obj):actions.setdefault(int(t),[]).append((prio,kind,obj))
    for o in orders:
        add(o['start'],40,'PREARM',o); add(o['need'],50,'NEED',o); add(o['need']+273,60,'FALLBACK_CHECK',o)
        if o['cancel'] is not None:add(o['cancel'],70,'CANCEL_ORIG',o)
    for r in dec:
        t=int(r['decisionMs'])
        if int(meta['firstReceivedMs'])<=t<=int(meta['lastReceivedMs']):add(t,30,'RESP_CHECK',r)
    for x in takers:add(x['t'],20,'TAKER',x)
    timeline=sorted(set([t for t in times if int(meta['firstReceivedMs'])<=t<=int(meta['lastReceivedMs'])]+list(actions)))

    def live_rows(lid=None,prearm_only=False):
        rows=[]
        for hid,z in hids.items():
            if lid is not None and z['o']['logical']!=lid:continue
            if prearm_only and z['kind']!='PREARM':continue
            s=ex.order_snapshot(bt,hid); st=str(s.get('status') or '')
            rem=float(s.get('leavesQty') or 0.) if st in {'NEW','PARTIALLY_FILLED'} else 0.
            if rem>EPS:rows.append((hid,z,s,rem))
        return rows

    def request_cancel(hid,reason):
        nonlocal pruned
        if hid in cancel_requested:return
        cur=bt.orders(0).get(hid)
        if cur is not None and int(cur.status) in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
            bt.cancel(0,hid,False);cancel_requested.add(hid);pruned+=1;prune_reasons[reason]+=1
            if reason in {'LOST_WEAK_SIDE_RESPONSIBILITY','MPQ_BASE_BREAK','DUPLICATE_OR_EXCESS_OWNER'}:
                pruned_logicals.add(hids[hid]['o']['logical'])

    def harvest(t):
        nonlocal state,total_fill,early,early_surplus,early_damage
        for hid in sorted(hids):
            z=hids[hid];o=z['o'];s=ex.order_snapshot(bt,hid);cur=float(s.get('cumExecQty') or 0.);prev=last_exec.get(hid,0.);dq=max(0.,cur-prev)
            if dq>EPS:
                pre=geom(state);rel=relation(state,o['side']); state=apply(state,o['side'],o['px'],dq,False);post=geom(state)
                total_fill+=dq;logical_filled[o['logical']]+=dq
                if t<o['need']:
                    early+=dq
                    if rel=='SURPLUS':early_surplus+=dq
                    early_damage+=max(0.,pre['floor']-post['floor'])
                trace.append({'t':t,'role':'MAKER','side':o['side'],'q':dq,'px':o['px'],'logical':o['logical'],'preNeed':t<o['need'],'relationBefore':rel,**post})
            last_exec[hid]=cur

    def submit(o,qty,kind):
        nonlocal next_id,prearmed,fallback,prearmed_shares
        if qty<=EPS:return None
        hid=next_id;next_id+=1;ex.submit_native(bt,hid,o['side'],o['px'],qty);hids[hid]={'o':o,'submittedQty':qty,'kind':kind};last_exec[hid]=0.
        if kind=='PREARM':prearmed+=1;prearmed_shares+=qty
        if kind=='FALLBACK':fallback+=1
        return hid

    def owner_prune():
        # Exact responsibility ownership: preserve oldest queue owners and cancel newest excess pending weak-side quantity.
        w=weak_side(state)
        if w is None:return
        gap=geom(state)['absNet']; live=[x for x in live_rows(prearm_only=True) if x[1]['o']['side']==w and x[1]['o']['need']>cur_t]
        live.sort(key=lambda x:(x[1]['o']['start'],x[0]))
        total=sum(x[3] for x in live)
        if total<=gap+EPS:return
        for hid,z,s,rem in reversed(live):
            if total<=gap+EPS:break
            request_cancel(hid,'DUPLICATE_OR_EXCESS_OWNER');total-=rem

    try:
        for cur_t in timeline:
            if int(bt.current_timestamp)<=cur_t*1_000_000:
                if not ex.advance_to(bt,cur_t):break
            harvest(cur_t)
            aa=sorted(actions.get(cur_t,[]),key=lambda x:x[0])
            # First apply frozen exogenous Taker path.
            for _,kind,obj in aa:
                if kind!='TAKER':continue
                pre=geom(state);state=apply(state,obj['side'],obj['px'],obj['q'],True);post=geom(state);trace.append({'t':cur_t,'role':'TAKER','side':obj['side'],'q':obj['q'],'px':obj['px'],'preNeed':False,'relationBefore':relation((pre['up'],pre['down'],pre['cu'],pre['cd'],pre['fees']),obj['side']),**post})
            # Responsibility check is closed-loop on realized counterfactual inventory, but only acts on already-resting pre-need orders.
            if any(kind=='RESP_CHECK' for _,kind,_ in aa) and policy.startswith('RESP'):
                for hid,z,s,rem in live_rows(prearm_only=True):
                    o=z['o']
                    if cur_t>=o['need'] or hid in cancel_requested:continue
                    rel=relation(state,o['side'])
                    if rel=='SURPLUS':request_cancel(hid,'LOST_WEAK_SIDE_RESPONSIBILITY');continue
                    if mpq_base_break(state,o['side'],o['px'],rem):request_cancel(hid,'MPQ_BASE_BREAK');continue
                if policy in {'RESP_OWNER','RESP_GAP','RESP_PAIRED_FLAT'}:owner_prune()
            for _,kind,o in aa:
                if kind in {'TAKER','RESP_CHECK'}:continue
                lid=o['logical']
                if kind=='PREARM':
                    if policy=='REACTIVE' or not o['modelSupported']:continue
                    if policy=='KEEP':submit(o,o['qty'],'PREARM');continue
                    rel=relation(state,o['side'])
                    if policy in {'RESP_WEAK','RESP_GAP'} and rel!='WEAK':continue
                    if rel=='SURPLUS':continue
                    if policy=='RESP_PAIRED_FLAT' and rel=='FLAT':
                        opp='DOWN' if o['side']=='UP' else 'UP'
                        mates=[x for x in flat_wait.values() if x['side']==opp and x['need']>cur_t]
                        if not mates:
                            flat_wait[lid]=o
                            continue
                        mate=sorted(mates,key=lambda x:(x['need'],x['logical']))[0]
                        flat_wait.pop(mate['logical'],None)
                        qpair=min(o['qty'],mate['qty'])
                        if qpair<=EPS:continue
                        prearm_relations['FLAT_PAIR']+=2
                        submit(mate,qpair,'PREARM');submit(o,qpair,'PREARM')
                        continue
                    if mpq_base_break(state,o['side'],o['px'],o['qty']):continue
                    qty=o['qty']
                    if policy in {'RESP_OWNER','RESP_GAP','RESP_PAIRED_FLAT'}:
                        w=weak_side(state)
                        if w is not None and o['side']==w:
                            pending=sum(x[3] for x in live_rows(prearm_only=True) if x[1]['o']['side']==w and x[1]['o']['need']>cur_t)
                            available=max(0.,geom(state)['absNet']-pending)
                            if policy=='RESP_OWNER' and available<=EPS:continue
                            if policy in {'RESP_GAP','RESP_PAIRED_FLAT'}:qty=min(qty,available)
                    if qty<=EPS:continue
                    prearm_relations[rel]+=1
                    submit(o,qty,'PREARM')
                elif kind=='NEED':
                    flat_wait.pop(lid,None)
                    # Preserve the frozen desired order: existing live prearm remainder counts toward the desired qty.
                    committed=sum(x[3] for x in live_rows(lid))
                    rem=max(0.,o['qty']-logical_filled[lid]-committed)
                    submit(o,rem,'MAIN')
                elif kind=='FALLBACK_CHECK':
                    # Only responsibility-pruned prearms may fallback after cancel response latency.
                    if lid not in pruned_logicals:continue
                    committed=sum(x[3] for x in live_rows(lid))
                    rem=max(0.,o['qty']-logical_filled[lid]-committed)
                    if rem>EPS:submit(o,rem,'FALLBACK')
                elif kind=='CANCEL_ORIG':
                    for hid,z,s,rem in live_rows(lid):request_cancel(hid,'ORIGINAL_LIFECYCLE_CANCEL')
        if int(bt.current_timestamp)<int(meta['lastReceivedMs'])*1_000_000:
            ex.advance_to(bt,int(meta['lastReceivedMs']));harvest(int(meta['lastReceivedMs']))
    finally:bt.close()
    tr=sorted(trace,key=lambda x:x['t']);fs=next((x for x in tr if x['floor']>=0),None);dur=durable(tr);final=geom(state);ps=path_stats(tr)
    return {'marketId':mid,'policy':policy,'leadMs':lead,'orders':len(orders),'prearmedOrders':prearmed,'prearmedShares':prearmed_shares,'prearmRelations':dict(prearm_relations),'prunedOrders':pruned,'fallbackOrders':fallback,
            'pruneReasons':dict(prune_reasons),'makerFilledShares':total_fill,'earlyMakerFillShares':early,'earlySurplusFillShares':early_surplus,
            'earlyFloorDamage':early_damage,'earlyMakerFillRateOfMakerFills':early/total_fill if total_fill>0 else 0.,'everSafe':fs is not None,'durableBase':dur is not None,
            'firstSafeMs':None if fs is None else fs['t'],'firstDurableMs':None if dur is None else dur['t'],'final':final,**ps}


def md(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return median(xs) if xs else None

def q(xs,p):
    xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not xs:return None
    pos=(len(xs)-1)*p;lo=int(pos);hi=min(lo+1,len(xs)-1);w=pos-lo;return xs[lo]*(1-w)+xs[hi]*w


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=25);a=ap.parse_args();ds=choose_files(a.max_markets);rows=[];errors=[]
    configs=[(0,'REACTIVE')]+[(l,p) for l in LEADS for p in ('KEEP','RESP_RELATION','RESP_OWNER')]
    for d in ds:
        mid=int(d['marketId'])
        for lead,pol in configs:
            try:rows.append(simulate(d,lead,pol))
            except Exception as e:errors.append({'marketId':mid,'leadMs':lead,'policy':pol,'error':f'{type(e).__name__}: {e}'})
        print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
    agg={}
    for lead,pol in configs:
        rr=[r for r in rows if r['leadMs']==lead and r['policy']==pol];key=f'{pol}_{lead}';fills=sum(r['makerFilledShares'] for r in rr);early=sum(r['earlyMakerFillShares'] for r in rr)
        reasons=Counter();[reasons.update(r['pruneReasons']) for r in rr]
        agg[key]={'markets':len(rr),'makerFilledShares':fills,'earlyMakerFillShares':early,'earlyFillShareOfMakerFills':early/fills if fills>0 else 0.,
                  'earlySurplusFillShares':sum(r['earlySurplusFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),
                  'prearmedOrders':sum(r['prearmedOrders'] for r in rr),'prunedOrders':sum(r['prunedOrders'] for r in rr),'fallbackOrders':sum(r['fallbackOrders'] for r in rr),'pruneReasons':dict(reasons),
                  'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr) if rr else None,
                  'medianFinalFloor':md([r['final']['floor'] for r in rr]),'p10FinalFloor':q([r['final']['floor'] for r in rr],.10),'medianFinalAbsNet':md([r['final']['absNet'] for r in rr]),
                  'p90FinalAbsNet':q([r['final']['absNet'] for r in rr],.90),'medianPositiveDurationSec':md([r['positiveDurationSec'] for r in rr]),'medianMaxFloorDrawdown':md([r['maxFloorDrawdown'] for r in rr])}
    report={'version':'R4_PREPOSITION_RESPONSIBILITY_PRUNE_V2','researchOnly':True,
            'hypothesis':'Maker queue optionality should persist only while the resting remainder owns strict-past Formation responsibility, rather than while same-side pMaker remains top-ranked.',
            'policies':{'KEEP':'same pMaker-supported prearm eligibility as V1; no responsibility pruning',
                        'RESP_RELATION':'prearm only when not current surplus; while pre-need, pull remainder if side becomes surplus or frozen MPQ BASE_BREAK semantics fire',
                        'RESP_OWNER':'RESP_RELATION plus exact weak-side pending-quantity ownership: preserve older queue owners and pull newest duplicate/excess owners when aggregate pending exceeds current realized weak-side gap'},
            'guards':{'dreamFill':False,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','entryLatencyMs':1092,'responseLatencyMs':273,'takerPathFrozenExogenous':True,
                      'winnerUsed':False,'liveTradingChanges':False,'noThresholdSweep':True,'floorBoundary':0.0,'prearmModelSupport':'latest strict-past pMaker at prearm time must support same side',
                      'responsibilityState':'counterfactual realized inventory only; no future fill/settlement input','futureFrozenOrderPriceTiming':'used only as execution counterfactual upper-bound anchor; not runtime promotion evidence'},
            'aggregate':agg,'errors':errors,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True);path=OUT/'r4_preposition_responsibility_prune_v2.json';path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'aggregate':agg,'errors':errors[:5]},ensure_ascii=False))
if __name__=='__main__':main()
