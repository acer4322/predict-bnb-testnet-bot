from __future__ import annotations
import argparse, json, math, sqlite3, sys, os
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == 'tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.generate_r4_r3_repair_counterfactual_teacher_v1 import candidate
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

EPS=1e-9
TERMINAL={'CANCELED','FILLED','REJECTED','EXPIRED'}
MODES=('SAME_PRICE_REFRESH','REPRICE_1T','REPRICE_3T')

def _compact(r):
    s=r['studentRollout']; p=s['finalPortfolio']
    return {'makerFillEvents':int(s['makerFillEvents']),'makerFilledShares':float(s['makerFilledShares']),
            'takerFills':int(s['takerFills']),'takerFilledShares':float(s.get('takerFilledShares') or 0.0),
            'makerCostUsdt':float(s.get('makerCostUsdt') or 0.0),'takerCostUsdt':float(s.get('takerCostUsdt') or 0.0),
            'takerFeesUsdt':float(s.get('takerFeesUsdt') or 0.0),'finalFloor':float(p.get('worst_case_floor') or 0.0),
            'finalAbsNet':float(p.get('combined_abs_net') or 0.0),'finalCoverage':float(p.get('combined_paired_coverage') or 0.0)}

def _path(c,a,key,o,now):
    snap=a.snap(key); bf=base.mod.outcome_book(a.book,None) or {}; bid=bf.get('up_bid') if o.side=='UP' else bf.get('down_bid')
    cum=float(snap.get('cumExecQty') or 0.0); leaves=snap.get('leavesQty')
    if leaves is None: leaves=max(0.0,float(o.shares))
    qoff=((float(bid)-float(o.price))/base.mod.GRID) if bid is not None else math.nan
    return {'key':[key[0],int(key[1])],'orderId':o.id,'side':o.side,'price':float(o.price),'orderAgeMs':float(now-int(o.placed_at_ms)),
            'hftStatus':str(snap.get('status') or 'NONE'),'cumExecQty':cum,'remainingQty':float(leaves),'quoteOffsetTicks':float(qoff),
            'initialDepth':float(o.initial_depth),'publicCumDepletion':float(o.cum_depletion),'occupiedBefore':bool(o.occupied_before)}

def run_refresh(mid:int, force_at:int, mode:str, disable_taker:bool=False):
    if mode not in MODES: raise ValueError(mode)
    orig_new=base.new_controller; action=[]
    def injected_new(a):
        c=orig_new(a); inv=c.inventory; orig_features=inv.features; native_cancel=c._cancel_order; native_add=c._add_order
        st={'armed':False,'pending':None,'done':False,'replacement':None,'blockedAdds':0}
        def cancel_guard(key,at_ms,reason):
            p=st.get('pending')
            if p and tuple(p['key'])==tuple(key) and not st['done']:
                # HFT cancel is sent by the outer replay wrapper. Keep controller ownership until venue terminal state
                # so fill-during-cancel remains visible to fill_wrap.
                return
            return native_cancel(key,at_ms,reason)
        def add_guard(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            pen=st.get('pending')
            if pen and not st['done'] and side==pen['side']:
                st['blockedAdds']+=1; return False
            return native_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        c._cancel_order=cancel_guard; c._add_order=add_guard
        def _replace(now:int):
            pen=st.get('pending')
            if not pen or st['done']: return
            key=tuple(pen['key']); snap=a.snap(key); status=str(snap.get('status') or 'NONE')
            if status not in TERMINAL and key in c.orders: return
            cum=float(snap.get('cumExecQty') or 0.0); delta=max(0.0,cum-float(pen['cumAtCancel'])); rem=max(0.0,float(pen['remainingAtCancel'])-delta)
            ev={'terminalAtMs':int(now),'terminalStatus':status,'cumAtTerminal':cum,'fillDuringCancelShares':delta,'genuineRemainingQty':rem,
                'replacementAfterTerminal':True,'duplicateOwnershipViolation':False}
            if rem<=EPS:
                ev['replacement']='NONE_FULLY_RESOLVED'; st['replacement']=ev; st['done']=True; return
            side=pen['side']; old_price=float(pen['oldPrice']); old_tick=int(pen['oldTick'])
            if mode=='SAME_PRICE_REFRESH':
                tick=old_tick; price=old_price
                opp='DOWN' if side=='UP' else 'UP'; opps=[o for o in c.orders.values() if o.side==opp]
                if (side,tick) in c.orders or (opps and price+max(o.price for o in opps)>base.mod.MAX_PAIR_PRICE_SUM+EPS):
                    ev['replacement']='BLOCKED_PRICE_OR_OCCUPIED'; st['replacement']=ev; st['done']=True; return
            else:
                off=1 if mode=='REPRICE_1T' else 3; qr=c._quote(side,off)
                if qr is None:
                    ev['replacement']='BLOCKED_NO_VALID_QUOTE'; st['replacement']=ev; st['done']=True; return
                tick,price=qr
                while (side,tick) in c.orders:
                    tick-=1
                    if tick<int(round(base.mod.MIN_PRICE/base.mod.GRID)):
                        ev['replacement']='BLOCKED_OCCUPIED'; st['replacement']=ev; st['done']=True; return
                    price=round(tick*base.mod.GRID,2)
            native_side='bids' if side=='UP' else 'asks'; native_price=round(price if side=='UP' else 1.0-price,10)
            initial=float(a.book.get(native_side,{}).get(native_price,0.0)); occupied=any(o.side==side for o in c.orders.values())
            c.sequence+=1; oid=f'R4_MAKER_REFRESH:{mid}:{mode}:{side}:{tick}:{now}:{c.sequence}'
            order=base.mod.PaperOrder(oid,side,int(tick),float(price),float(rem),int(now),int(now)*1_000_000,initial,native_side,native_price,bool(occupied))
            nkey=(side,int(tick)); c.orders[nkey]=order; num,rc=a.submit(nkey,order)
            if rc!=0:
                c.orders.pop(nkey,None); ev.update({'replacement':'SUBMIT_REJECT','submitRc':int(rc)}); st['replacement']=ev; st['done']=True; return
            c.placements.append({'at_ms':int(now),'side':side,'price':float(price),'reason':f'R4_{mode}','p':0.0,'occupied_before':int(occupied)})
            c.current_metrics['makerPlacements']+=1; c.run_metrics['makerPlacements']+=1
            bf=base.mod.outcome_book(a.book,None) or {}; bid=bf.get('up_bid') if side=='UP' else bf.get('down_bid')
            ev.update({'replacement':'PLACED','newOrderId':oid,'newPrice':float(price),'newQty':float(rem),'submitRc':int(rc),
                       'oldPrice':old_price,'oldQuoteOffsetTicks':pen['quoteOffsetTicks'],
                       'newQuoteOffsetTicks':((float(bid)-float(price))/base.mod.GRID) if bid is not None else math.nan})
            st['replacement']=ev; st['done']=True
        def features(now):
            _replace(int(now))
            if not st['armed'] and int(now)==int(force_at):
                net=float(inv.maker_up-inv.maker_down); repair='DOWN' if net>EPS else 'UP' if net<-EPS else None
                if repair:
                    xs=[]
                    for key,o in list(c.orders.items()):
                        if o.side!=repair: continue
                        ps=_path(c,a,key,o,int(now));
                        if ps['hftStatus'] in {'NEW','PARTIALLY_FILLED'} and ps['remainingQty']>EPS: xs.append((ps,key,o))
                    if xs:
                        # Refresh the least competitive existing weak-side carrier; tie-break by age.
                        ps,key,o=max(xs,key=lambda z:((z[0]['quoteOffsetTicks'] if math.isfinite(z[0]['quoteOffsetTicks']) else -1e9),z[0]['orderAgeMs']))
                        st['pending']={'key':[key[0],int(key[1])],'side':repair,'oldPrice':float(o.price),'oldTick':int(o.price_tick),
                                       'cumAtCancel':float(ps['cumExecQty']),'remainingAtCancel':float(ps['remainingQty']),
                                       'quoteOffsetTicks':float(ps['quoteOffsetTicks']),'orderAgeMs':float(ps['orderAgeMs']),'pathAtCancel':ps}
                        st['armed']=True
                        # At runtime this resolves to the replay cancel wrapper, which sends HFT cancel but our cancel_guard retains ownership.
                        c._cancel_order(key,int(now),f'R4_MAKER_{mode}')
                        action.append({'atMs':int(now),'mode':mode,'repairSide':repair,'makerNet':net,'pathAtCancel':ps,'state':st})
                    else:
                        st['armed']=True; st['done']=True; action.append({'atMs':int(now),'mode':mode,'repairSide':repair,'result':'NO_REPAIR_SIDE_CARRIER'})
                else:
                    st['armed']=True; st['done']=True; action.append({'atMs':int(now),'mode':mode,'result':'NO_MAKER_NET'})
            return orig_features(now)
        inv.features=features; c.r4_refresh_state=st; return c
    base.new_controller=injected_new
    try: rep=r3ctl.run_market(int(mid),True,disable_taker=bool(disable_taker))
    finally: base.new_controller=orig_new
    # Copy mutable state into a compact immutable result after rollout.
    if action and isinstance(action[0].get('state'),dict):
        s=action[0].pop('state'); action[0]['blockedSameSideAddsWhileCancelPending']=int(s.get('blockedAdds') or 0); action[0]['replacement']=s.get('replacement'); action[0]['completed']=bool(s.get('done'))
    rep['r4MakerRefreshV1']=action
    return rep

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ids',required=True); ap.add_argument('--data-root',required=True); ap.add_argument('--out',required=True); ap.add_argument('--disable-taker',action='store_true'); a=ap.parse_args()
    root=Path(a.data_root).resolve(); ids=[int(x) for x in a.ids.split(',') if x.strip()]
    base.STRATEGY_DB=root/'strategy_target_compare_v1.db'; base.mod.BOOK_DB=root/'wallet_maker_book_inference.db'; base.ex.BOOK_DB=root/'wallet_maker_book_inference.db'; base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'
    settle=root/'target_wallet_official_v1.db'; rows=[]
    for mid in ids:
        rec={'marketId':mid}
        try:
            con=sqlite3.connect(settle); rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone(); con.close(); winner=str(rr[0]) if rr else None
            b=r3ctl.run_market(mid,True,disable_taker=bool(a.disable_taker)); cand=candidate(b.get('decisionRows') or [],base.load_public_snapshots(mid)); bs=score(b,winner); rec.update({'winner':winner,'candidate':cand,'baselineScore':bs,'baselineCompact':_compact(b),'branches':{}})
            if cand:
                for mode in MODES:
                    x=run_refresh(mid,int(cand['decisionMs']),mode,disable_taker=bool(a.disable_taker)); xs=score(x,winner); act=x.get('r4MakerRefreshV1') or []
                    rec['branches'][mode]={'score':xs,'compact':_compact(x),'action':act[0] if act else None,
                        'conversion':('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if xs['pnlUsdt']>0 else 'LOSS'),
                        'deltaPnl':float(xs['pnlUsdt']-bs['pnlUsdt'])}
            rows.append(rec); print(json.dumps({'marketId':mid,'candidate':bool(cand),'branches':{k:v.get('conversion') for k,v in rec.get('branches',{}).items()}},allow_nan=True),flush=True)
        except Exception as e:
            rec['error']=f'{type(e).__name__}:{e}'; rows.append(rec); print(json.dumps({'marketId':mid,'error':rec['error']}),flush=True)
    summary={}
    for mode in MODES:
        z=[r['branches'][mode] for r in rows if not r.get('error') and mode in r.get('branches',{})]
        summary[mode]={'n':len(z),'conversions':dict(Counter(x['conversion'] for x in z)),
                       'meanDeltaPnl':sum(x['deltaPnl'] for x in z)/len(z) if z else None,
                       'takerFilledSharesDelta':sum(x['compact']['takerFilledShares']-next(r['baselineCompact']['takerFilledShares'] for r in rows if mode in r.get('branches',{}) and r['branches'][mode] is x) for x in z) if False else None}
    rep={'version':'R4_MAKER_LIFECYCLE_REFRESH_TEACHER_V1_NO_TAKER' if a.disable_taker else 'R4_MAKER_LIFECYCLE_REFRESH_TEACHER_V1','researchOnly':True,'actionAuthority':False,'modes':list(MODES),'summary':summary,'rows':rows,
         'disableTaker':bool(a.disable_taker),'guards':['strict-past exact seam only','same economic responsibility quantity only','cancel terminal before replacement','fill-during-cancel reconciled before replacement','no forced Taker','no live R3/R3.1 mutation']}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
