from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
SDB=ROOT/'data/strategy_r3s_r31_echtgeld_v1.db'; EDB=ROOT/'data/echtgeld_engine_v1.db'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_live_carrier_viability_echtgeld11_v1.json'
MIDS=[1783183,1783188,1783300,1783310,1783311,1783352,1783366,1783547,1783550,1783565,1783575]
TICK=.01

def j(v):
    try:return json.loads(v) if v else {}
    except:return {}

def main():
    sc=sqlite3.connect(SDB); sc.row_factory=sqlite3.Row; ec=sqlite3.connect(EDB); ec.row_factory=sqlite3.Row
    rows=[]; market_summary=[]
    for mid in MIDS:
        dec=[dict(r) for r in sc.execute("select decision_ms,seconds_left,public_state_json,portfolio_state_json from our_decisions where market_id=? and seconds_left>60 and seconds_left<180 order by decision_ms",(mid,))]
        orders=[dict(r) for r in ec.execute("select client_order_id,side,requested_price,requested_shares,created_at_ms,completed_at_ms,cancel_requested_at_ms from engine_cap100_orders where source_id='R3S_R31_8790' and source_market_id=? and role='MAKER' order by created_at_ms",(mid,))]
        ev=[dict(r) for r in ec.execute("select occurred_at_ms,event_type,client_order_id,side,state,delta_shares from engine_cap100_events where source_market_id=? and role='MAKER' order by occurred_at_ms,seq",(mid,))]
        byoid=defaultdict(list); fills=defaultdict(list)
        for e in ev:
            oid=e.get('client_order_id')
            if oid: byoid[oid].append(e)
            if oid and e['event_type']=='FILL_DELTA' and float(e.get('delta_shares') or 0)>0: fills[oid].append((int(e['occurred_at_ms']),float(e['delta_shares']),str(e.get('side') or '')))
        o_by={o['client_order_id']:o for o in orders}
        n0=len(rows)
        for d in dec:
            t=int(d['decision_ms']); ps=j(d['public_state_json']); pf=j(d['portfolio_state_json'])
            live=[]
            for o in orders:
                if int(o['created_at_ms'])>t: continue
                hist=[e for e in byoid.get(o['client_order_id'],[]) if int(e['occurred_at_ms'])<=t]
                if not hist: continue
                last=hist[-1]; et=str(last['event_type']); st=str(last.get('state') or '')
                # Only venue-confirmed, still-live carriers. PLANNED and REJECTED never own completion capacity.
                ack=[e for e in hist if e['event_type'] in {'ORDER_RESTING','ORDER_ACCEPTED','ORDER_PARTIAL_FILL'}]
                if not ack: continue
                if et in {'ORDER_FILLED','ORDER_CANCELED','ORDER_REJECTED'}: continue
                if st not in {'RESTING','PARTIAL_FILL','CANCEL_PENDING'} and et not in {'ORDER_RESTING','ORDER_ACCEPTED','ORDER_PARTIAL_FILL','CANCEL_REQUEST_ACCEPTED'}: continue
                cum=sum(q for tt,q,_ in fills.get(o['client_order_id'],[]) if tt<=t); rem=max(0.0,float(o['requested_shares'])-cum)
                if rem<=1e-9: continue
                live.append((o,hist,cum,rem,int(ack[0]['occurred_at_ms']),st,et))
            counts=defaultdict(int)
            for o,*_ in live: counts[str(o['side'])]+=1
            for o,hist,cum,rem,ack_ms,st,et in live:
                side=str(o['side']); bid=ps.get('predictUpBid') if side=='UP' else ps.get('predictDownBid'); ask=ps.get('predictUpAsk') if side=='UP' else ps.get('predictDownAsk'); midpx=ps.get('predictUpMid') if side=='UP' else ps.get('predictDownMid')
                price=float(o['requested_price']); req=float(o['requested_shares']); future=fills.get(o['client_order_id'],[])
                last_same=[tt for oo in orders if str(oo['side'])==side for tt,q,_ in fills.get(oo['client_order_id'],[]) if tt<=t]
                recent5=sum(q for oo in orders if str(oo['side'])==side for tt,q,_ in fills.get(oo['client_order_id'],[]) if t-5000<tt<=t)
                recent15=sum(q for oo in orders if str(oo['side'])==side for tt,q,_ in fills.get(oo['client_order_id'],[]) if t-15000<tt<=t)
                r={'marketId':mid,'t':t,'secondsLeft':float(d['seconds_left']),'orderId':o['client_order_id'],'side':side,'sideIsUp':int(side=='UP'),'orderAgeS':(t-ack_ms)/1000.0,'requestedQty':req,'remainingQty':rem,'remainingRatio':rem/max(req,1e-9),'partialFillRatio':cum/max(req,1e-9),'price':price,'notional':price*req,'venueState':st or et,'cancelPending':int(st=='CANCEL_PENDING' or et=='CANCEL_REQUEST_ACCEPTED'),'sameSideLiveCount':counts[side],'oppSideLiveCount':counts['DOWN' if side=='UP' else 'UP'],'currentBid':bid,'currentAsk':ask,'currentMid':midpx,'quoteOffsetTicks':None if bid is None else (float(bid)-price)/TICK,'spreadTicks':None if bid is None or ask is None else (float(ask)-float(bid))/TICK,'predictSourceAgeMs':ps.get('predictSourceAgeMs'),'predictReceiptAgeMs':ps.get('predictReceiptAgeMs'),'combinedAbsNet':pf.get('combined_abs_net'),'combinedCoverage':pf.get('combined_paired_coverage'),'worstCaseFloor':pf.get('worst_case_floor'),'bestCasePnl':pf.get('best_case_pnl'),'makerAbsNet':pf.get('maker_abs_net'),'lastMakerAgeMs':pf.get('last_maker_age_ms'),'makerFills5s':pf.get('maker_fills_5s'),'makerShares5s':pf.get('maker_shares_5s'),'sameSideFillShares5s':recent5,'sameSideFillShares15s':recent15,'timeSinceSameSideFillS':None if not last_same else (t-max(last_same))/1000.0}
                for h in (5,15,30):
                    fq=sum(q for tt,q,_ in future if t<tt<=t+h*1000); r[f'futureFillShares{h}s']=fq; r[f'labelFill{h}s']=int(fq>1e-9)
                rows.append(r)
        market_summary.append({'marketId':mid,'decisionRows':len(dec),'carrierRows':len(rows)-n0})
    sc.close(); ec.close()
    out={'version':'R4_LIVE_CARRIER_VIABILITY_ECHTGELD11_V1','researchOnly':True,'runtimeAuthority':False,'sourceBoundary':'R3+R3.1 Echtgeld strict-past decision snapshots + 8781 durable venue order lifecycle; labels use only future venue FILL_DELTA within horizon and are never runtime inputs','markets':MIDS,'rows':rows,'marketSummary':market_summary}
    OUT.write_text(json.dumps(out,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'markets':len(MIDS),'rows':len(rows),'positive5':sum(r['labelFill5s'] for r in rows),'positive15':sum(r['labelFill15s'] for r in rows),'positive30':sum(r['labelFill30s'] for r in rows),'perMarket':market_summary}))
if __name__=='__main__': main()
