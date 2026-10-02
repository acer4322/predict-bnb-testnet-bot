from __future__ import annotations
import argparse, bisect, json, math, sqlite3, statistics
from collections import defaultdict, deque, Counter
from pathlib import Path

SIDES=("UP","DOWN"); EPS=1e-9

def ro(p):
    c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=20); c.row_factory=sqlite3.Row; c.execute("PRAGMA query_only=ON"); return c

def opp(s): return "DOWN" if s=="UP" else "UP"
def finite(v):
    try:return math.isfinite(float(v))
    except:return False

def getv(d,*keys):
    for k in keys:
        if k in d and d[k] is not None:return d[k]
    return None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--target',default='data/target_wallet_official_v1.db'); ap.add_argument('--signal',default='data/strategy_target_compare_v1.db'); ap.add_argument('--markets',nargs='*',type=int); ap.add_argument('--n',type=int,default=3); ap.add_argument('--output',required=True); a=ap.parse_args()
    tc=ro(a.target); sc=ro(a.signal)
    try:
        if a.markets: mids=a.markets
        else:
            sm=[int(r[0]) for r in sc.execute("SELECT DISTINCT market_id FROM our_decisions ORDER BY market_id DESC LIMIT 1000")]
            mids=[]
            for mid in sm:
                hit=tc.execute("SELECT 1 FROM wallet_shadow_target_events WHERE market_id=? AND asset='BTC' AND role IN ('MAKER','TAKER') AND side IN ('UP','DOWN') LIMIT 1",(mid,)).fetchone()
                if hit:mids.append(mid)
                if len(mids)>=max(1,a.n):break
            mids=sorted(mids)
        key_features=[
          ('predict_up_mid',('predictUpMid','predict_up_mid')),
          ('predict_down_mid',('predictDownMid','predict_down_mid')),
          ('spot_minus_strike_bps',('spotMinusStrikeBps','spot_minus_strike_bps')),
          ('chainlink_minus_strike_bps',('chainlinkMinusStrikeBps','chainlink_minus_strike_bps')),
          ('spot_queue_imbalance',('spotQueueImbalance','spot_queue_imbalance')),
          ('spot_taker_imbalance_1s',('spotTakerImbalance1s','spot_taker_imbalance_1s')),
          ('spot_return_1s_bps',('spotReturn1sBps','spot_return_1s_bps')),
          ('futures_queue_imbalance',('futuresQueueImbalance','futures_queue_imbalance')),
          ('futures_taker_imbalance_1s',('futuresTakerImbalance1s','futures_taker_imbalance_1s')),
          ('futures_return_1s_bps',('futuresReturn1sBps','futures_return_1s_bps')),
          ('perp_spot_basis_bps',('perpSpotBasisBps','perp_spot_basis_bps')),
          ('seconds_left',('secondsLeft','seconds_left')),
        ]
        allrows=[]; market_summaries=[]
        for mid in mids:
            sig=[]; st=[]; seen=set()
            for r in sc.execute("SELECT decision_ms,source_snapshot_ms,public_state_json FROM our_decisions WHERE market_id=? ORDER BY decision_ms",(mid,)):
                try:d=json.loads(str(r['public_state_json']))
                except:continue
                if not isinstance(d,dict):continue
                t=int(d.get('sampledAtMs') or r['source_snapshot_ms'] or r['decision_ms'])
                if t in seen:continue
                seen.add(t); d=dict(d); d['_t']=t; sig.append(d); st.append(t)
            ev=list(tc.execute("SELECT id,event_ms,role,side,quote_type,price,shares FROM wallet_shadow_target_events WHERE market_id=? AND asset='BTC' AND role IN ('MAKER','TAKER') AND side IN ('UP','DOWN') ORDER BY event_ms,id",(mid,)))
            by=defaultdict(list)
            for r in ev: by[int(r['event_ms'])].append(r)
            qs={s:deque() for s in SIDES}; out={s:0.0 for s in SIDES}; hold={s:0.0 for s in SIDES}; lotid=0
            roles=Counter(); feat_nonmiss=Counter(); joined=0; joined2s=0; strict_ok=0; route=Counter(); pre_nonzero=0
            for t,legs in sorted(by.items()):
                pre=dict(out); pre_total=pre['UP']+pre['DOWN']; pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None)
                agg={s:{'q':0.0,'maker':0.0,'taker':0.0,'notional':0.0} for s in SIDES}
                for r in legs:
                    s=str(r['side']); q=float(r['shares']); p=float(r['price']); rr=str(r['role'])
                    if q<=EPS:continue
                    agg[s]['q']+=q; agg[s]['notional']+=q*p; agg[s]['maker']+=q if rr=='MAKER' else 0; agg[s]['taker']+=q if rr=='TAKER' else 0
                rem={s:agg[s]['q'] for s in SIDES}; repair={s:0.0 for s in SIDES}
                for pay in SIDES:
                    dq=qs[opp(pay)]; need=rem[pay]
                    while need>EPS and dq:
                        lot=dq[0]; take=min(need,float(lot['remaining'])); lot['remaining']-=take; out[opp(pay)]-=take; repair[pay]+=take; need-=take
                        if lot['remaining']<=EPS:dq.popleft()
                    rem[pay]=need
                pair_now=min(rem['UP'],rem['DOWN']); rem['UP']-=pair_now; rem['DOWN']-=pair_now
                births={s:0.0 for s in SIDES}
                for s in SIDES:
                    if rem[s]>EPS:
                        lotid+=1; qs[s].append({'id':lotid,'remaining':rem[s]}); out[s]+=rem[s]; births[s]+=rem[s]
                rq=repair['UP']+repair['DOWN']; bq=births['UP']+births['DOWN']; iq=agg['UP']['q']+agg['DOWN']['q']
                if rq>EPS and bq>EPS: role='COMPOSITE_CROSSING'
                elif rq>EPS: role='REPAIR_ONLY'
                elif bq>EPS: role='CLEAN_AGGREGATE_EXPAND'
                elif iq>EPS: role='PAIR_ONLY_OR_NET_NEUTRAL'
                else: role='EMPTY'
                roles[role]+=1
                makerq=agg['UP']['maker']+agg['DOWN']['maker']; takerq=agg['UP']['taker']+agg['DOWN']['taker']
                route['MIXED' if makerq>EPS and takerq>EPS else 'MAKER_ONLY' if makerq>EPS else 'TAKER_ONLY' if takerq>EPS else 'NONE']+=1
                if pre_total>EPS: pre_nonzero+=1
                idx=bisect.bisect_left(st,t)-1
                srow=sig[idx] if idx>=0 else None; age=(t-int(srow['_t'])) if srow else None
                if srow:
                    joined+=1; strict_ok+=int(int(srow['_t'])<t); joined2s+=int(age is not None and 0<=age<=2000)
                    for nm,ks in key_features:
                        if finite(getv(srow,*ks)):feat_nonmiss[nm]+=1
                rec={'market_id':mid,'event_ms':t,'signal_ms':int(srow['_t']) if srow else None,'signal_age_ms':age,'strict_past':bool(srow and int(srow['_t'])<t),'within_2s':bool(srow and 0<=age<=2000),'pre_outstanding_side':pre_side,'pre_outstanding_qty':pre_total,'pre_hold_up':hold['UP'],'pre_hold_down':hold['DOWN'],'repair_qty':rq,'birth_qty':bq,'economic_role':role,'input_qty':iq,'maker_qty':makerq,'taker_qty':takerq,'route_mix':'MIXED' if makerq>EPS and takerq>EPS else 'MAKER_ONLY' if makerq>EPS else 'TAKER_ONLY' if takerq>EPS else 'NONE'}
                if srow:
                    for nm,ks in key_features: rec[nm]=getv(srow,*ks)
                allrows.append(rec)
                hold['UP']+=agg['UP']['q']; hold['DOWN']+=agg['DOWN']['q']
            ncl=len(by); market_summaries.append({'market_id':mid,'fill_clocks':ncl,'signals':len(sig),'joined_any':joined,'joined_2s':joined2s,'strict_ok':strict_ok,'pre_nonzero_clocks':pre_nonzero,'roles':dict(roles),'routes':dict(route),'feature_nonmissing':dict(feat_nonmiss)})
        n=len(allrows); j=[r for r in allrows if r['signal_ms'] is not None]; j2=[r for r in j if r['within_2s']]
        out={'version':'TARGET_DIRECTION_CONFIDENCE_JOINT_SMOKE_V1','researchOnly':True,'markets':mids,'summary':{'markets':len(mids),'fill_clocks':n,'joined_any':len(j),'joined_any_rate':len(j)/n if n else None,'joined_2s':len(j2),'joined_2s_rate':len(j2)/n if n else None,'strict_past_violations':sum(not r['strict_past'] for r in j),'role_counts':dict(Counter(r['economic_role'] for r in allrows)),'route_counts':dict(Counter(r['route_mix'] for r in allrows)),'pre_nonzero_rate':sum(r['pre_outstanding_qty']>EPS for r in allrows)/n if n else None,'feature_nonmissing_rates_2s':{nm:(sum(finite(r.get(nm)) for r in j2)/len(j2) if j2 else None) for nm,_ in key_features}},'market_summaries':market_summaries,'sample_rows':allrows[:20], 'guards':['Signal timestamp must be strictly earlier than fill clock.','Economic role is aggregate accounting reconstruction from confirmed Maker+Taker fills, not private intent.','No winner, settlement, final inventory, or future action is used.','NO ACTION / WAIT is not inferred in this smoke.']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out['summary'],ensure_ascii=False,indent=2))
    finally:tc.close();sc.close()
if __name__=='__main__':main()
