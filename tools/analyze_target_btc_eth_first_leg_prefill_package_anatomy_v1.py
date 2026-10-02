from __future__ import annotations
import argparse,json,sqlite3,math,statistics,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_btc_eth_second_leg_execution_mechanism_v1 as mech
from tools import analyze_target_btc_eth_frontier_viability_v3 as fv
EPS=1e-9;GRID=.01
TARGET=ROOT/'data/target_wallet_official_v1.db';BOOKS=mech.BOOKS

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def legal_metric(bids,asks,side,first_price):
 bb=fv.target_bid(bids,asks,side);ba=fv.target_ask(bids,asks,side)
 if bb is None or ba is None:return None
 ceiling=1.0-float(first_price)-.01;raw=min(ceiling,float(ba)-.01);p=math.floor((raw+1e-10)*100)/100
 if p<=0:return None
 return {'bestBid':bb,'bestAsk':ba,'ceiling':ceiling,'legalPrice':p,'behind':max(0.,(bb-p)/GRID)}

def first_parent(tc,placements,asset,mid,ep):
 rr=tc.execute("select order_hash from wallet_shadow_target_events where asset=? and market_id=? and role='MAKER' and quote_type='BID' and event_ms=? and side=? and abs(price-?)<1e-9 order by id limit 1",(asset,mid,int(ep['firstEventMs']),str(ep['firstSide']),float(ep['firstPrice']))).fetchone()
 if rr is None:return None
 return placements.get(str(rr[0] or '').lower())

def block(rr):
 return {'episodes':len(rr),'markets':len({r['marketId'] for r in rr}),'firstLegRestMs':stats([r['firstLegRestMs'] for r in rr]),'firstPlacementBehindTicks':stats([r['firstPlacementBehindTicks'] for r in rr]),'firstAtBestReceiptFraction':stats([r['firstAtBestReceiptFraction'] for r in rr]),'oppPlacementLegalBehindTicks':stats([r['oppPlacementLegalBehindTicks'] for r in rr]),'oppFillLegalBehindTicks':stats([r['oppFillLegalBehindTicks'] for r in rr]),'oppWithin1ReceiptFraction':stats([r['oppWithin1ReceiptFraction'] for r in rr]),'oppPathBreakRate':sum(r['oppPathBreakSeen'] for r in rr)/len(rr) if rr else None,'oppBreakToFirstFillMs':stats([r['oppBreakToFirstFillMs'] for r in rr]),'firstEverBehindRate':sum(r['firstEverBehind'] for r in rr)/len(rr) if rr else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();src=json.load(open(a.source,encoding='utf-8'))
 base=[r for r in src['rows'] if r.get('cheapPair') and r.get('priceTimeResolved')]
 by=defaultdict(list)
 for r in base:by[(r['asset'],int(r['marketId']))].append(r)
 tc=sqlite3.connect(f'file:{TARGET.resolve().as_posix()}?mode=ro',uri=True);tc.row_factory=sqlite3.Row;rows=[]
 try:
  for asset in ('BTC','ETH'):
   bc=sqlite3.connect(f'file:{BOOKS[asset].resolve().as_posix()}?mode=ro',uri=True);bc.row_factory=sqlite3.Row
   try:
    keys=sorted(k for k in by if k[0]==asset)
    for ii,(_,mid) in enumerate(keys,1):
     eps=by[(asset,mid)];adds=mech.load_adds(bc,mid);placements=mech.reconstruct_parent_placements(bc,tc,asset,mid,adds)
     candidates=[]
     for ep in eps:
      pr=first_parent(tc,placements,asset,mid,ep)
      if not pr or not pr.get('highConfidencePlacement') or pr.get('placementReadyMs') is None:continue
      candidates.append((ep,pr))
     if not candidates:continue
     lo=min(int(pr['placementReadyMs']) for ep,pr in candidates)-1000;hi=max(int(ep['firstEventMs']) for ep,pr in candidates)+1000;states=fv.load_states(bc,mid,lo,hi)
     for ep,pr in candidates:
      ready=int(pr['placementReadyMs']);fill=int(ep['firstEventMs']);fs=str(ep['firstSide']);opp='DOWN' if fs=='UP' else 'UP';fp=float(ep['firstPrice']);path=[x for x in states if ready<=x[0]<=fill]
      if not path:continue
      first_beh=[];opp_beh=[];opp_break=[]
      for tt,bids,asks in path:
       fbb=fv.target_bid(bids,asks,fs)
       if fbb is not None:first_beh.append((tt,max(0.,(float(fbb)-fp)/GRID)))
       mm=legal_metric(bids,asks,opp,fp)
       if mm is not None:
        opp_beh.append((tt,mm['behind']))
        if mm['behind']>EPS:opp_break.append(tt)
      if not first_beh or not opp_beh:continue
      first_break=min(opp_break) if opp_break else None
      rows.append({'asset':asset,'marketId':mid,'firstEventMs':fill,'firstSide':fs,'firstPrice':fp,'pairSum':float(ep['pairSum']),'firstPlacementReadyMs':ready,'firstLegRestMs':fill-ready,'firstPlacementBehindTicks':first_beh[0][1],'firstMaxBehindTicks':max(x[1] for x in first_beh),'firstAtBestReceiptFraction':sum(x[1]<=EPS for x in first_beh)/len(first_beh),'firstEverBehind':any(x[1]>EPS for x in first_beh),'oppPlacementLegalBehindTicks':opp_beh[0][1],'oppFillLegalBehindTicks':opp_beh[-1][1],'oppWithin1ReceiptFraction':sum(x[1]<=1+EPS for x in opp_beh)/len(opp_beh),'oppPathBreakSeen':first_break is not None,'oppBreakToFirstFillMs':None if first_break is None else fill-first_break,'firstPlacementCoverage':float(pr['placementCoverage'])})
     if ii%20==0:print(json.dumps({'asset':asset,'markets':ii,'of':len(keys),'rows':len(rows)}),flush=True)
   finally:bc.close()
 finally:tc.close()
 out={'version':'TARGET_BTC_ETH_FIRST_LEG_PREFILL_PACKAGE_ANATOMY_V1','researchOnly':True,'source':a.source,'coverage':{'inputCheapEpisodes':len(base),'resolvedFirstPlacementEpisodes':len(rows)},'summary':{asset:block([r for r in rows if r['asset']==asset]) for asset in ('BTC','ETH')},'rows':rows,'boundary':['Successful official Target cheap-pair Maker episodes only.','First-leg placement is reconstructed from positive public depth adds + official parent hash confirmation; only high-confidence placements retained.','Public book path is descriptive and anonymous; no private queue rank inferred.','No OUR PnL/outcome used and no numeric BTC threshold transferred to ETH.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
