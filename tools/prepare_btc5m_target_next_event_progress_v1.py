"""Bounded offline Target-next-fill dataset; no model fitting on this host."""
from __future__ import annotations
import bisect
import collections
import json
import math
from audit_btc5m_target_core_loop_topology_v1 import ROOT,R,MIDS,SIDES,EPS,Ledger,read,sha,side_of

PACKAGE=ROOT/'.lan_worker_v1/target_next_event_progress_20260912_v1'
BASE=['phase','strong_mid','spread','strong_depth','book_age_log','gross_log','net_fraction','floor_fraction','live_lots','oldest_age_log']
HISTORY=['last_payment_fraction','no_payment_event_run','last_had_birth','last_had_payment','last_had_active_payment',
         'paid_to_gross','births_to_events','payments_to_events']


def main():
    rows=[];sources=[];excluded=collections.Counter()
    for mid in MIDS:
        bundle='open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
        p=ROOT/'.lan_worker_v1'/bundle/f'input_{mid}.json.gz';s=read(p)
        sources.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        start,end=s['market']['window_start_ms'],s['market']['window_end_ms']
        by=collections.defaultdict(list)
        for a in s['targetActions']:
            assert a['quote_type']=='BID' and a['role'] in ('MAKER','TAKER')
            assert a['event_ms']%1000==0
            by[a['event_ms']].append(a)
        ticks=sorted(by);books=sorted(s['books'],key=lambda b:b['received_ms']);bt=[b['received_ms'] for b in books]
        ledger=Ledger();inv={x:0. for x in SIDES};cost=paid=0.;nopay=pay_events=0
        for i,t in enumerate(ticks):
            aa=by[t];qty={x:sum(a['shares'] for a in aa if a['side']==x) for x in SIDES}
            ev=ledger.process_batch(t,qty['UP'],qty['DOWN'])
            for a in aa: inv[a['side']]+=a['shares'];cost+=a['price']*a['shares']
            payment=sum(x['qty'] for x in ev['payments']);paid+=payment;pay_events+=int(payment>EPS)
            nopay=0 if payment>EPS else nopay+1
            if i+1==len(ticks):excluded['last_event_no_next']+=1;continue
            anchor=t+999;future_t=ticks[i+1]
            if not(start<=anchor<end and future_t<=end):excluded['outside_market']+=1;continue
            side=side_of(inv)
            if side=='FLAT':excluded['flat_state']+=1;continue
            j=bisect.bisect_left(bt,anchor)-1
            if j<0:excluded['missing_past_book']+=1;continue
            book=books[j];bid,ask=book['best_bid'],book['best_ask']
            if bid is None or ask is None or not(0<bid<ask<1):excluded['invalid_two_sided_book']+=1;continue
            assert book['received_ms']<anchor<future_t and book['source_ms']<=book['received_ms']
            weak='DOWN' if side=='UP' else 'UP';gross=sum(inv.values());gap=abs(inv['UP']-inv['DOWN'])
            future=by[future_t];fq={x:sum(a['shares'] for a in future if a['side']==x) for x in SIDES}
            aq=sum(a['shares'] for a in future if a['side']==weak and a['role']=='TAKER')
            pay_next=min(gap,fq[weak]);active_lower=max(0.,pay_next-(fq[weak]-aq));active_upper=min(pay_next,aq)
            # UNKNOWN if a mixed-route crossing prevents assigning Active to old debt.
            active_label=1 if active_lower>EPS else 0 if active_upper<=EPS else None
            last_active_lower=0.
            for pay_side in SIDES:
                q=qty[pay_side];r=sum(a['shares'] for a in aa if a['side']==pay_side and a['role']=='TAKER')
                allocated=sum(x['qty'] for x in ev['payments'] if x['fill_side']==pay_side)
                last_active_lower+=max(0.,allocated-(q-r))
            lots=ledger.q[side];oldest=anchor-lots[0]['born_t'] if lots else 0
            bq=sum(q for price,q in book['bids'] if price==bid);aqty=sum(q for price,q in book['asks'] if price==ask)
            imbalance=(bq-aqty)/max(1.,bq+aqty)
            feats=dict(phase=(anchor-start)/(end-start),strong_mid=(bid+ask)/2 if side=='UP' else 1-(bid+ask)/2,
                spread=ask-bid,strong_depth=imbalance if side=='UP' else -imbalance,
                book_age_log=math.log1p(anchor-book['source_ms']),gross_log=math.log1p(gross),
                net_fraction=gap/(1+gross),floor_fraction=(min(inv.values())-cost)/(1+gross),
                live_lots=len(lots),oldest_age_log=math.log1p(oldest),last_payment_fraction=payment/max(EPS,sum(ev['outstanding_before'].values())),
                no_payment_event_run=nopay,last_had_birth=int(bool(ev['births'])),last_had_payment=int(payment>EPS),
                last_had_active_payment=int(last_active_lower>EPS),paid_to_gross=paid/(1+gross),
                births_to_events=ledger.births/(i+1),payments_to_events=pay_events/(i+1))
            assert set(feats)==set(BASE+HISTORY)
            rows.append(dict(market=mid,anchor_ms=anchor,last_fill_bucket=t,next_fill_bucket=future_t,
                book_received_ms=book['received_ms'],book_source_ms=book['source_ms'],strong_side=side,
                features=feats,labels=dict(next_weak_payment=int(pay_next>EPS),
                    next_clean_strong_add=int(fq[side]>EPS and fq[weak]<=EPS),next_active_payment=active_label)))
    PACKAGE.mkdir(parents=True,exist_ok=True)
    payload=dict(version='BTC5M_TARGET_NEXT_EVENT_PROGRESS_DATASET_V1',rows=rows,sources=sources,
        base_features=BASE,history_features=HISTORY,excluded=dict(excluded),
        observation_contract='Anchor is end of a whole-second Target fill bucket; public book must be received strictly before anchor; next bucket is label only.',
        limitations=['Target private receipt clocks unknown; observed_at_ms is observer arrival, not assumed Target own fill knowledge.',
            'This is conditional next-observed-fill prediction, not a next order/WAIT/route-admission policy.',
            'Reconstructed Target past state is offline teacher data, never OUR runtime authority.',
            'All eight markets already consumed; observations are not independent randomized decisions.',
            'FIFO and zero starting inventory are reconstruction assumptions.'])
    (PACKAGE/'dataset.json').write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(rows=len(rows),excluded=dict(excluded),markets=dict(collections.Counter(r['market'] for r in rows)),
        labels={k:dict(collections.Counter(str(r['labels'][k]) for r in rows)) for k in rows[0]['labels']})))


if __name__=='__main__':main()
