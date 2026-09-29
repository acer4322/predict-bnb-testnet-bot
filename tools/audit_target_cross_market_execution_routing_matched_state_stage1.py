import csv,json,math,statistics,hashlib
from collections import defaultdict
IN=r"data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv"
OUT=r"data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_MARKET_EXECUTION_ROUTING_MATCHED_STATE_STAGE1_RESULT_20260909.json"
FRAMES=["BTC5M","BTC15M","BTC1H","ETH5M"]
PB=[0,1/3,2/3,1.0000001]; CB=[0,.70,.90,1.0000001]; MB=[0,.55,.70,1.0000001]
def bindex(x,b):
    for i in range(len(b)-1):
        if b[i] <= x < b[i+1]: return i
    return None
def fnum(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except: return None
rows=list(csv.DictReader(open(IN,encoding="utf-8",newline="")))
counts=defaultdict(lambda:defaultdict(lambda:[0,0]))
elig=defaultdict(int)
raw=defaultdict(lambda:[0,0])
for r in rows:
    fr=r.get('frame'); role=r.get('current_class'); route=r.get('current_route')
    if fr not in FRAMES or role not in ('REPAIR','EXPAND') or route not in ('MAKER','TAKER'): continue
    raw[fr][0]+=1; raw[fr][1]+=route=='TAKER'
    ph=fnum(r.get('phase')); pc=fnum(r.get('paired_coverage')); dm=fnum(r.get('dominant_mid'))
    if None in (ph,pc,dm): continue
    bi=(bindex(ph,PB),bindex(pc,CB),bindex(dm,MB))
    if None in bi: continue
    key=(role,)+bi
    elig[fr]+=1
    counts[key][fr][0]+=1; counts[key][fr][1]+=route=='TAKER'
matched=[]
for key,d in counts.items():
    if all(d[fr][0]>=8 for fr in FRAMES): matched.append(key)
matched=sorted(matched)
cellinfo=[]
for key in matched:
    d=counts[key]; rates={fr:d[fr][1]/d[fr][0] for fr in FRAMES}; ns={fr:d[fr][0] for fr in FRAMES}
    cellinfo.append({'key':{'role':key[0],'phaseBin':key[1],'paircovBin':key[2],'dominantMidBin':key[3]},'n':ns,'takerRate':rates,'spread':max(rates.values())-min(rates.values()),'pooledN':sum(ns.values())})
totpool=sum(x['pooledN'] for x in cellinfo)
std_pooled={fr:sum((x['pooledN']/totpool)*x['takerRate'][fr] for x in cellinfo) if totpool else None for fr in FRAMES}
std_equal={fr:statistics.mean(x['takerRate'][fr] for x in cellinfo) if cellinfo else None for fr in FRAMES}
spreads=[x['spread'] for x in cellinfo]
wm_spread=sum(x['pooledN']*x['spread'] for x in cellinfo)/totpool if totpool else None
coverage={fr:sum(counts[k][fr][0] for k in matched)/elig[fr] if elig[fr] else None for fr in FRAMES}
def role_summary(role):
    xs=[x for x in cellinfo if x['key']['role']==role]; tp=sum(x['pooledN'] for x in xs)
    return {'cells':len(xs),'pooledRows':tp,
      'stdPooled':{fr:sum((x['pooledN']/tp)*x['takerRate'][fr] for x in xs) if tp else None for fr in FRAMES},
      'stdEqual':{fr:statistics.mean(x['takerRate'][fr] for x in xs) if xs else None for fr in FRAMES},
      'weightedMeanSpread':sum(x['pooledN']*x['spread'] for x in xs)/tp if tp else None,
      'medianSpread':statistics.median([x['spread'] for x in xs]) if xs else None}
res={'version':'TARGET_CROSS_MARKET_EXECUTION_ROUTING_MATCHED_STATE_STAGE1','input':IN,'inputSha256':hashlib.sha256(open(IN,'rb').read()).hexdigest(),
 'rowCount':len(rows),'frames':FRAMES,
 'rawTakerRate':{fr:raw[fr][1]/raw[fr][0] for fr in FRAMES},'eligibleRows':dict(elig),'matchedCellN':len(cellinfo),'matchedCoverage':coverage,
 'stdPooledTakerRate':std_pooled,'stdEqualCellTakerRate':std_equal,'weightedMeanWithinCellSpread':wm_spread,'medianWithinCellSpread':statistics.median(spreads) if spreads else None,
 'p90WithinCellSpread':sorted(spreads)[max(0,math.ceil(.9*len(spreads))-1)] if spreads else None,
 'role':{'REPAIR':role_summary('REPAIR'),'EXPAND':role_summary('EXPAND')},'cells':cellinfo,
 'boundary':['descriptive conditional invariance only','no winner/pnl/settlement','no policy authority','ETH15M absent from source slice']}
open(OUT,'w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps({k:res[k] for k in ['rawTakerRate','matchedCellN','matchedCoverage','stdPooledTakerRate','stdEqualCellTakerRate','weightedMeanWithinCellSpread','medianWithinCellSpread','p90WithinCellSpread','role']},ensure_ascii=False,indent=2))
