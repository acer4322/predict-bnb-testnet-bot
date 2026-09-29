"""Bounded, read-only book ETL for the frozen198-market cross-size audit.
No HFT, trading-policy import, model fit, database scan or source mutation.
Commands: init, smoke, group <ASSET_REGIME>, finalize. Existing outputs are reused.
"""
from pathlib import Path
from collections import defaultdict, Counter
import argparse, bisect, gzip, hashlib, json, lzma, math, statistics, time
from tools.build_market_capsule_v1 import _levels

BASE=Path('data/research/r4_v0/p0_provenance_v1')
OUT=BASE/'target_cross_size_public_join_v1_20260910'
META=BASE/'TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855.json'
SCORE=BASE/'TARGET_CROSS_SIZE_CONTROL_V1_20260910_SCORE.json'
CACHE=Path('data/research/market_capsule_v1/benchmark_50_v1/book_updates.parquet')
TAPES=Path('data/execution_tape_v1/markets')
GROUPS=('BTC_HISTORICAL','BTC_RECENT','ETH_HISTORICAL','ETH_RECENT')
EPS=1e-8
COLS=['market_id','source_ms','received_ms','order_count','is_checkpoint','best_bid','best_ask','spread',
      'bid_depth_total','ask_depth_total','top5_bids_json','top5_asks_json']
FEATURES=['phase','preNet','preGross','preFloor','postNet','postGross','postFloor','postBest',
          'normalizedAbsNet','normalizedFloor','observedYesPrice','route','hasTaker','buyOnly']
LABELS=['nextHasTaker','nextNetContraction','nextFloorImprovement','nextSurplusFlip','nextRiskAddition']


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def digest(d):return hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def readjson(p,cap=1048576):
    p=Path(p)
    if p.stat().st_size>cap:raise ValueError('input too large '+str(p))
    return json.loads(p.read_text(encoding='utf-8-sig'))


def writejson(p,d):
    with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)


def pct(xs,p):
    if not xs:return None
    s=sorted(xs);q=(len(s)-1)*p;lo=math.floor(q);hi=math.ceil(q)
    return s[lo]+(s[hi]-s[lo])*(q-lo)


def init():
    if (OUT/'MANIFEST.json').exists():return readjson(OUT/'MANIFEST.json')
    assert sha(META)=='cb078598e3cfb80fbc5ab66afaf8b870494aeeed99fa0991a32fa14ea5043cbd'
    assert sha(SCORE)=='406d2d6d968d190988d27675da5054dece86903cf481b5c673569d5cf627aaed'
    meta=readjson(META);score=readjson(SCORE);state=Path(score['eventStates']['path'])
    assert sha(state)==score['eventStates']['sha256']
    markets=[]
    for m in meta['markets']:
        key=m['asset']+'_'+m['regime'];p=CACHE if key=='BTC_HISTORICAL' else TAPES/(str(m['marketId'])+'.json.xz')
        assert p.exists() and (p==CACHE or p.stat().st_size<2000000)
        markets.append({k:m[k] for k in ('asset','regime','marketId','startMs','endMs')}|dict(group=key,
            source=p.as_posix(),sourceBytes=p.stat().st_size,sourceSha256=sha(p) if p!=CACHE else None))
    assert len(markets)==198
    d=dict(version='TARGET_CROSS_SIZE_PUBLIC_JOIN_V1',markets=markets,states=score['eventStates'],
       originalScoreSha256=sha(SCORE),originalMetadataSha256=sha(META),cache=dict(path=CACHE.as_posix(),bytes=CACHE.stat().st_size,sha256=sha(CACHE)),
       sourceCode={str(p):sha(p) for p in [Path(__file__),Path('tools/build_market_capsule_v1.py'),Path('tools/research_data_access_v1.py')]},
       effectiveTime='max source/received over dependency since checkpoint, strictly < target event_ms',
       primaryFreshnessMs=3000,sensitivityFreshnessMs=[1000,10000],originalMarkets=198,originalEventStates=14152,
       newHFT=0,newTraining=0,policyChanges=0)
    OUT.mkdir(exist_ok=True);writejson(OUT/'MANIFEST.json',d);return d


def state_rows(manifest):
    p=Path(manifest['states']['path']);assert sha(p)==manifest['states']['sha256'];groups=defaultdict(list);total=0
    with gzip.open(p,'rt',encoding='utf-8') as f:
        for line in f:
            total+=len(line.encode());assert total<32*1024**2
            r=json.loads(line);groups[r['marketId']].append(r)
    assert sum(map(len,groups.values()))==14152
    for rows in groups.values():rows.sort(key=lambda r:r['t'])
    return groups


def decorate(rows,chain_valid=None):
    out=[];prefixs=prefixr=None;valid=False;source_regressions=0;previous_source=-1
    for i,r in enumerate(rows):
        s=int(r['source_ms']);recv=int(r['received_ms']);cp=bool(r['is_checkpoint'])
        if s<previous_source:source_regressions+=1
        previous_source=s
        if cp:prefixs=s;prefixr=recv;valid=True
        elif prefixs is not None:prefixs=max(prefixs,s);prefixr=max(prefixr,recv)
        if chain_valid is not None:valid=bool(chain_valid[i])
        bids=json.loads(r['top5_bids_json']);asks=json.loads(r['top5_asks_json'])
        bb=r['best_bid'];ba=r['best_ask'];status='OK'
        if not valid or prefixs is None:status='NO_VALID_CHECKPOINT_CHAIN'
        elif not bids or not asks or bb is None or ba is None:status='EMPTY_BOOK_SIDE'
        elif not(0<bb<ba<1):status='CROSSED_OR_INVALID_BOOK'
        vals=[q for p,q in bids+asks]
        if any(not math.isfinite(q) or q<0 for q in vals):raise ValueError('bad depth')
        out.append(dict(seq=i,source_ms=s,received_ms=recv,chain_source_max=prefixs,chain_received_max=prefixr,
          available=max(prefixs,prefixr) if prefixs is not None else 2**63-1,status=status,
          best_bid=bb,best_ask=ba,spread=ba-bb if bb is not None and ba is not None else None,
          mid=(bb+ba)/2 if bb is not None and ba is not None else None,
          bid_qty=bids[0][1] if bids else None,ask_qty=asks[0][1] if asks else None,
          bid5=sum(q for p,q in bids),ask5=sum(q for p,q in asks),
          bid_total=r['bid_depth_total'],ask_total=r['ask_depth_total'],order_count=r['order_count']))
    if source_regressions:raise ValueError('nonmonotone source clock needs separate reconstruction contract')
    return out,dict(rows=len(out),sourceAfterReceived=sum(r['source_ms']>r['received_ms'] for r in out),
      sourceMinusReceivedMedian=pct([r['source_ms']-r['received_ms'] for r in out],.5),
      invalidBooks=dict(Counter(r['status'] for r in out)))


def raw_book(path,mid):
    with lzma.open(path,'rb') as f:raw=f.read(16*1024**2+1)
    if len(raw)>16*1024**2:raise ValueError('per-market expanded cap')
    d=json.loads(raw);assert int(d['marketId'])==mid and d['version']=='PREDICT_EXECUTION_TAPE_ARCHIVE_V1'
    bids={};asks={};valid=False;rows=[];valids=[];mismatch=0;checkpoints=0;first_bad=None
    assert len(d['updates'])<=25000
    for seq,u in enumerate(d['updates']):
        if not isinstance(u,list) or len(u)<7:raise ValueError('malformed update')
        if u[3]:
            bids=_levels(u[4]);asks=_levels(u[5]);valid=True;checkpoints+=1
        else:
            for name,book in [('bids',bids),('asks',asks)]:
                for ch in (u[6] or {}).get(name,[]):
                    if len(ch)<3:raise ValueError('malformed delta')
                    p,before,after=map(float,ch[:3]);seen=book.get(p,0.)
                    if not all(math.isfinite(v) for v in (p,before,after)) or not 0<p<1:raise ValueError('bad delta values')
                    if abs(seen-before)>1e-6*max(1.,abs(before),abs(seen)):
                        mismatch+=1;valid=False
                        if first_bad is None:first_bad=dict(seq=seq,side=name,p=p,expected=before,reconstructed=seen)
                    if after<=1e-12:book.pop(p,None)
                    else:book[p]=after
        b5=sorted(bids.items(),reverse=True)[:5];a5=sorted(asks.items())[:5]
        rows.append(dict(market_id=mid,source_ms=int(u[0]),received_ms=int(u[1]),order_count=int(u[2]),is_checkpoint=int(u[3]),
            best_bid=b5[0][0] if b5 else None,best_ask=a5[0][0] if a5 else None,
            bid_depth_total=sum(bids.values()),ask_depth_total=sum(asks.values()),
            top5_bids_json=json.dumps(b5),top5_asks_json=json.dumps(a5)))
        valids.append(valid)
    bs,info=decorate(rows,valids);info.update(compressedBytes=path.stat().st_size,expandedBytes=len(raw),
        deltaBeforeMismatches=mismatch,firstDeltaMismatch=first_bad,checkpoints=checkpoints)
    return bs,info,rows


def get_book(m,access=None):
    if m['group']=='BTC_HISTORICAL':
        rel=access.query('book_updates',where='market_id='+str(m['marketId']),columns=COLS,
                         order_by='source_ms,received_ms',limit=25001)
        rows=[dict(zip(COLS,r)) for r in rel.fetchall()];assert len(rows)<=25000
        bs,info=decorate(rows);info['sourceType']='EXISTING_CAPSULE_PARQUET';return bs,info,rows
    p=Path(m['source']);assert p.stat().st_size==m['sourceBytes'] and sha(p)==m['sourceSha256']
    bs,info,rows=raw_book(p,m['marketId']);info['sourceType']='EXACT_MARKET_TAPE_RECONSTRUCTED_ONCE';return bs,info,rows


def join_frames(frames,book):
    ordered=sorted(book,key=lambda r:(r['available'],r['seq']));j=0;latest=None;joined=[]
    srcs=[r['source_ms'] for r in book]
    for r in frames:
        t=r['t']
        while j<len(ordered) and ordered[j]['available']<t:
            c=ordered[j]
            if latest is None or c['seq']>latest['seq']:latest=c
            j+=1
        z={k:r[k] for k in ('asset','regime','marketId','t')};z['stateSha256']=digest(r)
        z.update({'feature_'+k:r.get(k) for k in FEATURES});z.update({'label_'+k:r.get(k) for k in LABELS})
        z.update(book_status='NO_STRICT_PRIOR_BOOK',fresh_1s=False,fresh_3s=False,fresh_10s=False,
            book_source_ms=None,book_received_ms=None,book_chain_source_max=None,book_chain_received_max=None,
            book_source_age_ms=None,book_received_age_ms=None,feature_book_mid=None,feature_book_spread=None,
            feature_book_bid_qty=None,feature_book_ask_qty=None,feature_book_bid5=None,feature_book_ask5=None,
            feature_book_total_depth=None,feature_book_imbalance5=None,feature_weak_ask=None,
            feature_weak_ask5=None,feature_net_to_weak_ask5=None,feature_gross_to_book5=None,
            feature_current_buyqty_to_weak_ask5=None,feature_book_order_count=None,
            sourceOnlyWouldUseUnreceived=False)
        idx=bisect.bisect_left(srcs,t)-1
        if idx>=0:z['sourceOnlyWouldUseUnreceived']=book[idx]['chain_received_max'] is not None and book[idx]['chain_received_max']>=t
        if latest is not None:
            b=latest
            assert b['source_ms']<t and b['received_ms']<t and b['chain_source_max']<t and b['chain_received_max']<t
            z.update(book_status=b['status'],book_source_ms=b['source_ms'],book_received_ms=b['received_ms'],
                book_chain_source_max=b['chain_source_max'],book_chain_received_max=b['chain_received_max'],
                book_source_age_ms=t-b['source_ms'],book_received_age_ms=t-b['received_ms'])
            if b['status']=='OK':
                age=max(z['book_source_age_ms'],z['book_received_age_ms'])
                z.update(fresh_1s=age<=1000,fresh_3s=age<=3000,fresh_10s=age<=10000)
                net=r['postNet'];g=r['postGross'];weak5=b['bid5'] if net>EPS else b['ask5'] if net<-EPS else None
                weakask=1-b['best_bid'] if net>EPS else b['best_ask'] if net<-EPS else None
                total5=b['bid5']+b['ask5']
                z.update(feature_book_mid=b['mid'],feature_book_spread=b['spread'],feature_book_bid_qty=b['bid_qty'],
                  feature_book_ask_qty=b['ask_qty'],feature_book_bid5=b['bid5'],feature_book_ask5=b['ask5'],
                  feature_book_total_depth=b['bid_total']+b['ask_total'],feature_book_imbalance5=(b['bid5']-b['ask5'])/total5 if total5>0 else None,
                  feature_weak_ask=weakask,feature_weak_ask5=weak5,feature_net_to_weak_ask5=abs(net)/weak5 if weak5 and weak5>0 else None,
                  feature_gross_to_book5=g/total5 if total5>0 else None,
                  feature_current_buyqty_to_weak_ask5=(r['postGross']-r['preGross'])/weak5 if weak5 and weak5>0 and r['buyOnly'] else None,
                  feature_book_order_count=b['order_count'])
        joined.append(z)
    return joined


def summarize(rows):
    valid=[r for r in rows if r['book_status']=='OK'];fresh=[r for r in rows if r['fresh_3s']]
    def distribution(key,rs=fresh):
        vs=[r[key] for r in rs if r.get(key) is not None]
        return dict(n=len(vs),median=pct(vs,.5),q10=pct(vs,.1),q90=pct(vs,.9))
    return dict(eventStates=len(rows),markets=len({r['marketId'] for r in rows}),statuses=dict(Counter(r['book_status'] for r in rows)),
        validPrior=len(valid),fresh1s=sum(r['fresh_1s'] for r in rows),fresh3s=len(fresh),fresh10s=sum(r['fresh_10s'] for r in rows),
        sourceOnlyUnreceived=sum(r['sourceOnlyWouldUseUnreceived'] for r in rows),
        sourceAge=distribution('book_source_age_ms',valid),receivedAge=distribution('book_received_age_ms',valid),
        midpoint=distribution('feature_book_mid'),spread=distribution('feature_book_spread'),
        weakAsk5=distribution('feature_weak_ask5'),netToWeakAsk5=distribution('feature_net_to_weak_ask5'),
        grossToBook5=distribution('feature_gross_to_book5'))


def sanity_tests():
    def b(seq,s,r,available,status='OK'):
        return dict(seq=seq,source_ms=s,received_ms=r,available=available,chain_source_max=s,chain_received_max=available,
           status=status,best_bid=.4,best_ask=.5,mid=.45,spread=.1,bid_qty=10,ask_qty=20,bid5=10,ask5=20,
           bid_total=10,ask_total=20,order_count=2)
    frame=dict(asset='TEST',regime='TEST',marketId=0,t=1000,postNet=5,postGross=15,preGross=14,buyOnly=True)
    row=join_frames([frame],[b(0,900,900,900),b(1,950,1100,1100)])[0]
    assert row['book_source_ms']==900 and row['sourceOnlyWouldUseUnreceived']
    assert join_frames([frame],[b(0,1000,900,1000)])[0]['book_status']=='NO_STRICT_PRIOR_BOOK'
    assert join_frames([frame],[b(0,900,900,900,'EMPTY_BOOK_SIDE')])[0]['book_status']=='EMPTY_BOOK_SIDE'
    assert not join_frames([frame],[b(0,900,900,900,'EMPTY_BOOK_SIDE')])[0]['fresh_3s']
    # A complete later checkpoint may be usable even if an earlier dependency arrives late.
    assert join_frames([frame],[b(0,800,1200,1200),b(1,950,950,950)])[0]['book_source_ms']==950
    assert row['feature_weak_ask']==.6 and row['feature_net_to_weak_ask5']==.5
    return 6


def run_group(group,smoke=False):
    started=time.monotonic();manifest=init();frames=state_rows(manifest)
    for p,h in manifest['sourceCode'].items():assert sha(p)==h,'audit code changed'
    chosen=[]
    for g in GROUPS:
        ms=[m for m in manifest['markets'] if m['group']==g]
        if smoke:chosen.append(ms[0])
        elif g==group:chosen+=ms
    if not smoke and not (OUT/'SMOKE.json').exists():raise ValueError('smoke required')
    name='SMOKE' if smoke else group;op=OUT/(name+'.json');rows_path=OUT/(name+'_JOINED.jsonl.gz')
    if op.exists():print(json.dumps(dict(reused=name,result=readjson(op)['summary']),ensure_ascii=False));return
    from tools.research_data_access_v1 import ResearchData
    with ResearchData() as access:
        access.con.execute('SET threads=1');access.con.execute("SET memory_limit='128MB'")
        assert sha(CACHE)==manifest['cache']['sha256']
        saved={}
        if not smoke:
            with gzip.open(OUT/'SMOKE_JOINED.jsonl.gz','rt',encoding='utf-8') as f:
                for line in f:r=json.loads(line);saved.setdefault(r['marketId'],[]).append(r)
        allrows=[];reports=[]
        for m in chosen:
            if time.monotonic()-started>14:raise TimeoutError('bounded group time cap; no automatic rerun')
            if m['marketId'] in saved:
                js=saved[m['marketId']];info={'reusedSmoke':True}
            else:
                bs,info,rawrows=get_book(m,access);js=join_frames(frames[m['marketId']],bs)
                if smoke and m['group']=='BTC_HISTORICAL':
                    p=Path('data/research/market_capsule_v1/source_bundle_50_v1/tapes')/(str(m['marketId'])+'.json.xz')
                    rb,ri,rr=raw_book(p,m['marketId']);assert len(rr)==len(rawrows)
                    for x,y in zip(rawrows,rr):
                        for k in ('source_ms','received_ms','best_bid','best_ask','bid_depth_total','ask_depth_total'):
                            assert x[k]==y[k] or (isinstance(x[k],(int,float)) and isinstance(y[k],(int,float)) and abs(x[k]-y[k])<1e-7),'cache/raw parity '+k
                        assert json.loads(x['top5_bids_json'])==json.loads(y['top5_bids_json'])
                        assert json.loads(x['top5_asks_json'])==json.loads(y['top5_asks_json'])
                    info['cacheVsRawParity']=True;info['rawParitySha256']=sha(p)
            assert len(js)==len(frames[m['marketId']])
            for a,r in zip(js,frames[m['marketId']]):
                assert a['stateSha256']==digest(r)
                assert all(a['label_'+k]==r.get(k) for k in LABELS)
            allrows+=js;reports.append(dict(marketId=m['marketId'],asset=m['asset'],regime=m['regime'],book=info,join=summarize(js)))
    with gzip.open(rows_path,'wt',encoding='utf-8',compresslevel=5) as f:
        for r in allrows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    result=dict(name=name,status='DATA_CAPTURE_WITH_EXPLICIT_MISSINGNESS_NOT_POLICY_PASS',
       summary=summarize(allrows),markets=reports,joined=dict(path=rows_path.as_posix(),sha256=sha(rows_path),bytes=rows_path.stat().st_size),
       seconds=time.monotonic()-started,unitChecks=sanity_tests(),newHFT=0,newTraining=0)
    writejson(op,result);print(json.dumps({k:result[k] for k in ('name','status','summary','seconds','unitChecks')},ensure_ascii=False))


def finalize():
    manifest=init();sources=[];sums={};per=[]
    for g in GROUPS:
        d=readjson(OUT/(g+'.json'));p=Path(d['joined']['path']);assert sha(p)==d['joined']['sha256']
        sources.append(d['joined']);sums[g]=d['summary'];per+=d['markets']
    assert len(per)==198 and sum(s['eventStates'] for s in sums.values())==14152
    op=OUT/'SUMMARY.json'
    if op.exists():print('FINAL_ALREADY_SAVED');return
    d=dict(status='PUBLIC_JOIN_CAPTURED_NOT_PRIVATE_INTENT_OR_STRATEGY_EQUIVALENCE',groups=sums,markets=198,eventStates=14152,
        sources=sources,perMarket=per,manifestSha256=sha(OUT/'MANIFEST.json'),newHFT=0,newTraining=0,
        sourceInterpretation='post-fill Target portfolio + prior-second public book; not Target decision time or OUR observable state',
        missingnessPolicy='all fixed markets/events retained; primary descriptive quality uses OK plus dual-clock age<=3s')
    writejson(op,d);print(json.dumps({k:d[k] for k in ('status','groups','markets','eventStates','newHFT','newTraining')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['init','smoke','group','finalize']);p.add_argument('--group',choices=GROUPS);a=p.parse_args()
    if a.stage=='init':d=init();print(json.dumps(dict(markets=len(d['markets']),unitChecks=sanity_tests(),manifest=str(OUT/'MANIFEST.json'))))
    elif a.stage=='smoke':run_group(None,smoke=True)
    elif a.stage=='group':run_group(a.group)
    else:finalize()
