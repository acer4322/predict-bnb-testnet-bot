from __future__ import annotations
import argparse,json,sqlite3,statistics,math,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import analyze_target_btc_eth_second_leg_price_time_lifecycle_v2 as v2
EPS=1e-9

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=json.load(open(a.source,encoding='utf-8')); rows=[r for r in src['rows'] if r.get('priceTimeResolved') and r.get('cheapPair') and r.get('placementMechanism')=='POSTFILL_NEW' and int(r.get('secondRestMs') or 0)>=3000]
 by=defaultdict(list)
 for r in rows:by[(r['asset'],int(r['marketId']))].append(r)
 out=[]
 for asset in ('BTC','ETH'):
  c=sqlite3.connect(f'file:{v2.BOOKS[asset].resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
  try:
   for _,mid in sorted(k for k in by if k[0]==asset):
    eps=by[(asset,mid)]; lo=min(int(r['placementReadyMs']) for r in eps)-1000; hi=max(min(int(r['secondEventMs']),int(r['placementReadyMs'])+10000) for r in eps)+500
    states=v2.build_market_states(c,mid,lo,hi)
    for r in eps:
     ready=int(r['placementReadyMs']); fill=int(r['secondEventMs']); side=r['secondSide']; p=float(r['secondPrice']); key,np=v2.native_level(side,p)
     path=[]
     for t,bids,asks in states:
      if t<ready or t>min(fill,ready+10000):continue
      book={'bids':bids,'asks':asks}; bb=v2.target_bid(book,side)
      if bb is None:continue
      depth=float((bids if key=='bids' else asks).get(np,0.0)); path.append((int(t),float(bb),depth))
     if not path:continue
     init=path[0][2]
     def snap(ms):
      cand=[x for x in path if x[0]<=ready+ms]
      if not cand:return None
      t,bb,d=cand[-1]; dep=max(0.0,init-min(x[2] for x in cand))/max(init,EPS); behind=max(0.0,(bb-p)/.01)
      return {'t':t,'behindTicks':behind,'depletionFraction':dep,'depth':d,'ageMs':t-ready,'causalStall':bool(behind>=5-EPS and dep<=1e-12 and t-ready>=ms-500)}
     out.append({'asset':asset,'marketId':mid,'placementReadyMs':ready,'fillMs':fill,'secondRestMs':int(r['secondRestMs']),'terminalMaxBehindTicks':r['maxBehindTicks'],'terminalDepletion':r['publicLevelDepletionFraction'],'s3':snap(3000),'s5':snap(5000)})
  finally:c.close()
 def block(asset,field):
  z=[r for r in out if r['asset']==asset and r.get(field)]
  return {'n':len(z),'causalStall':sum(bool(r[field]['causalStall']) for r in z),'causalStallRate':sum(bool(r[field]['causalStall']) for r in z)/len(z) if z else None,'behindMedian':statistics.median(r[field]['behindTicks'] for r in z) if z else None,'depletionMedian':statistics.median(r[field]['depletionFraction'] for r in z) if z else None}
 res={'version':'TARGET_SECOND_LEG_CAUSAL_STALL_3S_V1','researchOnly':True,'source':a.source,'population':'successful Target cheap POSTFILL_NEW second legs with rest>=3s','summary':{'BTC_3s':block('BTC','s3'),'BTC_5s':block('BTC','s5'),'ETH_3s':block('ETH','s3'),'ETH_5s':block('ETH','s5')},'rows':out,'decision':None,'boundary':['strict-past receipt-clock book state at decision horizon','successful actual Target fill is evaluation label only','no winner/PnL','no threshold sweep']}
 btc3=res['summary']['BTC_3s']; res['decision']='REJECT_3S_HARD_STALL_IF_FALSE_POSITIVE_NONZERO' if btc3['causalStall']>0 else '3S_CAUSAL_CROSSCHECK_CLEAN'
 Path(a.output).write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps({'summary':res['summary'],'decision':res['decision']},ensure_ascii=False))
if __name__=='__main__':main()
