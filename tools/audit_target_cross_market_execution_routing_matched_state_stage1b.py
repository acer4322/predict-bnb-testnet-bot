import csv,json,math,statistics,hashlib
from collections import defaultdict
IN=r"data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv"
OUT=r"data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_MARKET_EXECUTION_ROUTING_MATCHED_STATE_STAGE1B_RESULT_20260909.json"
FRAMES=["BTC5M","BTC15M","BTC1H","ETH5M"]
def fn(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except:return None
rows=list(csv.DictReader(open(IN,encoding='utf-8',newline='')))
counts=defaultdict(lambda:defaultdict(lambda:[0,0])); elig=defaultdict(int); raw=defaultdict(lambda:[0,0])
for r in rows:
    fr=r.get('frame'); role=r.get('current_class'); route=r.get('current_route')
    if fr not in FRAMES or role not in ('REPAIR','EXPAND') or route not in ('MAKER','TAKER'): continue
    raw[fr][0]+=1; raw[fr][1]+=route=='TAKER'
    ph=fn(r.get('phase')); pc=fn(r.get('paired_coverage')); sp=fn(r.get('spread_ticks')); di=fn(r.get('dominant_depth_imbalance')); pr=fn(r.get('prev_route_is_active'))
    if None in (ph,pc,sp,di,pr): continue
    key=(role, int(ph>=0.5), int(pc>=0.9), int(sp>1.5), int(di>=0), int(pr>=0.5))
    elig[fr]+=1; counts[key][fr][0]+=1; counts[key][fr][1]+=route=='TAKER'
matched=sorted(k for k,d in counts.items() if all(d[fr][0]>=8 for fr in FRAMES))
cellinfo=[]
for k in matched:
    d=counts[k]; rates={fr:d[fr][1]/d[fr][0] for fr in FRAMES}; ns={fr:d[fr][0] for fr in FRAMES}
    cellinfo.append({'key':{'role':k[0],'lateHalf':k[1],'highPairCov':k[2],'wideSpread':k[3],'nonnegDepthImbalance':k[4],'prevRouteActive':k[5]},'n':ns,'takerRate':rates,'spread':max(rates.values())-min(rates.values()),'pooledN':sum(ns.values())})
tot=sum(x['pooledN'] for x in cellinfo)
def sm(xs):
    tp=sum(x['pooledN'] for x in xs)
    return {'cells':len(xs),'pooledRows':tp,'stdPooled':{fr:sum((x['pooledN']/tp)*x['takerRate'][fr] for x in xs) if tp else None for fr in FRAMES},'stdEqual':{fr:statistics.mean(x['takerRate'][fr] for x in xs) if xs else None for fr in FRAMES},'weightedMeanSpread':sum(x['pooledN']*x['spread'] for x in xs)/tp if tp else None,'medianSpread':statistics.median([x['spread'] for x in xs]) if xs else None}
res={'version':'TARGET_CROSS_MARKET_EXECUTION_ROUTING_MATCHED_STATE_STAGE1B','input':IN,'inputSha256':hashlib.sha256(open(IN,'rb').read()).hexdigest(),'rowCount':len(rows),'frames':FRAMES,'rawTakerRate':{fr:raw[fr][1]/raw[fr][0] for fr in FRAMES},'eligibleRows':dict(elig),'matchedCellN':len(cellinfo),'matchedCoverage':{fr:sum(counts[k][fr][0] for k in matched)/elig[fr] if elig[fr] else None for fr in FRAMES},'stdPooledTakerRate':sm(cellinfo)['stdPooled'],'stdEqualCellTakerRate':sm(cellinfo)['stdEqual'],'weightedMeanWithinCellSpread':sm(cellinfo)['weightedMeanSpread'],'medianWithinCellSpread':sm(cellinfo)['medianSpread'],'role':{'REPAIR':sm([x for x in cellinfo if x['key']['role']=='REPAIR']),'EXPAND':sm([x for x in cellinfo if x['key']['role']=='EXPAND'])},'cells':cellinfo,'boundary':['descriptive conditional invariance only','no winner/pnl/settlement','no policy authority','ETH15M absent from source slice']}
open(OUT,'w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps({k:res[k] for k in ['matchedCellN','matchedCoverage','stdPooledTakerRate','stdEqualCellTakerRate','weightedMeanWithinCellSpread','medianWithinCellSpread','role']},ensure_ascii=False,indent=2))
