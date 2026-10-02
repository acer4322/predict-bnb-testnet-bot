"""Publish small per-market audit metadata and a lossless joined research table.
No full-market profile may become a same-market earlier prediction feature.
No models, native code, market outcomes or trading changes.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import json,time
import duckdb
from tools.mark_target_maker_size_regimes_v1_20260910 import BASE,OUT,load,write,sha,local

SOURCE=BASE/'target_cross_size_public_join_v1_20260910/joined_states.parquet'
SOURCE_SHA='a363924592e516e39b83a87809b734924ffbcfe9cc72eaeb6f3a071c458c8c46'


def validate_model_features(columns):
    if not columns or any(not c.startswith('feature_') for c in columns):
        raise ValueError('explicit feature_ columns only; audit size metadata, labels and identities are not model inputs')
    return list(columns)


def main():
    start=time.monotonic();op=OUT/'SUMMARY.json'
    if op.exists():raise FileExistsError(str(op))
    registry=load(OUT/'REGISTRY.json',4*1024**2);probes=load(OUT/'BOUNDED_TRANSITION_PROBES.json',2*1024**2)
    proof=load(OUT/'PROBE_RAW_FILL_VERIFICATION.json');assert proof['sourceSha256']==sha(OUT/'BOUNDED_TRANSITION_PROBES.json')
    assert sha(SOURCE)==SOURCE_SHA
    rows=registry['primary']+registry['auxiliary']+probes['profiles']
    assert len(rows)==len({(r['asset'],r['market_id']) for r in rows})==246
    flat=[];at=int(time.time()*1000)
    for r in rows:
        flat.append(dict(asset=r['asset'],market_id=r['market_id'],
            audit_primary_cohort=r['primary_cohort'],audit_window_start_ms=r['window_start_ms'],audit_window_end_ms=r['window_end_ms'],
            audit_calendar_group=r['calendar_group'],audit_size_profile=r['size_profile'],
            audit_maker_filled_orders=r['maker_filled_orders'],audit_size_mean=r['mean_qty'],audit_size_median=r['median_qty'],
            audit_size_p10=r['p10_qty'],audit_size_p90=r['p90_qty'],
            audit_top_modes_json=json.dumps(r['top_modes'],ensure_ascii=False,separators=(',',':')),
            audit_tier_counts_json=json.dumps(r['known_tier_counts'],ensure_ascii=False,separators=(',',':')),
            audit_multi_fill_orders=r['multiple_fill_orders'],audit_minimum_fill_price=r['minimum_fill_price'],
            audit_original_requested_qty_known=False,audit_order_terminal_known=False,
            audit_true_policy_epoch='UNKNOWN',audit_label_scope='POST_MARKET_METADATA_NOT_FEATURE',
            audit_source=r['evidence_source'],audit_annotation_recorded_ms=at))
    jp=OUT/'MARKET_LABELS.jsonl'
    with jp.open('x',encoding='utf-8') as f:
        for r in flat:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    parquet=OUT/'market_size_labels.parquet';joined=OUT/'states_with_size_audit.parquet'
    assert not parquet.exists() and not joined.exists()
    c=duckdb.connect();c.execute('SET threads=1');c.execute("SET memory_limit='128MB'")
    c.execute("COPY (SELECT * FROM read_json_auto('"+jp.as_posix()+"',format='newline_delimited')) TO '"+parquet.as_posix()+"' (FORMAT PARQUET,COMPRESSION ZSTD)")
    original_cols=[r[0] for r in c.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(SOURCE)]).fetchall()]
    audit_cols=[r[0] for r in c.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(parquet)]).fetchall() if r[0].startswith('audit_')]
    sql="COPY (SELECT s.*, "+','.join('r."'+x+'"' for x in audit_cols)+" FROM read_parquet('"+SOURCE.as_posix()+"') s LEFT JOIN read_parquet('"+parquet.as_posix()+"') r ON s.asset=r.asset AND s.marketId=r.market_id) TO '"+joined.as_posix()+"' (FORMAT PARQUET,COMPRESSION ZSTD)"
    c.execute(sql)
    n=c.execute('SELECT count(*),count(DISTINCT marketId),sum(CASE WHEN audit_size_profile IS NULL THEN 1 ELSE 0 END) FROM read_parquet(?)',[str(joined)]).fetchone()
    assert n==(14152,198,0)
    cols=','.join('"'+x+'"' for x in original_cols)
    diff=c.execute('SELECT count(*) FROM ((SELECT '+cols+' FROM read_parquet(?) EXCEPT ALL SELECT '+cols+' FROM read_parquet(?)) UNION ALL (SELECT '+cols+' FROM read_parquet(?) EXCEPT ALL SELECT '+cols+' FROM read_parquet(?)))',[str(SOURCE),str(joined),str(joined),str(SOURCE)]).fetchone()[0]
    assert diff==0
    assert all(x.startswith('audit_') for x in audit_cols)
    features=validate_model_features([x for x in original_cols if x.startswith('feature_')])
    for invalid in [['audit_size_mean'],['feature_postNet','label_nextHasTaker'],['feature_postNet','audit_size_profile']]:
        try:validate_model_features(invalid)
        except ValueError:pass
        else:raise AssertionError('future audit feature was not rejected')
    support=c.execute('''SELECT asset,audit_calendar_group,audit_size_profile,
      count(DISTINCT marketId) AS markets,count(*) AS states,
      sum(CASE WHEN fresh_3s THEN 1 ELSE 0 END) AS fresh_states
      FROM read_parquet(?) GROUP BY ALL ORDER BY asset,audit_calendar_group,audit_size_profile''',[str(joined)]).fetchall()
    c.close()
    # Sample chronology is descriptive. No unobserved periods are assigned a size.
    timeline=[]
    for r in probes['profiles']:
        timeline.append(dict(asset=r['asset'],market_id=r['market_id'],start=r['window_start_local'],end=r['window_end_local'],
            profile=r['size_profile'],orders=r['maker_filled_orders'],top_modes=r['top_modes'][:3],scope='TIMELINE_ONLY'))
    recent_btc=sorted([r for r in registry['auxiliary']+registry['primary'] if r['asset']=='BTC' and r['size_profile']=='BTC_OBS_30_55_TWO_PEAKS'],key=lambda r:r['window_start_ms'])
    first=recent_btc[0];timeline.append(dict(asset='BTC',market_id=first['market_id'],start=first['window_start_local'],end=first['window_end_local'],
        profile=first['size_profile'],orders=first['maker_filled_orders'],top_modes=first['top_modes'][:3],scope='EARLIEST_ALREADY_FROZEN_NEW_PROFILE_WITNESS'))
    summary=dict(status='MARKET_SIZE_METADATA_PUBLISHED_MULTI_PROFILE_HISTORY_OBSERVED',
        primaryMarkets=198,auxiliaryPriorSnapshotMarkets=42,boundedProbeMarketRecords=6,totalRegistryMarkets=246,
        unchangedOriginalStates=14152,unmatchedStates=0,originalColumnsMismatchCount=diff,
        groupProfiles=registry['summary'],timeline=sorted(timeline,key=lambda x:x['start']),
        boundedMissingRanges=[r for r in probes['samples'] if r['status']!='OBSERVED_PROFILE'],
        perProfileStateCoverage=[dict(zip(['asset','period','profile','markets','states','fresh3s'],r)) for r in support],
        exactSwitchTimestamp='NOT_IDENTIFIED_OR_ASSUMED_SINGLE_CHANGE',
        keyFinding='bounded probes observe intermediate20/55,35/70,30/50 peaks, so one15-to30/55 switch label is not adequate',
        inputArtifacts=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [OUT/'REGISTRY.json',OUT/'BOUNDED_TRANSITION_PROBES.json',OUT/'PROBE_RAW_FILL_VERIFICATION.json',SOURCE]],
        outputs=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [jp,parquet,joined]],
        featureWhitelist=features,validation=dict(profileUnitTests=registry['tests'],futureFeatureGuardTests=3,probeRawAggregation=proof),
        worker='probe timed out8s; no dispatch/training or local replay fallback',newHFT=0,newTraining=0,seconds=time.monotonic()-start,
        interpretation=['all observed size tags refer to cumulative fill distributions, not complete original order quantities',
          'six timeline records include one market with no observedMaker; two bounded time ranges have no recordedmarket; unknown != inactivity',
          '30 and55 may coexist in one market; several peaks do not prove software version switches or fixed private order tranches',
          'full-market metadata is retrospective and must not be an earlier same-market model feature; train-fitted prefix estimates require a separate contract',
          'recent pairedBTC/ETH comparison is same-period cross-asset development, not proof of old-to-new size-regime generalization',
          'old/newBTC source/received clock mismatch remains; size labeling does not fix execution-clock comparability',
          'additional timeline42+6 markets excluded from original198 policy/model cohort; never added by outcome-based selection'])
    write(op,summary)
    print(json.dumps({k:summary[k] for k in ['status','primaryMarkets','auxiliaryPriorSnapshotMarkets','boundedProbeMarketRecords','totalRegistryMarkets','unchangedOriginalStates','unmatchedStates','groupProfiles','timeline','perProfileStateCoverage','outputs','exactSwitchTimestamp','newHFT','newTraining','seconds']},ensure_ascii=False))


if __name__=='__main__':main()
