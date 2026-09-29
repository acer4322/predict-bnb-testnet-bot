from __future__ import annotations
import json,sqlite3,bisect,math,statistics
from pathlib import Path
ROOT=Path.cwd().resolve();P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'TARGET_ETH_REPAIR_TAKER_PASSIVE_OPTION_EXHAUSTION_ANATOMY_V1.json';FC=P/'eth_fresh150_inference_compact_v1.db';OUT=P/'TARGET_ETH_REPAIR_TAKER_PARALLEL_MAKER_REMAINDER_V1.json'

def qt(xs,q):
 y=sorted(float(v) for v in xs if v is not None and math.isfinite(float(v)))
 if not y:return None
 z=(len(y)-1)*q;i=int(z);j=min(i+1,len(y)-1);w=z-i;return y[i]*(1-w)+y[j]*w

def st(xs):
 y=[float(v) for v in xs if v is not None and math.isfinite(float(v))]
 return {'n':len(y),'mean':statistics.mean(y) if y else None,'median':statistics.median(y) if y else None,'p25':qt(y,.25),'p75':qt(y,.75),'p90':qt(y,.9)}
def main():
 d=json.loads(SRC.read_text(encoding='utf-8'));rows=[dict(x) for x in d['rows'] if x['role']=='REPAIR' and float(x.get('preAbsNet') or 0)>1e-9]
 con=sqlite3.connect(f'file:{FC.resolve().as_posix()}?mode=ro',uri=True);ev={}
 for m,s,t,q in con.execute("select market_id,side,event_ms,shares from maker_book_inference_wallet_events where role='MAKER' order by market_id,event_ms"):
  ev.setdefault((int(m),str(s)),[]).append((int(t),float(q)))
 con.close()
 for r in rows:
  gap=float(r['preAbsNet']);q=float(r['shares']);r['repairFraction']=min(q,gap)/gap;r['rawOverResidual']=q>gap+1e-9;r['fullResidualOrMore']=q>=gap-1e-9
  arr=ev.get((int(r['marketId']),str(r['side'])),[]);ts=[x[0] for x in arr];i=bisect.bisect_right(ts,int(r['t']));nxt=arr[i][0] if i<len(arr) else None;r['nextSameSideMakerFillDelayMs']=None if nxt is None else nxt-int(r['t'])
  r['parallelMaker']=int(r.get('activeDistinct_pre') or 0)>=1;r['parallelMaker2Plus']=int(r.get('activeDistinct_pre') or 0)>=2
 def block(z):
  delays=[x['nextSameSideMakerFillDelayMs'] for x in z if x['nextSameSideMakerFillDelayMs'] is not None]
  return {'n':len(z),'markets':len({x['marketId'] for x in z}),'repairFraction':st([x['repairFraction'] for x in z]),'fullResidualOrMoreRate':sum(x['fullResidualOrMore'] for x in z)/len(z) if z else None,'rawOverResidualRate':sum(x['rawOverResidual'] for x in z)/len(z) if z else None,'nextSameSideMakerFillObservedRate':len(delays)/len(z) if z else None,'nextSameSideMakerFillDelayMs':st(delays),'makerFillWithin1sRate':sum(d<=1000 for d in delays)/len(z) if z else None,'makerFillWithin3sRate':sum(d<=3000 for d in delays)/len(z) if z else None,'makerFillWithin10sRate':sum(d<=10000 for d in delays)/len(z) if z else None}
 par=[x for x in rows if x['parallelMaker']];none=[x for x in rows if not x['parallelMaker']];par2=[x for x in rows if x['parallelMaker2Plus']];first=[x for x in rows if x.get('firstRepairTakerInMarket')]
 out={'version':'TARGET_ETH_REPAIR_TAKER_PARALLEL_MAKER_REMAINDER_V1','researchOnly':True,'actionAuthority':False,'coverage':{'repairTakerParents':len(rows),'parallelMakerParents':len(par),'parallelMaker2PlusParents':len(par2),'noParallelMakerParents':len(none)},'summary':{'PARALLEL_MAKER':block(par),'NO_PARALLEL_MAKER':block(none),'PARALLEL_2PLUS':block(par2),'FIRST_REPAIR_PER_MARKET':block(first)},'rows':rows,'boundary':['parallel Maker is retrospective active-parent proxy; not literal private order continuity proof','repair fraction is descriptive Target sizing anatomy only','next Maker fill is official fresh wallet Maker fill after Taker timestamp','no PnL tuning, no runtime change, no BTC numeric transfer']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT),'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
