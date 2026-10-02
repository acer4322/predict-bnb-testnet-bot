"""Complete four-arm comparison; no engine, fitting, or causal attribution."""
import argparse,gzip,hashlib,json,math,statistics as S
from collections import Counter
from pathlib import Path

P=Path(__file__).resolve().parent;ROOT=P.parents[2]
BASE=ROOT/'data/research/lan_worker_returns/native-engine-stack-fav-under-185-20261002-v1'
VAR=ROOT/'data/research/lan_worker_returns/native-engine-stack-switch-throttle-185-20261002-v1'
STAGE=P/'stage/native_engine_stack_variants185_20261002_v1'
ap=argparse.ArgumentParser();ap.add_argument('--strict-stack',action='store_true');args=ap.parse_args()
if args.strict_stack:
    BASE=VAR=ROOT/'data/research/lan_worker_returns/native-engine-stack-strict-four185-20261002-v2r1'
    STAGE=P/'stage/native_engine_stack_strict185_20261002_v2r1'
OUT_PREFIX='B_STRICT_' if args.strict_stack else 'B_'
CLOUD=ROOT/'docs/research_specs/results/CLOUD_LAB_RESULTS_20261002.json'
RV=ROOT/'docs/research_specs/results/SPOT_RV5_TABLE_20261002.json'
cloud=json.loads(CLOUD.read_bytes());rv=json.loads(RV.read_bytes())
rows={**json.loads((BASE/'stack_local.json').read_bytes()),**json.loads((VAR/'stack_local.json').read_bytes())}
assert set(rows)=={'FAV_TAKER','UNDER_TAKER','V1_SWITCH','V2_THROTTLE'}
labels={r['market_id']:r['winner'] for r in json.loads((STAGE/'LABELS.json').read_bytes())['records']}
assert len(labels)==185
assert json.loads((P/'A_CLOUD_COMPARISON.json').read_bytes())['status']=='PASS'
cloud={s:{r['market']:r for r in rs} for s,rs in cloud.items()}
assert all(set(rs)==set(labels) for rs in cloud.values())

def corr(x,y):
    mx,my=S.fmean(x),S.fmean(y)
    denominator=math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/denominator if denominator else None

def effective(strategy,mid):
    if strategy in cloud:return strategy
    high=rv[str(mid)]['rv_5m']>=3.1834e-05
    return 'UNDER_TAKER' if high else ('FAV_TAKER' if strategy=='V1_SWITCH' else 'NO_TRADE')

def trace(path):return json.loads(gzip.decompress(path.read_bytes()))

summaries={};classified={};identity=[];validity=[]
for strategy,rs in rows.items():
    assert len(rs)==185 and {r['market'] for r in rs}==set(labels)
    pairs=[];classified[strategy]=[];makers=takers=0.;stats=Counter()
    for r in rs:
        mid=r['market'];eff=effective(strategy,mid);ret=BASE if strategy in cloud else VAR
        tr=trace(ret/'arms'/f'{strategy}_{mid}'/'orders_and_fills.json.gz')
        assert tr['market_id']==mid and tr['strategy']==strategy
        assert r==json.loads((ret/'arms'/f'{strategy}_{mid}'/'result.json').read_bytes())
        assert len(tr['orders'])==r['submits'] and len(tr['fills'])==r['fills']
        meta=json.loads((STAGE/'META'/f'{mid}.json').read_bytes())
        assert rv[str(mid)]['window_start_s']*1000==meta['window_start_ms']
        first_decision=tr['decisions'][0]['seconds'] if tr['decisions'] else None
        fav_clock_valid=eff!='FAV_TAKER' or first_decision==12
        maker_fills=sum(f['maker'] for f in tr['fills'])
        if args.strict_stack:
            assert maker_fills==0 and r['time_in_force']=='IOC'
            assert (r['policy_validity']=='VALID_OBSERVED_CONTEXT')==fav_clock_valid
            if not fav_clock_valid:
                assert eff=='FAV_TAKER' and r['submits']==r['fills']==r['cost']==r['pnl']==0
        validity.append({'strategy':strategy,'market_id':mid,'effective_strategy':eff,'initial_F_clock':'PASS_12S' if eff=='FAV_TAKER' and fav_clock_valid else ('INVALID_INITIAL_FAV_CLOCK' if not fav_clock_valid else 'NOT_REQUIRED_BY_EFFECTIVE_STRATEGY'),'first_policy_decision_seconds':first_decision,'first_book_received_seconds':(meta['received_min_ms']-meta['window_start_ms'])/1000,'maker_fill_count':maker_fills,'strict_only_taker_receipts':'FAIL_OBSERVED_MAKER' if maker_fills else 'PASS_OBSERVED_RECEIPTS','formal_full_spec_path_eligible':fav_clock_valid and not maker_fills})
        for o in tr['orders']:
            assert eff!='NO_TRADE' and o['qty']==15
            assert 12<=o['seconds']<(270 if eff=='FAV_TAKER' else 290)
            assert (o['place_ms']-meta['window_start_ms']-12000)%2000==0
        for side,key in (('UP','up'),('DOWN','dn')):
            assert abs(sum(f['qty'] for f in tr['fills'] if f['side']==side)-r[key])<1e-8
        assert abs(sum(f['qty']*f['price'] for f in tr['fills'])-r['cost'])<1e-8
        assert r['win']==labels[mid] and r['inferred'] is False and r['fits']==0 and r['accounting_reconciled']
        assert abs(r['pnl']-((r['up'] if r['win']=='UP' else r['dn'])-r['cost']))<1e-8
        assert abs(r['pnl_fee_1pct']-(r['pnl']-.01*r['cost']))<1e-8
        assert abs(r['pnl_fee_2pct']-(r['pnl']-.02*r['cost']))<1e-8
        cls=cloud['FAV_TAKER'][mid]['cls']
        assert cls==cloud['UNDER_TAKER'][mid]['cls']
        classified[strategy].append(dict(r,cls=cls,effective_strategy=eff,rv_5m=rv[str(mid)]['rv_5m'],classification_source='A_unchanged_lab_1Hz_path_offline_only'))
        if strategy not in cloud:
            if eff=='NO_TRADE':
                assert r['submits']==r['fills']==r['cost']==r['up']==r['dn']==r['pnl']==r['unresolved_owners_at_eof']==0
                matched=True
            else:
                parent=trace(BASE/'arms'/f'{eff}_{mid}'/'orders_and_fills.json.gz')
                assert tr['orders']==parent['orders'] and tr['fills']==parent['fills']
                pr=next(x for x in rows[eff] if x['market']==mid)
                assert all(r[k]==pr[k] for k in ('pnl','cost','up','dn','submits','fills','native_clock_ms','unresolved_owners_at_eof'))
                matched=True
            identity.append({'strategy':strategy,'market_id':mid,'effective_strategy':eff,'native_replayed':True,'exact_parent_orders_and_fills_or_zero':matched})
        a=cloud[eff][mid] if eff!='NO_TRADE' else {'pnl':0.,'cost':0.,'up':0.,'dn':0.}
        aq=a['up']+a['dn'];ap=a['cost']/aq if aq else None;bp=r['average_fill_price']
        pairs.append({'market_id':mid,'effective_strategy':eff,'classification':cls,'cloud_reference_pnl':a['pnl'],'B_pnl':r['pnl'],'B_minus_cloud_pnl':r['pnl']-a['pnl'],'cloud_reference_cost':a['cost'],'B_cost':r['cost'],'cloud_reference_average_fill_price':ap,'B_average_fill_price':bp,'average_fill_price_difference':bp-ap if bp is not None and ap is not None else None,'B_submits':r['submits'],'B_average_order_seconds':r['average_order_seconds'],'B_eof_unresolved_owners':r['unresolved_owners_at_eof']})
        makers+=sum(f['qty'] for f in tr['fills'] if f['maker']);takers+=sum(f['qty'] for f in tr['fills'] if not f['maker']);stats.update(r['stats'])
    ds=[r['B_minus_cloud_pnl'] for r in pairs];prices=[r['average_fill_price_difference'] for r in pairs if r['average_fill_price_difference'] is not None]
    c=corr([r['cloud_reference_pnl'] for r in pairs],[r['B_pnl'] for r in pairs]);md=S.fmean(ds)
    norders=sum(r['submits'] for r in rs)
    summaries[strategy]={'markets':185,'B_mean_pnl':S.fmean(r['pnl'] for r in rs),'cloud_reference_mean_pnl':S.fmean(r['cloud_reference_pnl'] for r in pairs),'B_minus_cloud_mean_pnl':md,'B_minus_cloud_pnl_population_std':S.pstdev(ds),'B_minus_cloud_pnl_sample_std':S.stdev(ds),'pnl_correlation':c,'abs_mean_lt_2':abs(md)<2,'corr_gt_095':c is not None and c>.95,'numerical_acceptance':'PASS' if abs(md)<2 and c is not None and c>.95 else 'FAIL','numerical_scope':'ALL_185_DESCRIPTIVE_INCLUDING_FLAGGED_PATHS; not full mechanism acceptance','invalid_initial_F_paths':sum(x['initial_F_clock']=='INVALID_INITIAL_FAV_CLOCK' for x in validity if x['strategy']==strategy),'full_spec_acceptance':'FAIL_STRICT_TAKER_MECHANISM_AND_DOCUMENTED_CLOCK_VALIDITY','mean_average_fill_price_difference':S.fmean(prices) if prices else None,'price_paired_markets':len(prices),'B_total_orders':norders,'B_mean_orders_per_market':S.fmean(r['submits'] for r in rs),'B_order_weighted_mean_seconds':sum(r['average_order_seconds']*r['submits'] for r in rs if r['submits'])/norders if norders else None,'B_market_mean_order_seconds':S.fmean(r['average_order_seconds'] for r in rs if r['submits']),'cloud_order_count_time_difference':'UNKNOWN_NOT_RECORDED_BY_SUPPLIED_LAB','B_maker_fill_qty':makers,'B_taker_fill_qty':takers,'EOF_unresolved_paths':sum(r['unresolved_owners_at_eof']>0 for r in rs),'EOF_unresolved_owners':sum(r['unresolved_owners_at_eof'] for r in rs),'mean_fee_1pct_sensitivity':S.fmean(r['pnl_fee_1pct'] for r in rs),'mean_fee_2pct_sensitivity':S.fmean(r['pnl_fee_2pct'] for r in rs),'effective_strategy_counts':dict(Counter(effective(strategy,r['market']) for r in rs)),'class_counts':dict(Counter(r['classification'] for r in pairs)),'stats':dict(stats),'largest_pnl_differences':sorted(pairs,key=lambda r:abs(r['B_minus_cloud_pnl']),reverse=True)[:10],'pairs':pairs}
    if args.strict_stack:
        invalid={x['market_id'] for x in validity if x['strategy']==strategy and x['initial_F_clock']=='INVALID_INITIAL_FAV_CLOCK'}
        vp=[r for r in pairs if r['market_id'] not in invalid]
        vc=corr([r['cloud_reference_pnl'] for r in vp],[r['B_pnl'] for r in vp]);vmd=S.fmean(r['B_minus_cloud_pnl'] for r in vp)
        summaries[strategy].update(only_taker_observed='PASS_ZERO_MAKER_FILLS',holdings_cap_basis='CONFIRMED_INVENTORY; pending ledger ownership retained',full_spec_acceptance=('INVALID_INCOMPLETE_INITIAL_F_CONTEXT' if invalid else ('PASS_DECLARED_NUMERIC_GATES' if abs(md)<2 and c is not None and c>.95 else 'FAIL_DECLARED_NUMERIC_GATES')),valid_context_only_descriptive={'markets':len(vp),'pnl_correlation':vc,'B_minus_cloud_mean_pnl':vmd,'B_minus_cloud_pnl_population_std':S.pstdev(r['B_minus_cloud_pnl'] for r in vp),'numerical_gates_pass':vc is not None and vc>.95 and abs(vmd)<2,'scope':'Context-valid subset disclosed for diagnosis, not a replacement for full 185 cohort acceptance.'})

out={'status':'COMPLETE_FOUR_ARM_NUMERICAL_COMPARISON','cloud_source_commit':'72095db1','cloud_sha256':hashlib.sha256(CLOUD.read_bytes()).hexdigest(),'RV_sha256':hashlib.sha256(RV.read_bytes()).hexdigest(),'RV_threshold':3.1834e-05,'variant_cloud_reference':'Per-market frozen RV selection from original cloud FAV/UNDER; v2 low-RV exactly zero. This is a composed reference, not a separately supplied cloud variant run.','RV_raw_bar_reconstruction':'UNKNOWN_RAW_BARS_NOT_SUPPLIED; supplied preregistered exogenous table, window_start verified','strategies':summaries,'A_platform_comparison':'PASS_370_ROWS_MAX_DIFFERENCE_ZERO','component_causal_attribution':'NOT_ISOLATED_BY_ABLATION','scope':'FROZEN_SPEC_ACTION_PRODUCER_ON_V49_MIXED_EXECUTION_KERNEL; original CG1AT alpha not replayed','all_old_gates_passed':False,'model_fits':0,'live_changes':0}
if args.strict_stack:
    out['execution_correction']='IOC, confirmed holding predicate, exact +12s initial F or invalid/no-trade; scoped new follow-on only'
    previous=json.loads((P/'B_CLOUD_COMPARISON.json').read_bytes())['strategies']
    out['strict_minus_original_GTC_package']={s:{'strict_mean_pnl':r['B_mean_pnl'],'GTC_mean_pnl':previous[s]['B_mean_pnl'],'mean_pnl_difference':r['B_mean_pnl']-previous[s]['B_mean_pnl'],'strict_total_orders':r['B_total_orders'],'GTC_total_orders':previous[s]['B_total_orders'],'strict_maker_qty':r['B_maker_fill_qty'],'GTC_maker_qty':previous[s]['B_maker_fill_qty'],'component_changes':'IOC + confirmed holding predicate + exact 12s context validity together; per-component contribution not isolated'} for s,r in summaries.items()}
(P/(OUT_PREFIX+'CLOUD_COMPARISON.json')).write_text(json.dumps(out,indent=2,allow_nan=False),encoding='utf-8')
(P/(OUT_PREFIX+'ALL_CLASSIFIED_RESULTS.json')).write_text(json.dumps(classified,separators=(',',':'),allow_nan=False),encoding='utf-8')
(P/('STRICT_VARIANTS_IDENTITY_AUDIT.json' if args.strict_stack else 'VARIANTS_IDENTITY_AUDIT.json')).write_text(json.dumps({'status':'PASS','rows':370,'native_replayed':370,'verified_orders_fills_or_zero':370,'rows_detail':identity},indent=2),encoding='utf-8')
(P/(OUT_PREFIX+'PATH_VALIDITY.json')).write_text(json.dumps({'status':'COMPLETE_WITH_INVALID_INITIAL_CONTEXT' if args.strict_stack else 'COMPLETE_WITH_MECHANISM_FAILURES','rows':740,'invalid_initial_F_clock_paths':sum(x['initial_F_clock']=='INVALID_INITIAL_FAV_CLOCK' for x in validity),'invalid_initial_F_markets':sorted({x['market_id'] for x in validity if x['initial_F_clock']=='INVALID_INITIAL_FAV_CLOCK'}),'policy_initialization_issue':'Missing official +12s book. Strict corrected run marks invalid and submits no FAV orders; historical GTC run selected F later. No replacement or missing-book inference.','maker_receipts':'Strict corrected IOC run asserts zero maker receipts; original GTC observed maker receipts.','rows_detail':validity},indent=2),encoding='utf-8')
print(json.dumps({s:{k:v for k,v in r.items() if k not in ('pairs','largest_pnl_differences','stats')} for s,r in summaries.items()}))
