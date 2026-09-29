from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np

def summarize(rows,key):
    rr=sorted(rows,key=lambda r:int(r['resolvedAtMs']))
    pnl=np.asarray([float(r[key]['pnlUsdt']) for r in rr],float)
    eq=np.cumsum(pnl); peak=np.maximum.accumulate(np.r_[0.0,eq]); dd=np.r_[0.0,eq]-peak
    maker=np.asarray([float(r[key].get('makerFilledShares') or 0) for r in rr]); taker=np.asarray([float(r[key].get('takerFilledShares') or 0) for r in rr])
    floors=np.asarray([float(r[key].get('finalFloor') or 0) for r in rr]); absn=np.asarray([float(r[key].get('finalAbsNet') or 0) for r in rr])
    return {'markets':len(rr),'totalPnl':float(pnl.sum()),'winRate':float(np.mean(pnl>0)),'nonLossRate':float(np.mean(pnl>=0)),'meanPnl':float(pnl.mean()),'medianPnl':float(np.median(pnl)),'pnlStd':float(pnl.std()),'maxWin':float(pnl.max()),'maxLoss':float(pnl.min()),'maxDrawdown':float(dd.min()),'worstFinalFloor':float(floors.min()),'medianFinalFloor':float(np.median(floors)),'meanFinalAbsNet':float(absn.mean()),'maxFinalAbsNet':float(absn.max()),'makerFilledShares':float(maker.sum()),'takerFilledShares':float(taker.sum()),'makerFillEvents':int(sum(int(r[key].get('makerFillEvents') or 0) for r in rr)),'takerFills':int(sum(int(r[key].get('takerFills') or 0) for r in rr)),'activeMarkets':int(sum((r[key].get('makerFillEvents') or 0)>0 or (r[key].get('takerFills') or 0)>0 for r in rr)),'timeline':[{'marketId':int(r['marketId']),'resolvedAtMs':int(r['resolvedAtMs']),'pnl':float(r[key]['pnlUsdt'])} for r in rr]}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();p=Path(a.input);o=json.loads(p.read_text(encoding='utf-8')); rows=[r for r in o['rows'] if 'error' not in r]
    r3=summarize(rows,'R3'); cand=summarize(rows,'candidate')
    ret=lambda x,y: float(x/y) if abs(y)>1e-12 else math.nan
    rep={'version':'R4_CURRENT_VS_R3_STABILITY_V1','source':str(p),'R3':r3,'candidate':cand,'delta':{'totalPnl':cand['totalPnl']-r3['totalPnl'],'winRate':cand['winRate']-r3['winRate'],'maxLoss':cand['maxLoss']-r3['maxLoss'],'maxDrawdown':cand['maxDrawdown']-r3['maxDrawdown'],'worstFinalFloor':cand['worstFinalFloor']-r3['worstFinalFloor'],'meanFinalAbsNet':cand['meanFinalAbsNet']-r3['meanFinalAbsNet'],'makerShareRetention':ret(cand['makerFilledShares'],r3['makerFilledShares']),'takerShareRetention':ret(cand['takerFilledShares'],r3['takerFilledShares']),'activeMarketRetention':ret(cand['activeMarkets'],r3['activeMarkets'])},'perMarket':[{'marketId':r['marketId'],'r3Pnl':r['R3']['pnlUsdt'],'candidatePnl':r['candidate']['pnlUsdt'],'deltaPnl':r['deltaPnl'],'r3MakerShares':r['R3']['makerFilledShares'],'candidateMakerShares':r['candidate']['makerFilledShares'],'r3TakerShares':r['R3']['takerFilledShares'],'candidateTakerShares':r['candidate']['takerFilledShares'],'crossOverrides':(r.get('mgmtStats') or {}).get('crossOverrides',0)} for r in rows]}
    Path(a.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
