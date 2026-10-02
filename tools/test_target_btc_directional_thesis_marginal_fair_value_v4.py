from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.test_target_btc_eth_directional_thesis_authority_v1 import reconstruct_maker_events,label_one,fit_eval
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=BASE/'target_inventory_risk_fairvalue_v1.json';OUT=BASE/'TARGET_BTC_DIRECTIONAL_THESIS_MARGINAL_FAIR_VALUE_V4.json'

def main():
 d=json.load(open(SRC,encoding='utf-8'));rows=[r for r in d['rows'] if r.get('role')=='MAKER' and r.get('purpose')=='DOMINANT_ADD'];mids=np.asarray([int(r['marketId']) for r in rows],int);ev=reconstruct_maker_events('BTC',set(mids.tolist()));y=[]
 for r in rows:y.append(label_one(ev.get(int(r['marketId']),[]),int(r['t']),str(r['actionSide']).upper())[2])
 y=np.asarray(y,int)
 base=['absNet','secondsLeft','rv10MeanBps','avgDominantCost','riskPressure','riskSqrt'];avg=base+['fairDominantProb','fairEdgePerShare','directionalAlphaValue','directionAligned']
 # unique union with marginal quote-value representation
 vals=[]
 for r in rows:
  fair=float(r['fairDominantProb']);px=float(r['orderPx']);qty=float(r['orderQty']);marg=fair-px
  z=dict(r);z['marginalFairEdgePerShare']=marg;z['marginalOrderValue']=qty*marg;z['fairToQuoteRatio']=fair/max(px,.01);vals.append(z)
 marginal=base+['fairDominantProb','orderPx','orderQty','marginalFairEdgePerShare','marginalOrderValue','fairToQuoteRatio','directionAligned']
 combined=[]
 for k in avg+marginal:
  if k not in combined:combined.append(k)
 names=combined;X=np.asarray([[float(r.get(k)) if r.get(k) is not None and math.isfinite(float(r.get(k))) else np.nan for k in names] for r in vals],float);ix={k:i for i,k in enumerate(names)}
 groups={'RISK_BASE':base,'AVG_COST_FAIR':avg,'MARGINAL_QUOTE_FAIR':marginal,'COMBINED':combined};res={}
 for n,ff in groups.items():
  _,s,_=fit_eval(X,y,mids,[ix[k] for k in ff]);res[n]=s
 out={'version':'TARGET_BTC_DIRECTIONAL_THESIS_MARGINAL_FAIR_VALUE_V4','researchOnly':True,'actionAuthority':False,'coverage':{'rows':len(rows),'markets':len(set(mids.tolist()))},'groups':res,'testAucDeltas':{'AVG_vs_RISK':res['AVG_COST_FAIR']['test']['auc']-res['RISK_BASE']['test']['auc'],'MARGINAL_vs_RISK':res['MARGINAL_QUOTE_FAIR']['test']['auc']-res['RISK_BASE']['test']['auc'],'MARGINAL_vs_AVG':res['MARGINAL_QUOTE_FAIR']['test']['auc']-res['AVG_COST_FAIR']['test']['auc'],'COMBINED_vs_AVG':res['COMBINED']['test']['auc']-res['AVG_COST_FAIR']['test']['auc']},'testValueAnatomy':{},'guards':{'winnerUsed':False,'pnlUsed':False,'futureFeatureUsed':False,'behaviorChanged':False}}
 # held-out anatomy under same market split
 from tools.test_target_btc_eth_directional_thesis_authority_v1 import split_markets
 tr,va,te=split_markets(mids);mask=np.asarray([int(m) in te for m in mids]);
 for k in ['fairDominantProb','orderPx','marginalFairEdgePerShare','marginalOrderValue','fairEdgePerShare','directionalAlphaValue']:
  a=np.asarray([float(z[k]) for z in vals]);out['testValueAnatomy'][k]={'positiveMedian':float(np.median(a[mask&(y==1)])),'negativeMedian':float(np.median(a[mask&(y==0)]))}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':out['coverage'],'test':{k:v['test'] for k,v in res.items()},'deltas':out['testAucDeltas'],'anatomy':out['testValueAnatomy']},indent=2),flush=True)
if __name__=='__main__':main()
