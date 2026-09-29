"""Descriptive cross-environment standardization on the frozen public joins.
No prediction fitting, policy training, HFT, outcomes/winner lookup or retuning.
"""
from pathlib import Path
from collections import defaultdict,Counter
import gzip,json,hashlib,statistics,time,math
from tools.build_target_cross_size_public_join_v1_20260910 import OUT,BASE,GROUPS,sha,readjson,writejson,pct

LABELS=['nextHasTaker','nextNetContraction','nextFloorImprovement','nextSurplusFlip']


def features(r,public=False,age=False):
    mid=r['feature_book_mid'];ib=r['feature_normalizedAbsNet'];phase=r['feature_phase']
    pb=0 if mid<.3 else 1 if mid<.5 else 2 if mid<.7 else 3
    key=(phase,min(3,int(ib*4)),pb)
    if public:
        spread=r['feature_book_spread'];ratio=r['feature_net_to_weak_ask5']
        sb=0 if spread<=.01+1e-8 else 1 if spread<=.03+1e-8 else 2
        rb=0 if ratio<=.25 else 1 if ratio<=1 else 2
        key+=(sb,rb)
    if age:
        a=max(r['book_source_age_ms'],r['book_received_age_ms'])
        key+=(0 if a<=1000 else 1 if a<=3000 else 2,)
    return key


def eligible(r,fresh='fresh_3s'):
    return r[fresh] and r['feature_postGross']>1e-8 and r['label_nextHasTaker'] is not None and \
        .1<=r['feature_book_mid']<=.9 and r['feature_net_to_weak_ask5'] is not None


def comparison(left,right,public=False,age=False,restrict=None):
    sets=[]
    for rows in [left,right]:
        counts=Counter(r['marketId'] for r in rows);n=len(counts);cs={}
        for r in rows:
            if restrict is not None and features(r,True) not in restrict:continue
            k=features(r,public,age);w=1/n/counts[r['marketId']]
            c=cs.setdefault(k,dict(mass=0.,markets=set(),events=0,sums=Counter()))
            c['mass']+=w;c['markets'].add(r['marketId']);c['events']+=1
            for label in LABELS:c['sums'][label]+=w*float(r['label_'+label])
        sets.append(cs)
    a,b=sets;keys=[k for k in a if k in b and len(a[k]['markets'])>=3 and len(b[k]['markets'])>=3]
    weights={k:min(a[k]['mass'],b[k]['mass']) for k in keys};mass=sum(weights.values())
    def outcomes(label):
        if not mass:return dict(left=None,right=None,rightMinusLeft=None,meanAbsoluteCellDifference=None)
        x=sum(weights[k]*a[k]['sums'][label]/a[k]['mass'] for k in keys)/mass
        y=sum(weights[k]*b[k]['sums'][label]/b[k]['mass'] for k in keys)/mass
        mae=sum(weights[k]*abs(b[k]['sums'][label]/b[k]['mass']-a[k]['sums'][label]/a[k]['mass']) for k in keys)/mass
        return dict(left=x,right=y,rightMinusLeft=y-x,meanAbsoluteCellDifference=mae)
    matchedrowsleft=[r for r in left if features(r,public,age) in keys and (restrict is None or features(r,True) in restrict)]
    matchedrowsright=[r for r in right if features(r,public,age) in keys and (restrict is None or features(r,True) in restrict)]
    d=dict(publicAdded=public,clockAgeAdded=age,commonCells=len(keys),overlapMass=mass,
       leftEligible=len(left),rightEligible=len(right),leftCoverage=sum(a[k]['mass'] for k in keys),
       rightCoverage=sum(b[k]['mass'] for k in keys),leftMatchedEvents=len(matchedrowsleft),rightMatchedEvents=len(matchedrowsright),
       leftMatchedMarkets=len({r['marketId'] for r in matchedrowsleft}),rightMatchedMarkets=len({r['marketId'] for r in matchedrowsright}),
       outcomes={label:outcomes(label) for label in LABELS},
       cells=[dict(key=list(k),weight=weights[k],leftMarkets=len(a[k]['markets']),rightMarkets=len(b[k]['markets']),
          leftEvents=a[k]['events'],rightEvents=b[k]['events'],
          leftRates={l:a[k]['sums'][l]/a[k]['mass'] for l in LABELS},
          rightRates={l:b[k]['sums'][l]/b[k]['mass'] for l in LABELS}) for k in keys])
    return d,set(keys)


def distribution(rows,key):
    by=defaultdict(list)
    for r in rows:
        v=r.get(key)
        if v is not None:by[r['marketId']].append(v)
    return dict(events=sum(len(v) for v in by.values()),markets=len(by),
        pooledMedian=pct([x for vs in by.values() for x in vs],.5),medianOfMarketMedians=pct([statistics.median(v) for v in by.values()],.5))


def main():
    began=time.monotonic();op=OUT/'CONDITIONAL_COMPARISON.json'
    if op.exists():raise FileExistsError(str(op))
    summary=readjson(OUT/'SUMMARY.json',3000000);groups={};allrows=[];overall=Counter();clock={};bookchecks=[]
    smoke=readjson(OUT/'SMOKE.json');smokeinfo={r['marketId']:r['book'] for r in smoke['markets']}
    for g in GROUPS:
        result=readjson(OUT/(g+'.json'));p=Path(result['joined']['path']);assert sha(p)==result['joined']['sha256']
        rs=[];total=0
        with gzip.open(p,'rt',encoding='utf-8') as f:
            for line in f:
                total+=len(line.encode());assert total<32*1024**2;r=json.loads(line);rs.append(r)
                if r['book_source_ms'] is not None:
                    assert max(r['book_source_ms'],r['book_received_ms'],r['book_chain_source_max'],r['book_chain_received_max'])<r['t']
        assert len(rs)==summary['groups'][g]['eventStates'];groups[g]=rs;allrows+=rs
        overall.update({k:sum(r[k] for r in rs) for k in ['fresh_1s','fresh_3s','fresh_10s','sourceOnlyWouldUseUnreceived']})
        infos=[smokeinfo[r['marketId']] if r['book'].get('reusedSmoke') else r['book'] for r in result['markets']]
        bookchecks.append(dict(group=g,reconstructionRows=sum(r['rows'] for r in infos),
            sourceAfterReceived=sum(r['sourceAfterReceived'] for r in infos),
            deltaBeforeMismatches=sum(r.get('deltaBeforeMismatches',0) for r in infos),
            medianPerMarketSourceMinusReceived=statistics.median(r['sourceMinusReceivedMedian'] for r in infos)))
        good=[r for r in rs if eligible(r)]
        clock[g]=dict(eligible3s=len(good),eligible1s=sum(eligible(r,'fresh_1s') for r in rs),eligible10s=sum(eligible(r,'fresh_10s') for r in rs),
            lowPriceOutsideCommonMid=sum(r['fresh_3s'] and not .1<=r['feature_book_mid']<=.9 for r in rs),
            sourceAge=distribution(good,'book_source_age_ms'),receivedAge=distribution(good,'book_received_age_ms'),
            stateDistributions={key:distribution(good,key) for key in ['feature_book_spread','feature_weak_ask5','feature_net_to_weak_ask5',
               'feature_gross_to_book5','feature_current_buyqty_to_weak_ask5']})
    assert len(allrows)==14152 and len({r['marketId'] for r in allrows})==198
    comparisons=[]
    for name,left,right in [('BTC_OLD_TO_NEW','BTC_HISTORICAL','BTC_RECENT'),('ETH_OLD_TO_NEW','ETH_HISTORICAL','ETH_RECENT'),
                             ('RECENT_MATCHED_CLOCK_BTC_TO_ETH','BTC_RECENT','ETH_RECENT')]:
        a=[r for r in groups[left] if eligible(r)];b=[r for r in groups[right] if eligible(r)]
        basic,_=comparison(a,b);joint,keys=comparison(a,b,True);restricted,_=comparison(a,b,False,restrict=keys)
        # Secondary clock sensitivity exposed by the actual source/received offsets.
        # Same preregistered age bins1s/3s; do not tune them to maximize support.
        clocked,_=comparison(a,b,True,True)
        sensitivity=[]
        for cutoff in ['fresh_1s','fresh_10s']:
            aa=[r for r in groups[left] if eligible(r,cutoff)];bb=[r for r in groups[right] if eligible(r,cutoff)]
            c,_=comparison(aa,bb,True)
            sensitivity.append(dict(freshness=cutoff,result=c))
        comparisons.append(dict(name=name,basic=basic,withPublicExecutionState=joint,basicOnJointCellsOnly=restricted,
            withClockAgeSensitivity=clocked,freshnessSensitivity=sensitivity))
    # Export reusable flat, label-separated Parquet, never include future fields implicitly.
    import duckdb
    con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='128MB'")
    paths=[str(OUT/(g+'_JOINED.jsonl.gz')).replace('\\','/').replace("'","''") for g in GROUPS]
    plist='['+','.join("'"+p+"'" for p in paths)+']';parquet=OUT/'joined_states.parquet'
    if parquet.exists():raise FileExistsError(str(parquet))
    sql="COPY (SELECT * FROM read_json_auto("+plist+", format='newline_delimited', union_by_name=true)) TO '"+parquet.as_posix()+"' (FORMAT PARQUET, COMPRESSION ZSTD)"
    con.execute(sql)
    n=con.execute('SELECT count(*),count(DISTINCT marketId) FROM read_parquet(?)',[str(parquet)]).fetchone();assert n==(14152,198)
    schema=con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(parquet)]).fetchall();con.close()
    result=dict(version='TARGET_CROSS_SIZE_PUBLIC_CONDITIONAL_V1',verdict='DESCRIPTIVE_COMMON_SUPPORT_NOT_MODEL_OR_POLICY_EQUIVALENCE',
        markets=198,frames=14152,coverage=dict(overall),bookReconstructionChecks=bookchecks,clockAndDistributions=clock,
        comparisons=comparisons,sourceSummarySha256=sha(OUT/'SUMMARY.json'),
        joinedParquet=dict(path=parquet.as_posix(),bytes=parquet.stat().st_size,sha256=sha(parquet),rows=14152,markets=198,
          featureColumns=[x[0] for x in schema if x[0].startswith('feature_')],labelColumns=[x[0] for x in schema if x[0].startswith('label_')]),
        newHFT=0,modelFits=0,newTraining=0,policyChanges=0,seconds=time.monotonic()-began,
        limitations=['Public aggregate depth may include Target quotes; it is not external liquidity excluding self.',
          'Top5 displayed shares may span widely different prices; not all liquidity is executable at best price or an equal cost band.',
          'State ratios use known realized inventory, not unobserved original order intent or outstanding private responsibility.',
          'Labels are next OBSERVED fills; no HOLD/nonexecution decisions or placement-times are observed.',
          'Different time periods have large recorded source-receipt clock offsets. Both-before rule prevents using unreceived rows but cannot identify true clock synchronization.',
          'Adding conditioning dimensions changes common support; basic-on-joint-cells is included, but this is not a causal or predictive improvement test.',
          'No CI/equivalence test, public-volatility/spot join or actual cross-size policy training/performance was run.'])
    writejson(op,result)
    def compact(c):return {k:v for k,v in c.items() if k!='cells'}
    print(json.dumps(dict(output=op.as_posix(),bytes=op.stat().st_size,sha256=sha(op),coverage=dict(overall),
      bookReconstructionChecks=bookchecks,clockAndDistributions=clock,
      comparisons=[dict(name=c['name'],basic=compact(c['basic']),withPublic=compact(c['withPublicExecutionState']),
        basicOnJointCells=compact(c['basicOnJointCellsOnly']),clockSensitive=compact(c['withClockAgeSensitivity'])) for c in comparisons],
      parquet=result['joinedParquet'],seconds=result['seconds']),ensure_ascii=False))


if __name__=='__main__':main()
