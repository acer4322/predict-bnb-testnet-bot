from __future__ import annotations
import json, math, hashlib, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/DAGGER60_LOCAL_PENDING_FRESH101_RETEST_EXTREME_PACKET_V1_20260909'
TEST20=ROOT/'data/research/lan_worker_returns/eth-dagger60-local-pending-reservation-v1/result.json'
FRESH_HIST=ROOT/'data/research/lan_worker_returns/eth-dagger-fresh101-local-pending-v1/result.json'
FRESH_RETEST=ROOT/'data/research/lan_worker_returns/dagger60-lp-fresh101-retest-repro-20260909-v1/result.json'
EPS=1e-9

def load(p):
    return json.loads(p.read_text(encoding='utf-8'))

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def qtile(xs,q):
    if not xs:return None
    a=sorted(float(x) for x in xs)
    if len(a)==1:return a[0]
    z=(len(a)-1)*q; i=int(math.floor(z)); j=int(math.ceil(z))
    if i==j:return a[i]
    return a[i]+(a[j]-a[i])*(z-i)

def mdd(xs):
    c=0.; peak=0.; dd=0.
    for x in xs:
        c+=float(x); peak=max(peak,c); dd=max(dd,peak-c)
    return dd

def geom(r):
    a=float(r['pnl']); b=float(r['oppositePnl']); lo=min(a,b); hi=max(a,b)
    if lo>EPS:return 'BOTH_POSITIVE'
    if hi<-EPS:return 'BOTH_NEGATIVE'
    if lo<-EPS and hi>EPS:return 'MIXED_SIGN'
    return 'TOUCHES_ZERO'

def dom(r):
    u=float(r['up']);d=float(r['down'])
    if u>d+EPS:return 'UP'
    if d>u+EPS:return 'DOWN'
    return 'FLAT'

def row_detail(r):
    p=float(r['pnl']); op=float(r['oppositePnl']); buy=float(r['buyNotional'])
    d=dom(r)
    return {
        'marketId':int(r['marketId']),'winner':r['winner'],'pnl':p,'oppositePnl':op,
        'bestEndpoint':max(p,op),'worstEndpoint':min(p,op),'buyNotional':buy,
        'marketRoi':p/buy if buy>EPS else None,'pairCoverage':float(r['pairCoverage']),
        'floor':float(r['floor']),'absNet':float(r['absNet']),'submits':int(r['submits']),
        'fills':int(r['fills']),'up':float(r['up']),'down':float(r['down']),
        'duplicateBlocked':int(r.get('duplicateBlocked',0)),'localPendingBlocked':int(r.get('localPendingBlocked',0)),
        'targetPnl':float(r.get('targetPnl',0.)),'targetBuy':float(r.get('targetBuy',0.)),
        'targetMinusOurPnl':float(r.get('targetPnl',0.))-p,'dominantSide':d,
        'dominantMatchesWinner':None if d=='FLAT' else d==str(r['winner']).upper(),
        'endpointGeometry':geom(r),'oneSidedTerminal':min(float(r['up']),float(r['down']))<=EPS,
        'positiveFloor':float(r['floor'])>=-EPS,
    }

def summary(rows):
    ds=[row_detail(r) for r in rows]; ps=[x['pnl'] for x in ds]; buys=[x['buyNotional'] for x in ds]
    wins=[x for x in ps if x>EPS]; losses=[x for x in ps if x<-EPS]; pos=sum(wins); neg=-sum(losses)
    best=sorted(wins,reverse=True)
    total=sum(ps); active=[x for x in ds if x['buyNotional']>EPS]
    domrows=[x for x in ds if x['dominantMatchesWinner'] is not None]
    geometries={k:sum(x['endpointGeometry']==k for x in ds) for k in ['BOTH_POSITIVE','MIXED_SIGN','BOTH_NEGATIVE','TOUCHES_ZERO']}
    targetps=[x['targetPnl'] for x in ds]
    out={
      'markets':len(ds),'activeMarkets':len(active),'tradeCoverage':sum(x['fills']>0 for x in ds)/len(ds) if ds else None,
      'zeroFillMarkets':sum(x['fills']==0 for x in ds),'pnl':total,'buyNotional':sum(buys),'roi':total/sum(buys) if sum(buys)>EPS else None,
      'wins':len(wins),'losses':len(losses),'winRate':len(wins)/len(ds) if ds else None,
      'avgWin':statistics.mean(wins) if wins else None,'avgLoss':statistics.mean(losses) if losses else None,
      'payoffRatio':(statistics.mean(wins)/abs(statistics.mean(losses))) if wins and losses else None,
      'profitFactor':pos/neg if neg>EPS else None,'grossPositivePnl':pos,'grossNegativePnl':neg,
      'maxWin':max(ps) if ps else None,'maxLoss':min(ps) if ps else None,
      'leaveOneBestOutPnl':total-max(ps) if ps else None,
      'leaveTop3BestOutPnl':total-sum(sorted(ps,reverse=True)[:min(3,len(ps))]) if ps else None,
      'top1ShareOfGrossPositive':(best[0]/pos) if best and pos>EPS else None,
      'top3ShareOfGrossPositive':(sum(best[:3])/pos) if best and pos>EPS else None,
      'maxSequentialDrawdown':mdd(ps),'meanBuy':statistics.mean(buys) if buys else None,
      'meanPairCoverage':statistics.mean(x['pairCoverage'] for x in ds) if ds else None,
      'medianPairCoverage':statistics.median(x['pairCoverage'] for x in ds) if ds else None,
      'positiveFloorRate':sum(x['positiveFloor'] for x in ds)/len(ds) if ds else None,
      'meanAbsNet':statistics.mean(x['absNet'] for x in ds) if ds else None,
      'medianAbsNet':statistics.median(x['absNet'] for x in ds) if ds else None,
      'meanSubmits':statistics.mean(x['submits'] for x in ds) if ds else None,
      'meanFills':statistics.mean(x['fills'] for x in ds) if ds else None,
      'totalSubmits':sum(x['submits'] for x in ds),'totalFills':sum(x['fills'] for x in ds),
      'meanDuplicateBlocked':statistics.mean(x['duplicateBlocked'] for x in ds) if ds else None,
      'meanLocalPendingBlocked':statistics.mean(x['localPendingBlocked'] for x in ds) if ds else None,
      'oneSidedTerminalMarkets':sum(x['oneSidedTerminal'] for x in ds),
      'oneSidedTerminalRate':sum(x['oneSidedTerminal'] for x in ds)/len(ds) if ds else None,
      'dominantSideMatchesWinnerMarkets':sum(x['dominantMatchesWinner'] is True for x in domrows),
      'dominantSideMismatchMarkets':sum(x['dominantMatchesWinner'] is False for x in domrows),
      'dominantSideMatchRate':sum(x['dominantMatchesWinner'] is True for x in domrows)/len(domrows) if domrows else None,
      'endpointGeometryCounts':geometries,
      'aggregateBestEndpoint':sum(x['bestEndpoint'] for x in ds),'aggregateWorstEndpoint':sum(x['worstEndpoint'] for x in ds),
      'worstEndpointSingleMarket':min(x['worstEndpoint'] for x in ds) if ds else None,
      'targetAggregatePnl':sum(targetps),'targetWinRate':sum(x>EPS for x in targetps)/len(targetps) if targetps else None,
      'pnlQuantiles':{str(q):qtile(ps,q) for q in [0,0.05,0.1,0.25,0.5,0.75,0.9,0.95,1]},
      'absNetQuantiles':{str(q):qtile([x['absNet'] for x in ds],q) for q in [0,0.25,0.5,0.75,0.9,0.95,1]},
      'pairCoverageQuantiles':{str(q):qtile([x['pairCoverage'] for x in ds],q) for q in [0,0.25,0.5,0.75,0.9,0.95,1]},
      'lossThresholdCounts':{'>=1':sum(x<=-1 for x in ps),'>=2.5':sum(x<=-2.5 for x in ps),'>=5':sum(x<=-5 for x in ps),'>=10':sum(x<=-10 for x in ps),'>=15':sum(x<=-15 for x in ps)},
      'fillsOneMarketCount':sum(x['fills']==1 for x in ds),'fillsAtMost3MarketCount':sum(x['fills']<=3 for x in ds),
    }
    return out,ds

def blocks(rows,size=20):
    out=[]
    for i in range(0,len(rows),size):
        part=rows[i:i+size]; s,_=summary(part)
        out.append({'ordinalStart':i+1,'ordinalEnd':i+len(part),'firstMarketId':int(part[0]['marketId']),'lastMarketId':int(part[-1]['marketId']),**{k:s[k] for k in ['markets','pnl','winRate','roi','maxLoss','maxWin','leaveOneBestOutPnl','meanPairCoverage','meanAbsNet','positiveFloorRate','dominantSideMatchRate','targetAggregatePnl','targetWinRate']}})
    return out

def group_diag(ds,keyfn):
    groups={}
    for x in ds:
        k=str(keyfn(x)); groups.setdefault(k,[]).append(x)
    out={}
    for k,xs in groups.items():
        ps=[x['pnl'] for x in xs]
        out[k]={'n':len(xs),'pnl':sum(ps),'winRate':sum(p>EPS for p in ps)/len(xs),'avgPnl':statistics.mean(ps),'meanPairCoverage':statistics.mean(x['pairCoverage'] for x in xs),'meanAbsNet':statistics.mean(x['absNet'] for x in xs),'meanFills':statistics.mean(x['fills'] for x in xs),'meanLocalPendingBlocked':statistics.mean(x['localPendingBlocked'] for x in xs)}
    return out

def fmt(x,n=4):
    if x is None:return 'null'
    if isinstance(x,int):return str(x)
    return f'{x:.{n}f}'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    t=load(TEST20); h=load(FRESH_HIST); r=load(FRESH_RETEST)
    st,dt=summary(t['rows']); sf,df=summary(r['rows'])
    parity={'jsonExactEqual':h==r,'summaryExactEqual':h.get('summary')==r.get('summary'),'round1ExactEqual':h.get('round1Offline')==r.get('round1Offline'),'round2ExactEqual':h.get('round2Offline')==r.get('round2Offline'),'rowsExactEqual':h.get('rows')==r.get('rows'),'historicalFreshSha256':sha(FRESH_HIST),'retestFreshSha256':sha(FRESH_RETEST)}
    detail={
      'schema':'DAGGER60_LOCAL_PENDING_FRESH101_RETEST_EXTREME_DETAIL_V1','researchOnly':True,'runtimeAuthority':False,
      'candidate':'ETH_DAGGER60_LOCAL_PENDING_RESERVATION_V1','retestTarget':'ETH_DAGGER_FRESH101_LOCAL_PENDING_V1',
      'parity':parity,'test20SuccessSummaryDerived':st,'fresh101RetestSummaryDerived':sf,
      'freshMinusTest20':{k:(sf[k]-st[k] if isinstance(sf.get(k),(int,float)) and isinstance(st.get(k),(int,float)) and not isinstance(sf.get(k),bool) and not isinstance(st.get(k),bool) else None) for k in ['pnl','roi','winRate','profitFactor','payoffRatio','maxLoss','leaveOneBestOutPnl','maxSequentialDrawdown','meanPairCoverage','positiveFloorRate','meanAbsNet','meanSubmits','meanFills','dominantSideMatchRate']},
      'fresh101ChronologyBlocks20':blocks(r['rows'],20),
      'fresh101Groups':{
        'endpointGeometry':group_diag(df,lambda x:x['endpointGeometry']),
        'dominantMatch':group_diag(df,lambda x:'MATCH' if x['dominantMatchesWinner'] is True else 'MISMATCH' if x['dominantMatchesWinner'] is False else 'FLAT'),
        'localPendingExercise':group_diag(df,lambda x:'BLOCKED_GT0' if x['localPendingBlocked']>0 else 'BLOCKED_0'),
        'oneSidedTerminal':group_diag(df,lambda x:'ONE_SIDED' if x['oneSidedTerminal'] else 'TWO_SIDED'),
      },
      'fresh101Worst10':sorted(df,key=lambda x:x['pnl'])[:10],
      'fresh101Best10':sorted(df,key=lambda x:x['pnl'],reverse=True)[:10],
      'fresh101AllMarkets':df,
      'test20AllMarkets':[row_detail(x) for x in t['rows']],
      'offlineModel':{'test20Round1':t.get('round1Offline'),'test20Round2':t.get('round2Offline'),'freshRound1':r.get('round1Offline'),'freshRound2':r.get('round2Offline')},
      'boundaries':{'test20':t.get('boundary'),'fresh101':r.get('boundary')},
      'interpretationBoundary':['Fresh101 retest is a reproduction on already-consumed chronology-later data, not new unseen confirmation.','Winner/Target PnL are evaluation-only in the frozen runner; no Target trajectory/objective is used at Fresh101 runtime.','Reported PnL is the runner terminal gross accounting and is not a newly established full-net-after-all-costs claim.','Postprocess does not change policy or HFT behavior.']
    }
    (OUT/'DETAIL.json').write_text(json.dumps(detail,indent=2,ensure_ascii=False),encoding='utf-8')
    lines=[]
    lines += ['# DAgger60 Local Pending Reservation V1 — Fresh101 Exact Retest / Extreme Evidence Packet','', 'Date: 2026-09-09  ', 'Mode: **ordinary execution / realistic HFT reproduction / detailed postprocess**  ', 'Status: **EXACT_REPRODUCTION_PASS / GENERALIZATION_FAIL_CONFIRMED**','']
    lines += ['## 1. Exact reproduction','',f'- Historical Fresh101 result SHA256: `{parity["historicalFreshSha256"]}`',f'- Retest Fresh101 result SHA256: `{parity["retestFreshSha256"]}`',f'- JSON exact equal: **{parity["jsonExactEqual"]}**',f'- 101 market rows exact equal: **{parity["rowsExactEqual"]}**',f'- Round1 / Round2 offline metrics exact equal: **{parity["round1ExactEqual"]} / {parity["round2ExactEqual"]}**','']
    lines += ['## 2. TEST20 success vs Fresh101 retest','', '| Metric | TEST20 consumed success | Fresh101 chronology-later retest |','|---|---:|---:|']
    for label,key in [('Markets','markets'),('PnL','pnl'),('ROI','roi'),('Win rate','winRate'),('Profit factor','profitFactor'),('Avg win','avgWin'),('Avg loss','avgLoss'),('Payoff ratio','payoffRatio'),('Max win','maxWin'),('Max loss','maxLoss'),('LOBO PnL','leaveOneBestOutPnl'),('Max sequential DD','maxSequentialDrawdown'),('Trade coverage','tradeCoverage'),('Mean buy','meanBuy'),('Mean pair coverage','meanPairCoverage'),('Positive-floor rate','positiveFloorRate'),('Mean abs-net','meanAbsNet'),('Mean submits','meanSubmits'),('Mean fills','meanFills'),('Mean local-pending blocks','meanLocalPendingBlocked'),('Dominant-side match rate','dominantSideMatchRate'),('Aggregate best endpoint','aggregateBestEndpoint'),('Aggregate worst endpoint','aggregateWorstEndpoint')]:
        lines.append(f'| {label} | {fmt(st.get(key))} | {fmt(sf.get(key))} |')
    lines += ['', '## 3. Fresh101 chronology blocks','', '| Ordinals | Markets | PnL | WR | Max loss | Pair cov | Abs-net | Dominant match | Target PnL | Target WR |','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for b in detail['fresh101ChronologyBlocks20']:
        lines.append(f"| {b['ordinalStart']}-{b['ordinalEnd']} | {b['firstMarketId']}-{b['lastMarketId']} | {fmt(b['pnl'])} | {fmt(b['winRate'])} | {fmt(b['maxLoss'])} | {fmt(b['meanPairCoverage'])} | {fmt(b['meanAbsNet'])} | {fmt(b['dominantSideMatchRate'])} | {fmt(b['targetAggregatePnl'])} | {fmt(b['targetWinRate'])} |")
    lines += ['', '## 4. Fresh101 endpoint / residual anatomy','',f"- Endpoint geometry counts: `{json.dumps(sf['endpointGeometryCounts'],ensure_ascii=False)}`",f"- One-sided terminal markets: **{sf['oneSidedTerminalMarkets']}/{sf['markets']}**",f"- Dominant side matches realized winner: **{sf['dominantSideMatchesWinnerMarkets']}/{sf['dominantSideMatchesWinnerMarkets']+sf['dominantSideMismatchMarkets']} = {fmt(sf['dominantSideMatchRate'])}**",f"- Aggregate best endpoint: **{fmt(sf['aggregateBestEndpoint'])}**",f"- Aggregate worst endpoint: **{fmt(sf['aggregateWorstEndpoint'])}**",f"- Positive-floor rate: **{fmt(sf['positiveFloorRate'])}**",f"- Loss threshold counts: `{json.dumps(sf['lossThresholdCounts'],ensure_ascii=False)}`",'']
    lines += ['### Dominant-side correctness groups','', '| Group | n | PnL | WR | mean pair cov | mean abs-net | mean fills |','|---|---:|---:|---:|---:|---:|---:|']
    for k,v in detail['fresh101Groups']['dominantMatch'].items():lines.append(f"| {k} | {v['n']} | {fmt(v['pnl'])} | {fmt(v['winRate'])} | {fmt(v['meanPairCoverage'])} | {fmt(v['meanAbsNet'])} | {fmt(v['meanFills'])} |")
    lines += ['', '### Endpoint geometry groups','', '| Geometry | n | PnL | WR | mean pair cov | mean abs-net |','|---|---:|---:|---:|---:|---:|']
    for k,v in detail['fresh101Groups']['endpointGeometry'].items():lines.append(f"| {k} | {v['n']} | {fmt(v['pnl'])} | {fmt(v['winRate'])} | {fmt(v['meanPairCoverage'])} | {fmt(v['meanAbsNet'])} |")
    lines += ['', '## 5. Worst 10 Fresh101 markets','', '| marketId | winner | PnL | opposite | floor | abs-net | pair cov | fills | submits | dominant | correct? | local blocks | Target PnL |','|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|']
    for x in detail['fresh101Worst10']:
        lines.append(f"| {x['marketId']} | {x['winner']} | {fmt(x['pnl'])} | {fmt(x['oppositePnl'])} | {fmt(x['floor'])} | {fmt(x['absNet'])} | {fmt(x['pairCoverage'])} | {x['fills']} | {x['submits']} | {x['dominantSide']} | {x['dominantMatchesWinner']} | {x['localPendingBlocked']} | {fmt(x['targetPnl'])} |")
    lines += ['', '## 6. Best 10 Fresh101 markets','', '| marketId | winner | PnL | opposite | floor | abs-net | pair cov | fills | dominant | correct? | Target PnL |','|---:|---|---:|---:|---:|---:|---:|---:|---|---|---:|']
    for x in detail['fresh101Best10']:
        lines.append(f"| {x['marketId']} | {x['winner']} | {fmt(x['pnl'])} | {fmt(x['oppositePnl'])} | {fmt(x['floor'])} | {fmt(x['absNet'])} | {fmt(x['pairCoverage'])} | {x['fills']} | {x['dominantSide']} | {x['dominantMatchesWinner']} | {fmt(x['targetPnl'])} |")
    lines += ['', '## 7. What this retest establishes','', '- The historical Fresh101 failure is exactly reproducible under the frozen candidate; it is not a report-copy or one-off worker anomaly.', '- Local pre-ack reservation remains a valid responsibility/conservation fix, but it is not a complete profitable strategy.', '- The TEST20 success does not generalize to Fresh101: positive PnL, 60% WR and positive LOBO all collapse on chronology-later data.', '- This retest does **not** identify a new selector or repair rule. It should be used as evidence for mechanism diagnosis, not for post-result tuning on Fresh101.', '- Fresh101 is already consumed development evidence; no promotion/generalization claim may treat this rerun as fresh confirmation.', '', '## 8. What remains unproven / useful for Extreme', '', '- Whether the dominant failure is side/objective selection, downstream reauthorization, regime/path dependence, or another strict-past state representation problem requires a separate causal contrast.', '- Full-net-after-all-costs profitability remains outside this old runner; the gross Fresh101 failure is already negative before a new full-net claim.', '- No action-level authority genealogy is added by this retest. `DETAIL.json` contains all 101 terminal rows and derived diagnostics; if Extreme requires within-market event lineage, that should be requested as a bounded follow-up diagnostic rather than inferred from terminal aggregates.', '', '## 9. Files', '', '- `DETAIL.json` — all 101 retest markets plus all TEST20 markets with derived endpoint/dominant-side fields.', '- Retest raw result: `data/research/lan_worker_returns/dagger60-lp-fresh101-retest-repro-20260909-v1/result.json`.', '- Historical Fresh101 result: `data/research/lan_worker_returns/eth-dagger-fresh101-local-pending-v1/result.json`.', '- Original TEST20 success result: `data/research/lan_worker_returns/eth-dagger60-local-pending-reservation-v1/result.json`.']
    (OUT/'EXTREME_EVIDENCE_PACKET.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'out':str(OUT),'parity':parity,'test20':{k:st[k] for k in ['pnl','roi','winRate','leaveOneBestOutPnl','maxLoss','maxSequentialDrawdown','dominantSideMatchRate']},'fresh101':{k:sf[k] for k in ['pnl','roi','winRate','leaveOneBestOutPnl','maxLoss','maxSequentialDrawdown','dominantSideMatchRate','aggregateBestEndpoint','aggregateWorstEndpoint']}},indent=2))

if __name__=='__main__':main()
