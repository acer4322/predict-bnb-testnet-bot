"""Read existing client timing/fee records; publish aggregates without identifiers.

No private account endpoints, orders, service changes, DB writes or fee fitting.
Exact contract algebra/debit units remain unknown unless separately verified.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from export_graduation_local_inputs import ROOT, dump, load, pct


def distribution(xs):
    return dict(n=len(xs),min=min(xs) if xs else None,p10=pct(xs,.1),median=pct(xs,.5),p90=pct(xs,.9),p99=pct(xs,.99),max=max(xs) if xs else None)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out)
    c=sqlite3.connect((ROOT/'data/echtgeld_engine_v1.db').as_uri()+'?mode=ro',uri=True)
    c.execute('PRAGMA query_only=ON')
    # Raw history is inspected locally; only aggregate allowlisted numeric fields
    # are exported, without account/order/token identifiers or error messages.
    rows=list(c.execute('SELECT role,state,requested_shares,place_started_at_ms,place_completed_at_ms,raw_json FROM engine_cap100_orders'))
    c.close()
    timing=defaultdict(list); filled=defaultdict(list); fee_bands=defaultdict(list); statuses=defaultdict(int)
    for role,state,qty,start,end,raw in rows:
        if start is not None and end is not None:
            assert end>=start
            timing[role].append(end-start);statuses[(role,state)]+=1
            if state=='FILLED':filled[role].append(end-start)
        if not raw or state!='FILLED':continue
        o=json.loads(raw)
        if o.get('vendor')!='PREDICT_FUN' or o.get('side')!='BUY':continue
        try:
            p=float(o['price']);cash=float(o['filledUsdtAmount']);net=float(o['filledShareQty'])
            provider=float(o['marketProviderFee']);network=float(o['networkFee'])
        except (KeyError,TypeError,ValueError):continue
        if not 0<p<1 or net<=0:continue
        band='0.00-0.50' if p<=.5 else '0.50-0.60' if p<=.6 else '0.60-0.70' if p<=.7 else '0.70-0.80' if p<=.8 else '0.80-1.00'
        fee_bands[(str(o.get('orderType')),band)].append((provider,network,net,cash,cash/net-p))
    sampled_qty=[float(qty) for role,state,qty,start,end,raw in rows if role=='TAKER' and start is not None and end is not None]
    starts=[start for role,state,qty,start,end,raw in rows if start is not None and end is not None]
    ends=[end for role,state,qty,start,end,raw in rows if start is not None and end is not None]
    records=[]
    for (order_type,band),rs in sorted(fee_bands.items()):
        records.append(dict(local_order_type=order_type,reported_price_band=band,n=len(rs),
                            marketProviderFee_raw=distribution([r[0] for r in rs]),
                            networkFee_raw=distribution([r[1] for r in rs]),
                            reported_net_shares=distribution([r[2] for r in rs]),
                            reported_cash_per_net_share=distribution([r[3]/r[2] for r in rs]),
                            reported_cash_per_net_share_minus_reported_order_price=distribution([r[4] for r in rs])))
    obj=dict(version='GRADUATION_HISTORICAL_CLIENT_COST_EVIDENCE_V1',read_only=True,source='data/echtgeld_engine_v1.db::engine_cap100_orders',
             no_identifiers_exported=True,order_rows=len(rows),
             client_submit_to_response_ms={k:distribution(v) for k,v in timing.items()},
             FILLED_orders_client_submit_to_response_ms={k:distribution(v) for k,v in filled.items()},
             client_response_state_counts=[dict(local_intent_role=k[0],state=k[1],n=v) for k,v in sorted(statuses.items())],
             historical_taker_request_shares=distribution(sampled_qty),
             historical_measurement_window_UTC=[datetime.fromtimestamp(t/1000,timezone.utc).isoformat() for t in [min(starts),max(ends)]],
             timing_scope='Binance Wallet Predict.fun client place call start to response; excludes prior quote/book requests/signing. Historic small BUYs, not 150-300 share HEDGE or SELL timing.',
             actual_decision_to_order_accept_ms=None,actual_submit_to_exchange_fill_ms=None,
             fee_records=records,
             fee_unit='marketProviderFee/networkFee are raw history fields; exact units and debit semantics not independently verified, do not assume USD',
             observed_cost_scope='filledUsdtAmount / filledShareQty is reported cash per net share. Excess over reported order price can include fees, slippage or rounding; it is not isolated fee.',
             role_scope='Local MAKER is LIMIT intent, not verified exchange maker role; immediately crossed LIMIT can pay fees.',
             venue_sell=dict(supported=True,source='https://dev.predict.fun/how-to-create-or-cancel-orders-679306m0',restriction='held outcome shares and required approvals; current engine cap100 taker path implements BUY only; no SELL dispatched or runtime adapter changed'),
             fee_policy=dict(maker_base_fee=0,taker_base_rate='per-market feeRateBps from authoritative market API, observed 200',
                             source='https://predict.fun/zh-cn/learn/how-prediction-market-fees-work',
                             price_behavior='full base rate at <=0.50, decreasing above 0.50; no uniform fixed notional rate',
                             exact_deployed_fee_algebra='UNKNOWN',exact_fee_per_share='UNKNOWN',debit_asset='quote BUY shares / SELL USDT; history fields unverified',account_discount='UNKNOWN',
                             local_model_only='cash-equivalent fee per gross share = min(p,1-p)*feeRateBps/10000; core.py:775; not independently verified against deployed Predict contract',
                             local_model_examples=[dict(p=p,fee_usdt_equivalent_per_gross_share=min(p,1-p)*.02) for p in [.4,.5,.6,.7,.8]]),
             quote_fee_units_confirmed=dict(BUY='shares',SELL='USDT',
                 source='https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-agentic-wallet/references/prediction.md#L469-L518',
                 scope='quote feeAmount only; do not unconditionally equate order-history marketProviderFee/networkFee to quote fields'),
             HEDGE1_feasibility='UNKNOWN: public depth and historical response samples are evidence; no guaranteed arrival liquidity, exact fees or size-matched fill latency',
             model_fits=0,orders_submitted=0)
    dump(out/'GRADUATION_VENUE_COST_EVIDENCE.json',obj)
    print(json.dumps({'timing_samples':sum(map(len,timing.values())),'taker_ms':obj['client_submit_to_response_ms'].get('TAKER'),'taker_request_shares':obj['historical_taker_request_shares'],'fee_band_aggregates':len(records)}))


if __name__=='__main__':main()
