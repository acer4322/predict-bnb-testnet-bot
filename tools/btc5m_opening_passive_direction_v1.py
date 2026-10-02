"""V45 offline falsification: opening Maker flow versus subsequent inventory flow.

All features use only prefixes; future quantities are labels only. No fitting,
private intention label, winner-based selection, or Target inputs to OUR actor.
"""
import argparse
from collections import Counter
import gzip
import json
import math
from pathlib import Path

from export_market_capsule_source_bundle_v1 import _ro,DEFAULT_MAKER_DB,DEFAULT_PUBLIC_DB,DEFAULT_TARGET_DB,DEFAULT_TAPE_DIR
from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha

STEM='BTC5M_OPENING_PASSIVE_DIRECTION_V1_20260913'
PANEL='BTC5M_FROZEN_NEW_MARKET_PANEL_V1_20260913'
V43=ROOT/'.lan_worker_v1/demand_price_coordination_1977248_20260913_v2'
V44=ROOT/'.lan_worker_v1/tail_acquisition_ablation_1977248_20260913_v1'


def dump(tag,x):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def sign(q):
    return 'UP' if q>1e-8 else 'DOWN' if q < -1e-8 else None


def freeze():
    assert not (R/(STEM+'_PROTOCOL.json')).exists()
    with _ro(DEFAULT_MAKER_DB) as m,_ro(DEFAULT_PUBLIC_DB) as p,_ro(DEFAULT_TARGET_DB) as t:
        latest=dict(p.execute('SELECT market_id,sampled_at_ms FROM public_source_snapshots_v2 ORDER BY id DESC LIMIT 1').fetchone())
        available=[dict(r) for r in m.execute('SELECT * FROM maker_execution_market_quality_v1 WHERE eligible_execution_training=1 AND window_start_ms<=? ORDER BY window_start_ms DESC LIMIT 100',(latest['sampled_at_ms'],))]
        selected=[];screen=[]
        for row in available:
            mid=row['market_id'];pub=p.execute('SELECT COUNT(*) FROM public_source_snapshots_v2 WHERE market_id=?',(mid,)).fetchone()[0]
            target=t.execute("SELECT COUNT(*) FROM wallet_shadow_target_events WHERE asset='BTC' AND market_id=?",(mid,)).fetchone()[0]
            tape=(DEFAULT_TAPE_DIR/f'{mid}.json.xz').exists()
            screen.append(dict(market=mid,public_rows=pub,target_rows=target,tape_exists=tape))
            if pub and target and tape:selected.append(dict(quality=row,public_rows=pub,target_rows=target))
            if len(selected)==2:break
        assert [r['quality']['market_id'] for r in selected]==[2127218,2127048], 'Do not silently replace preinspected availability candidates'
        latest80=[dict(r) for r in t.execute("SELECT m.market_id,m.window_end_ms FROM target_markets m WHERE m.asset='BTC' AND m.window_end_ms>0 AND EXISTS(SELECT 1 FROM wallet_shadow_target_events e WHERE e.asset='BTC' AND e.market_id=m.market_id) ORDER BY m.window_end_ms DESC LIMIT 80")]
    assert len(latest80)==80
    model_hashes={k:sha(v/'manifest.json') for k,v in [('V43',V43),('V44',V44)]}
    assert model_hashes==dict(V43='8a8d7a86ca4f233facd1f77e9ba652cb3104d780b9b08c0a1fabe9d59affc158',V44='85d0f7f8aae17785e791b380d807f6817d32ee5ae74880fcd10c2067fdf63057')
    protocol=dict(status='FROZEN_BEFORE_OPENING_FEATURES_OR_NEW_TARGET_LABELS',latest_public=latest,
        native_selection=selected,availability_screen=screen,latest80=latest80,extra_context=[2127218,2127048,1977248],
        selection='Latest two COMPLETE_FORWARD eligible BTC markets with tape, public snapshots and Target activity. Only availability/counts inspected before freeze, no winner, PnL, final side or opening side. Adjacent markets, small chronological transfer, not independent population draws.',
        opening_cohort='Latest80 with observed Target activity, fixed before reading fills; three extra context markets reported separately, not counted in latest80.',
        predictors=['First Maker event-time bucket net side; simultaneous sides remain an aggregate, not an ordering assertion','First unambiguous one-sided Maker bucket at the earliest Maker timestamp only','Maker net at 10,30,60 elapsed seconds','All-route net at 10,30,60 seconds as accounting comparator'],
        labels=['Final observed net share side, never winner','Only incremental acquisitions strictly after feature cutoff, to remove mechanical inclusion of the opening inventory','Future Taker net after feature cutoff'],
        materiality='Report all final net signs and separate abs(final net)>15 diagnostic; no outcome-based replacement or threshold search.',
        causality='Association cannot distinguish fill feedback from preexisting quotes, common price moves or hidden strategy. Second-quantized fills cannot establish same-second order. No claim about private belief or received-time live availability.',
        dedup='Sep05 initial-direction audit compared OUR BOOK_IMBALANCE_SIGN with first Target materialization, not Target opening Maker versus later incremental flow. Sep07 confidence-source study did not identify formation. V33/V39 NO_DIRECTION already locks first OWN net with UP bootstrap asymmetry. Current falsifier tests opening persistence in a separately fixed later cohort, not another direction-persistence model fit.',
        model_manifest_sha256=model_hashes,native_arms=['V43 known final observed Target direction','V44 known final observed Target direction','V44 existing NO_DIRECTION first confirmed OWN net'],
        maximum_native_jobs=6,max_threads=4,model_fits=0,parameter_search=0,local_native_jobs=0,
        dedup_search='No 2127218/2127048 hits in handoffs, protocols/preregistrations, waves, selections, manifests or Python under research/tools/.lan_worker_v1; no collected result path for either. This establishes no located prior use, not an exhaustive proof against every external research record.',
        existing_constraints='No changes to fixed15, minimum NEW, repair counts/dust, depth pricing or growth controller. No soft-throttle tuning before the transfer evidence.')
    dump('PROTOCOL',protocol)
    return dict(status='FROZEN',native_markets=[2127218,2127048],opening_markets=80,model_manifest_sha256=model_hashes)


def metrics(rows,predictor,label,material=False):
    rr=[r for r in rows if not material or abs(r['final_net'])>15]
    valid=[r for r in rr if r['features'][predictor]['side'] is not None and r['features'][predictor][label] is not None]
    counts=Counter(r['features'][predictor][label] for r in valid)
    hits=sum(r['features'][predictor]['side']==r['features'][predictor][label] for r in valid)
    recall={s:sum(r['features'][predictor]['side']==s for r in valid if r['features'][predictor][label]==s)/counts[s] if counts[s] else None for s in ('UP','DOWN')}
    return dict(total=len(rr),valid=len(valid),ties_or_missing=len(rr)-len(valid),correct=hits,accuracy=hits/len(valid) if valid else None,
        label_counts=dict(counts),majority_accuracy=max(counts.values())/len(valid) if valid else None,
        per_side_recall=recall,balanced_accuracy=sum(recall.values())/2 if all(v is not None for v in recall.values()) else None)


def analyze():
    protocol=read(R/(STEM+'_PROTOCOL.json'));assert not (R/(STEM+'_RESULT.json')).exists()
    ids=[r['market_id'] for r in protocol['latest80']];allids=list(dict.fromkeys(ids+protocol['extra_context']))
    raw=[];rows=[];excluded=[]
    with _ro(DEFAULT_TARGET_DB) as db:
        db.execute('BEGIN')
        for mid in allids:
            end=db.execute('SELECT window_end_ms FROM target_markets WHERE market_id=?',(mid,)).fetchone()[0];start=end-300000
            fills=[dict(r) for r in db.execute("SELECT id,event_ms,role,side,quote_type,price,shares FROM wallet_shadow_target_events WHERE asset='BTC' AND market_id=? ORDER BY event_ms,id",(mid,))]
            raw.append(dict(market=mid,start=start,end=end,fills=fills))
            issues=[]
            if any(r['quote_type']!='BID' for r in fills):issues.append('SELL_OR_NON_BID_NEEDS_SEPARATE_ACCOUNTING')
            if any(not start<=r['event_ms']<end for r in fills):issues.append('OUTSIDE_WINDOW')
            if any(r['side'] not in ('UP','DOWN') or r['role'] not in ('MAKER','TAKER') or not 0<r['price']<1 or r['shares']<=0 for r in fills):issues.append('INVALID_FILL')
            if issues:excluded.append(dict(market=mid,issues=issues));continue
            def net(rr):return math.fsum(r['shares']*(1 if r['side']=='UP' else -1) for r in rr)
            finalnet=net(fills);cost=math.fsum(r['shares']*r['price'] for r in fills)
            inv={s:math.fsum(r['shares'] for r in fills if r['side']==s) for s in ('UP','DOWN')}
            maker=[r for r in fills if r['role']=='MAKER'];first=min((r['event_ms'] for r in maker),default=None)
            initial=[r for r in maker if r['event_ms']==first];features={}
            for name,cut,rr in [('first_maker_bucket',first,initial),('first_maker_unambiguous',first,initial if len({r['side'] for r in initial})==1 else [])]+[
                (f'{route.lower()}_{secs}s',start+secs*1000,[r for r in fills if r['event_ms']<start+secs*1000 and (route=='ALL' or r['role']==route)])
                for secs in (10,30,60) for route in ('MAKER','ALL')]:
                # Fixed windows exclude their endpoint; first event bucket is consumed in full.
                future=[r for r in fills if cut is not None and (r['event_ms']>cut if name.startswith('first_') else r['event_ms']>=cut)]
                fn=net(future);ft=net([r for r in future if r['role']=='TAKER']);n=net(rr)
                features[name]=dict(side=sign(n),net=n,cutoff_seconds=(cut-start)/1000 if cut is not None else None,
                    prefix_legs=len(rr),final_side=sign(finalnet),future_net=fn,future_side=sign(fn),future_taker_net=ft,future_taker_side=sign(ft),future_legs=len(future))
            rows.append(dict(market=mid,cohort='latest80' if mid in ids else 'context',start=start,end=end,
                fill_legs=len(fills),first_maker_seconds=(first-start)/1000 if first else None,first_maker_both_sides=len({r['side'] for r in initial})==2,
                final_net=finalnet,inventory=inv,cost=cost,payoff={s:inv[s]-cost for s in inv},features=features,
                second_quantized=all(r['event_ms']%1000==0 for r in fills)))
    rawpath=R/(STEM+'_RAW.json.gz');rawpath.write_bytes(gzip.compress(json.dumps(raw,separators=(',',':'),allow_nan=False).encode(),mtime=0))
    panel=[r for r in rows if r['cohort']=='latest80'];names=list(rows[0]['features'])
    scores={p:{label:metrics(panel,p,label) for label in ('final_side','future_side','future_taker_side')} for p in names}
    material={p:metrics(panel,p,'final_side',True) for p in names}
    result=dict(status='COMPLETE',audit='PASS',protocol_sha256=sha(R/(STEM+'_PROTOCOL.json')),raw_sha256=sha(rawpath),
        selected_latest80=len(ids),eligible_latest80=len(panel),excluded=excluded,rows=rows,metrics=scores,material_final_net_over15=material,
        first_maker_both_side_buckets=sum(r['first_maker_both_sides'] for r in panel),
        final_label_counts=dict(Counter(sign(r['final_net']) for r in panel)),
        caveat=protocol['causality'],model_fits=0,parameter_search=0,native_jobs=0)
    dump('RESULT',result)
    return dict(status='PASS',eligible_latest80=len(panel),excluded=excluded,first_maker=scores['first_maker_bucket'],maker30=scores['maker_30s'],context=[dict(market=r['market'],first=r['features']['first_maker_bucket'],final_net=r['final_net']) for r in rows if r['cohort']=='context'])


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('freeze','analyze'));a=ap.parse_args()
    print(json.dumps(globals()[a.action](),ensure_ascii=False))
