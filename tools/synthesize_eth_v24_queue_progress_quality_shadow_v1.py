from __future__ import annotations
import argparse,json,math,statistics
from pathlib import Path
EPS=1e-9

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def block(rr):
 pos=[r for r in rr if float(r.get('initialDepth') or 0)>EPS]
 return {
  'orders':len(rr),'markets':len({int(r['marketId']) for r in rr}),
  'levelZeroRate':sum(bool(r.get('levelZeroSeen')) for r in rr)/len(rr) if rr else None,
  'everBehindRate':sum(bool(r.get('everBehind')) for r in rr)/len(rr) if rr else None,
  'maxBehindTicks':stats([r.get('maxBehindTicks') for r in rr]),
  'atBestReceiptFraction':stats([r.get('atBestReceiptFraction') for r in rr]),
  'publicLevelDepletionFraction':stats([r.get('publicLevelDepletionFraction') for r in rr]),
  'progressEvents':stats([r.get('progressEvents') for r in rr]),
  'extensions':stats([r.get('extensions') for r in rr]),
  'durationMs':stats([r.get('durationMs') for r in rr]),
  'positiveInitialDepthOrders':len(pos),
  'grossDepletionFractionPositiveInitial':stats([r.get('grossDepletionFraction') for r in pos]),
  'grossReplenishmentFractionPositiveInitial':stats([r.get('grossReplenishmentFraction') for r in pos]),
  'replenishmentToDepletionPositiveInitial':stats([r.get('replenishmentToDepletion') for r in pos]),
  'maxBehindLe1Rate':sum(float(r.get('maxBehindTicks') or 0)<=1.000001 for r in rr)/len(rr) if rr else None,
  'maxBehindLe3Rate':sum(float(r.get('maxBehindTicks') or 0)<=3.000001 for r in rr)/len(rr) if rr else None,
  'maxBehindGe10Rate':sum(float(r.get('maxBehindTicks') or 0)>=9.999999 for r in rr)/len(rr) if rr else None
 }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--audit-inputs',nargs='+',required=True);ap.add_argument('--baseline-inputs',nargs='+',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 audit=[];marketRows=[];target=None
 for p in a.audit_inputs:
  d=json.load(open(p,encoding='utf-8'));audit+=d.get('rows',[]);marketRows+=d.get('marketRows',[]);target=target or d.get('targetSuccessfulCheapPostfillReference')
 base=[]
 for p in a.baseline_inputs:base+=json.load(open(p,encoding='utf-8')).get('rows',[])
 bm={int(r['marketId']):r for r in base};am={int(r['marketId']):r for r in marketRows}
 mism=[]
 for mid,m in am.items():
  b=bm.get(mid,{}).get('V23',{}).get('functional',{})
  checks={'firstFills':(m.get('firstFills'),b.get('reserveFirstLegActualFill')),'cycles':(m.get('cycles'),b.get('reserveCycleCompletion')),'ceilingSubmits':(m.get('ceilingSubmits'),b.get('ceilingRepairSubmits')),'floorGain':(m.get('floorGain'),b.get('reserveCycleFloorGainTotal')),'pnl':(m.get('pnlDiagnosticOnly'),b.get('pnlDiagnosticOnly'))}
  bad={k:v for k,v in checks.items() if v[1] is None or abs(float(v[0])-float(v[1]))>1e-8}
  if bad:mism.append({'marketId':mid,'mismatch':bad})
 filled=[r for r in audit if r.get('filled')];unfilled=[r for r in audit if not r.get('filled')]
 failedMarketIds=sorted(mid for mid,m in am.items() if float(m.get('firstFills') or 0)>float(m.get('cycles') or 0))
 successMarketIds=sorted(mid for mid,m in am.items() if float(m.get('firstFills') or 0)>0 and float(m.get('cycles') or 0)>=float(m.get('firstFills') or 0))
 # Explicitly surface high-progress failures: progress alone must not be interpreted as executable frontier survival.
 highProgressUnfilled=sorted([{'marketId':int(r['marketId']),'key':r['key'],'progressEvents':int(r.get('progressEvents') or 0),'extensions':int(r.get('extensions') or 0),'maxBehindTicks':float(r.get('maxBehindTicks') or 0),'atBestReceiptFraction':float(r.get('atBestReceiptFraction') or 0),'publicLevelDepletionFraction':float(r.get('publicLevelDepletionFraction') or 0),'levelZeroSeen':bool(r.get('levelZeroSeen'))} for r in unfilled if int(r.get('progressEvents') or 0)>=3],key=lambda x:(-x['progressEvents'],x['marketId']))[:30]
 out={'version':'ETH_V24_QUEUE_PROGRESS_QUALITY_SHADOW_SYNTHESIS_V1','researchOnly':True,'behaviorChange':False,'markets':len(am),'marketIds':sorted(am),'behaviorFingerprintMismatches':mism,'behaviorFingerprintExact':len(mism)==0,'failedMarketIds':failedMarketIds,'fullyCompletedMarketIds':successMarketIds,'orders':len(audit),'filledOrders':len(filled),'unfilledOrders':len(unfilled),'quality':{'filled':block(filled),'unfilled':block(unfilled),'all':block(audit)},'highProgressUnfilledExamples':highProgressUnfilled,'targetSuccessfulCheapPostfillReference':target,'interpretation':[
  'Queue progress alone is insufficient if unfilled orders can continue receiving depth-decrease events while losing execution-frontier relevance.',
  'The next functional candidate should couple queue-priority preservation to both progress and frontier relevance/state validity; it should not be a fixed TTL or copied tick threshold.',
  'Target BTC/ETH successful second-leg rows are teacher context for normalized topology only; numeric distributions remain asset-specific.'
 ],'boundary':['All 40 chronology-forward markets through 1843049 are already consumed before this synthesis.','Audit instrumentation must reproduce frozen V23 behavior exactly before feature interpretation.','No PnL tuning and no new-market consumption.']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
