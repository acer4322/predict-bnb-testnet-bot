"""Per-market observed Maker size metadata; never original-order or policy labels.

All-input observations are preserved; no outcome, inferred parent size, live policy,
HFT, training, or large DB scan. Full-market tags are POSTHOC_METADATA, not features.
"""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime,timezone,timedelta
import argparse,hashlib,json,statistics,math,sqlite3,time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
OUT=BASE/'target_maker_size_regimes_v1_20260910'
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
TZ=timezone(timedelta(hours=8))
PARENTS=BASE/'TARGET_MAKER_SIZE_PRICE_FLOOR_PARENTS_V1_20260910.jsonl'
PARENT_SHA='6578500dd035ece2eb34e58087a03a8b23733e005731781932b30d2a222a3a19'
META=BASE/'TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855.json'
META_SHA='cb078598e3cfb80fbc5ab66afaf8b870494aeeed99fa0991a32fa14ea5043cbd'
RECENT_SCORE=BASE/'TARGET_RECENT_MAKER_SHARES_SCORE_V2_20260910_1855.json'
RECENT_SHA='a777d6c3dad31fe936a957190addd24106cbf1ae087fd0456006fd92a236575f'
TIERS=(5,10,12,15,18,30,55)


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def load(p,cap=1048576):
    p=Path(p);assert p.stat().st_size<=cap
    return json.loads(p.read_text(encoding='utf-8-sig'))


def write(p,obj):
    with Path(p).open('x',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False)


def local(ms):return datetime.fromtimestamp(ms/1000,TZ).isoformat()


def pct(xs,p):
    if not xs:return None
    x=sorted(xs);j=(len(x)-1)*p;lo=math.floor(j);hi=math.ceil(j)
    return x[lo]+(x[hi]-x[lo])*(j-lo)


def profile(asset,hist):
    ranked=sorted(hist.items(),key=lambda x:(-x[1],x[0]));mode=ranked[0][0] if ranked else None
    tied=[q for q,n in ranked if n==ranked[0][1]] if ranked else []
    if not ranked:return 'NO_OBSERVED_MAKER_ORDERS',tied
    if asset=='BTC' and len(ranked)>1 and {ranked[0][0],ranked[1][0]}=={30.,55.}:
        return 'BTC_OBS_30_55_TWO_PEAKS',tied
    if len(tied)>1:return asset+'_OBS_TIED_MODES',tied
    if asset=='BTC' and mode==15:return 'BTC_OBS_MODE_15',tied
    if asset=='ETH' and mode==10:return 'ETH_OBS_MODE_10',tied
    if asset=='ETH' and mode==5:return 'ETH_OBS_MODE_5',tied
    return asset+'_OBS_OTHER_MODE',tied


def summarize(m,parents,source_kind):
    values=[p['qty'] for p in parents];hist=Counter(round(q,6) for q in values);ranked=sorted(hist.items(),key=lambda x:(-x[1],x[0]))
    name,tied=profile(m['asset'],hist);n=len(values)
    return dict(asset=m['asset'],market_id=m['marketId'],window_start_ms=m['startMs'],window_end_ms=m['endMs'],
      window_start_local=local(m['startMs']),window_end_local=local(m['endMs']),calendar_group=m['regime'],
      primary_cohort=m.get('primary',True),evidence_source=source_kind,
      size_profile=name,mode_qty=tied[0] if len(tied)==1 else None,tied_mode_quantities=tied,
      maker_filled_orders=n,mean_qty=statistics.mean(values) if n else None,median_qty=statistics.median(values) if n else None,
      p10_qty=pct(values,.1),p90_qty=pct(values,.9),minimum_observed_qty=min(values) if n else None,
      maximum_observed_qty=max(values) if n else None,
      top_modes=[dict(qty=q,orders=k,fraction=k/n) for q,k in ranked[:8]],
      full_qty_histogram=[dict(qty=q,orders=k) for q,k in sorted(hist.items())],
      known_tier_counts={str(q):hist.get(float(q),0) for q in TIERS},
      primary_two_peak_fraction=(hist[30]+hist[55])/n if n else None,
      multiple_fill_orders=sum(p.get('legs',1)>1 for p in parents),
      minimum_fill_price=min((p['price'] for p in parents),default=None),
      first_observed_fill_ms=min((p.get('firstMs') for p in parents if p.get('firstMs') is not None),default=None),
      last_observed_fill_ms=max((p.get('lastMs') for p in parents if p.get('lastMs') is not None),default=None),
      original_requested_quantity_known=False,terminal_completion_known=False,
      true_policy_epoch='UNKNOWN',label_scope='POST_MARKET_OBSERVED_FILL_METADATA_ONLY',
      feature_allowed=False,source_closure='market end is not proof of complete execution of every order')


def tests():
    assert profile('BTC',Counter({15.:5,3.:2}))[0]=='BTC_OBS_MODE_15'
    assert profile('BTC',Counter({30.:5,55.:5,3.:2}))[0]=='BTC_OBS_30_55_TWO_PEAKS'
    assert profile('BTC',Counter({30.:5,3.:4,55.:2}))[0]=='BTC_OBS_OTHER_MODE'
    assert profile('ETH',Counter({10.:5,5.:2}))[0]=='ETH_OBS_MODE_10'
    assert profile('ETH',Counter({10.:5,5.:5}))[0]=='ETH_OBS_TIED_MODES'
    assert profile('ETH',Counter())[0]=='NO_OBSERVED_MAKER_ORDERS'
    assert profile('BTC',Counter({18.:4,1.:2}))[0]!='BTC_OBS_MODE_15'
    m=dict(asset='ETH',marketId=1,startMs=0,endMs=300000,regime='TEST')
    s=summarize(m,[dict(qty=2,price=.5,legs=3)],'UNIT')
    assert s['maker_filled_orders']==1 and s['multiple_fill_orders']==1 and not s['feature_allowed']
    return 8


def build():
    start=time.monotonic();count=tests();OUT.mkdir(exist_ok=True)
    if (OUT/'REGISTRY.json').exists():print('REGISTRY_ALREADY_EXISTS_NO_REWRITE');return
    assert sha(PARENTS)==PARENT_SHA and sha(META)==META_SHA and sha(RECENT_SCORE)==RECENT_SHA
    metas=load(META)['markets'];rs=load(RECENT_SCORE);groups=defaultdict(list)
    with PARENTS.open(encoding='utf-8') as f:
        for line in f:
            p=json.loads(line);groups[(p['asset'],p['marketId'])].append(p)
    primary=[]
    for m in metas:
        pars=groups[(m['asset'],m['marketId'])];assert pars
        assert all(p['regime']==m['regime'] and m['startMs']<=p['firstMs']<=p['lastMs']<m['endMs'] for p in pars)
        primary.append(summarize(m,pars,'FROZEN_PARENT_ORDER_AGGREGATE'))
    assert len(primary)==198 and sum(r['maker_filled_orders'] for r in primary)==20797
    snap=Path(rs['source']['snapshot']);assert sha(snap)==rs['source']['snapshotSha256']
    sd=load(snap,8*1024**2);prior_groups=defaultdict(list)
    for p in sd['makerParents']:
        if p['period']=='PRECEDING_2H':
            prior_groups[(p['asset'],p['market_id'])].append(dict(qty=p['shares'],price=p['average_price'],
                  legs=p['fill_legs'],firstMs=p['first_event_ms'],lastMs=p['last_event_ms']))
    auxiliary=[]
    for r in rs['perMarket']:
        if r['period']!='PRECEDING_2H':continue
        pars=prior_groups[(r['asset'],r['market_id'])];assert len(pars)==r['filledOrderCount']
        m=dict(asset=r['asset'],marketId=r['market_id'],startMs=r['window_start_ms'],endMs=r['window_end_ms'],
            regime='AUX_PRECEDING_2H',primary=False)
        auxiliary.append(summarize(m,pars,'PREVIOUS_FIXED_SNAPSHOT_NOT_MODEL_COHORT'))
    assert len(auxiliary)==42
    summary={}
    for a in ['BTC','ETH']:
        for period in ['HISTORICAL','RECENT','AUX_PRECEDING_2H']:
            rows=[r for r in primary+auxiliary if r['asset']==a and r['calendar_group']==period]
            summary[a+'_'+period]=dict(markets=len(rows),profiles=dict(Counter(r['size_profile'] for r in rows)),
                earliest_window=min(r['window_start_local'] for r in rows),latest_window=max(r['window_end_local'] for r in rows),
                filledOrders=sum(r['maker_filled_orders'] for r in rows))
    d=dict(version='TARGET_MAKER_MARKET_SIZE_REGISTRY_V1',created_at=datetime.now(TZ).isoformat(),
        primary_markets=198,auxiliary_markets=42,primary=primary,auxiliary=auxiliary,summary=summary,
        sources=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [PARENTS,META,RECENT_SCORE,snap]],
        tests=count,newHFT=0,newTraining=0,seconds=time.monotonic()-start,
        semantics='descriptive cumulative observed fills by Maker order; no inferred original size, min/max or private strategy switch',
        forbids='full-market size_profile/top_modes are posthoc metadata, not inputs for earlier same-market forecasts')
    write(OUT/'REGISTRY.json',d)
    print(json.dumps(dict(output=str(OUT/'REGISTRY.json'),bytes=(OUT/'REGISTRY.json').stat().st_size,sha256=sha(OUT/'REGISTRY.json'),
       summary=summary,tests=count,seconds=d['seconds']),ensure_ascii=False))


def probe():
    if (OUT/'BOUNDED_TRANSITION_PROBES.json').exists():print('PROBES_ALREADY_EXIST');return
    from tools.audit_target_recent_maker_shares_v2_20260910 import is_five_minute
    times=['2026-09-07T17:00:00+08:00','2026-09-08T00:00:00+08:00','2026-09-08T12:00:00+08:00',
      '2026-09-09T00:00:00+08:00','2026-09-09T12:00:00+08:00','2026-09-10T00:00:00+08:00',
      '2026-09-10T06:00:00+08:00','2026-09-10T12:00:00+08:00']
    c=sqlite3.connect(Path('data/target_wallet_official_v1.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-4096')
    start=time.monotonic();deadline=start+6;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000);c.execute('BEGIN')
    q='''SELECT r.market_id,r.asset,r.title,r.resolved_at_ms,m.window_end_ms
        FROM target_market_results r INDEXED BY idx_target_results_asset_resolved
        JOIN target_markets m ON m.market_id=r.market_id WHERE r.asset=? AND r.resolved_at_ms>? AND r.resolved_at_ms<=?
        ORDER BY r.resolved_at_ms ASC LIMIT 10'''
    pq='''SELECT order_hash,side,quote_type,shares,average_price,fill_legs,first_event_ms,last_event_ms
        FROM target_parent_orders INDEXED BY idx_target_parents_asset_market_time
        WHERE asset=? AND market_id=? AND wallet=? AND role='MAKER'
        ORDER BY last_event_ms,parent_id LIMIT 1501'''
    samples=[];profiles=[];raw_parents={};plans=[]
    try:
        for text in times:
            t=int(datetime.fromisoformat(text).timestamp()*1000)
            qp=[tuple(r) for r in c.execute('EXPLAIN QUERY PLAN '+q,('BTC',t,t+1800000))]
            assert not any('SCAN r' in str(r) for r in qp)
            candidates=[dict(r) for r in c.execute(q,('BTC',t,t+1800000))]
            candidates=[m for m in candidates if is_five_minute(m['title']) and t-300000<=m['window_end_ms']-300000<t+1800000]
            if not candidates:samples.append(dict(probe_start=text,status='NO_RECORDED_5M_MARKET_IN_BOUNDED_RANGE'));continue
            row=candidates[0];mid=row['market_id'];qp2=[tuple(r) for r in c.execute('EXPLAIN QUERY PLAN '+pq,('BTC',mid,WALLET))]
            assert not any('SCAN target_parent' in str(r) for r in qp2)
            parents=[dict(r) for r in c.execute(pq,('BTC',mid,WALLET))]
            if len(parents)>1500:samples.append(dict(probe_start=text,market_id=mid,status='PARENT_ROW_CAP_NO_PROFILE'));continue
            if any(p['quote_type']!='BID' for p in parents):
                samples.append(dict(probe_start=text,market_id=mid,status='NONBUY_MAKER_REQUIRES_SEPARATE_SCOPE'));continue
            m=dict(asset='BTC',marketId=mid,startMs=row['window_end_ms']-300000,endMs=row['window_end_ms'],regime='BOUNDED_TIMELINE_PROBE',primary=False)
            pars=[dict(qty=p['shares'],price=p['average_price'],legs=p['fill_legs'],firstMs=p['first_event_ms'],lastMs=p['last_event_ms']) for p in parents]
            assert all(m['startMs']<=p['firstMs']<=p['lastMs']<m['endMs'] for p in pars)
            prof=summarize(m,pars,'INDEXED_OFFICIAL_DB_TIMELINE_ONLY');profiles.append(prof);raw_parents[str(mid)]=parents
            samples.append(dict(probe_start=text,market_id=mid,status='OBSERVED_PROFILE',size_profile=prof['size_profile'],
                    orders=len(parents),window_start=prof['window_start_local'],window_end=prof['window_end_local'],top_modes=prof['top_modes'][:3]))
            if not plans:plans=[qp,qp2]
    except sqlite3.OperationalError as ex:samples.append(dict(status='SQL_WATCHDOG_STOP',error=str(ex)))
    finally:c.rollback();c.close()
    d=dict(status='BOUNDED_OBSERVATION_NOT_EXACT_POLICY_SWITCH',samples=samples,profiles=profiles,raw_parents=raw_parents,
        query_plan=plans,seconds=time.monotonic()-start,probe_budget=len(times),
        nonAssumptions='no assumption of a single permanent switch; missing ranges are not inactivity; no new policy evaluation or training')
    write(OUT/'BOUNDED_TRANSITION_PROBES.json',d)
    print(json.dumps(dict(samples=samples,seconds=d['seconds'],completed_profiles=len(profiles),output=str(OUT/'BOUNDED_TRANSITION_PROBES.json')),ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['build','probe']);a=ap.parse_args()
    build() if a.action=='build' else probe()
