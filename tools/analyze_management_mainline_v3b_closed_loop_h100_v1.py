from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
POLICIES=('NATIVE','ALWAYS_REPAIR','ALWAYS_REEXPAND','ALTERNATE_ROLE')
def maxdd(x):
 s=0.;peak=0.;dd=0.
 for v in x:s+=float(v);peak=max(peak,s);dd=max(dd,peak-s)
 return float(dd)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inputs',nargs='+',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
 for p in a.inputs: rows += json.loads(Path(p).read_text(encoding='utf-8'))['rows']
 rows=sorted(rows,key=lambda r:int(r['marketId'])); base=np.array([r['policies']['NATIVE']['metrics']['pnl'] for r in rows],float); outm={}
 for pol in POLICIES:
  pnl=np.array([r['policies'][pol]['metrics']['pnl'] for r in rows],float);fills=np.array([r['policies'][pol]['metrics']['fills'] for r in rows],float);alt=np.array([r['policies'][pol]['metrics']['alternations'] for r in rows],float);delta=pnl-base;aff=np.abs(delta)>1e-9
  vals={
   'markets':len(rows),'totalPnl':float(pnl.sum()),'meanPnl':float(pnl.mean()),'winRate':float((pnl>0).mean()),'worstPnl':float(pnl.min()),'bestPnl':float(pnl.max()),'maxDrawdown':maxdd(pnl),'worst20Mean':float(np.mean(np.sort(pnl)[:max(1,int(math.ceil(.2*len(pnl))))])),'fillsPerMarket':float(fills.mean()),'alternationsPerMarket':float(alt.mean()),'affectedMarkets':int(aff.sum()),'improvedVsNative':int((delta>1e-9).sum()),'worsenedVsNative':int((delta<-1e-9).sum()),'tiesVsNative':int((abs(delta)<=1e-9).sum()),'affectedImprovementRate':float(((delta>1e-9)&aff).sum()/aff.sum()) if aff.sum() else None,'deltaTotalPnl':float(delta.sum()),'deltaP10':float(np.quantile(delta,.1)),'deltaMedian':float(np.median(delta)),'deltaP90':float(np.quantile(delta,.9))}
  outm[pol]=vals
 b=outm['NATIVE']; gates={}
 for pol in POLICIES[1:]:
  z=outm[pol];gates[pol]={'affectedImprovementRate70':bool((z['affectedImprovementRate'] or 0)>=.70),'totalPnlImproves':bool(z['totalPnl']>b['totalPnl']+1e-9),'winRateNonWorse':bool(z['winRate']>=b['winRate']-1e-12),'worstMarketWithin10Pct':bool(z['worstPnl']>=b['worstPnl']-max(1.0,abs(b['worstPnl'])*.10)),'worst20MeanWithin10Pct':bool(z['worst20Mean']>=b['worst20Mean']-max(.5,abs(b['worst20Mean'])*.10)),'maxDDWithin110Pct':bool(z['maxDrawdown']<=b['maxDrawdown']*1.10+1e-9),'fillActivityAtLeast80Pct':bool(z['fillsPerMarket']>=b['fillsPerMarket']*.80-1e-9)};gates[pol]['allPass']=all(gates[pol].values())
 out={'version':'MANAGEMENT_MAINLINE_V3B_CLOSED_LOOP_H100_ANALYSIS_V1_20260907','researchOnly':True,'runtimeAuthority':False,'allSafetyPass':all(all(r['checks'].values()) for r in rows),'markets':len(rows),'policies':outm,'promotionGates':gates,'boundary':['diagnostic extreme policies only','70/30 gate applies to behavior-changing markets','whole-cohort PnL/WR/tail/DD/activity all required','no policy is promoted by this diagnostic alone']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allSafetyPass':out['allSafetyPass'],'policies':outm,'promotionGates':gates},ensure_ascii=False))
if __name__=='__main__':main()
