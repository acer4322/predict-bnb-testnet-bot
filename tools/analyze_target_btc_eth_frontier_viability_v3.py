from __future__ import annotations
import argparse,json,sqlite3,zlib,math,statistics
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]; EPS=1e-9; GRID=.01
BOOKS={'BTC':ROOT/'data/wallet_maker_book_inference.db','ETH':ROOT/'data/wallet_maker_book_inference_eth5m.db'}

def dec(b):
 if not b:return None
 try:return json.loads(zlib.decompress(b).decode('utf-8'))
 except Exception:return None

def apply_changes(book,ch):
 if not isinstance(ch,dict):return
 for key in ('bids','asks'):
  vals=ch.get(key)
  if not isinstance(vals,list):continue
  for x in vals:
   if isinstance(x,dict):
    try:p=float(x.get('price'));after=x.get('after');delta=x.get('delta')
    except Exception:continue
    if after is not None:
     try:a=float(after)
     except Exception:continue
    elif delta is not None:
     try:a=float(book[key].get(p,0.0))+float(delta)
     except Exception:continue
    else:continue
   elif isinstance(x,(list,tuple)) and len(x)>=3:
    try:p=float(x[0]);a=float(x[2])
    except Exception:continue
   else:continue
   if a<=EPS:book[key].pop(p,None)
   else:book[key][p]=a

def load_states(c,mid,start,end):
 anchor=c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and source_timestamp_ms<=? and is_checkpoint=1 order by source_timestamp_ms desc,id desc limit 1',(mid,int(start))).fetchone();stream=[]
 if anchor:stream.append(anchor)
 lo=int(anchor[0]) if anchor else int(start)-60000
 stream+=list(c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and source_timestamp_ms>? and source_timestamp_ms<=? order by source_timestamp_ms,id',(mid,lo,int(end))))
 book={'bids':{},'asks':{}};out=[]
 for r in stream:
  t=int(r[0])
  if int(r[1]):book={'bids':{float(k):float(v) for k,v in (dec(r[2]) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r[3]) or {}).items()}}
  else:apply_changes(book,dec(r[4]) or {})
  if t>=start:out.append((t,dict(book['bids']),dict(book['asks'])))
 return out

def target_bid(bids,asks,side):
 if not bids or not asks:return None
 return float(max(bids)) if side=='UP' else 1.0-float(min(asks))

def target_ask(bids,asks,side):
 if not bids or not asks:return None
 return float(min(asks)) if side=='UP' else 1.0-float(max(bids))

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def enrich(ep,states):
 p=float(ep['secondPrice']);side=ep['secondSide'];ready=int(ep['placementReadyMs']);fill=int(ep['secondEventMs']);first_t=int(ep['firstEventMs']);first_p=float(ep['firstPrice']);path=[]
 first_states=[x for x in states if x[0]<=first_t]; first_metric=None
 if first_states:
  _,fbids,fasks=first_states[-1]; fbb=target_bid(fbids,fasks,side); fba=target_ask(fbids,fasks,side); ceiling=1.0-first_p-0.01
  if fbb is not None and fba is not None:
   raw=min(ceiling,float(fba)-0.01); lp=math.floor((raw+1e-10)*100.0)/100.0
   if lp>0:first_metric={'firstFillOppBestBid':fbb,'firstFillOppBestAsk':fba,'firstFillOppEconomicCeiling':ceiling,'firstFillOppLegalPassivePrice':lp,'firstFillOppLegalBehindTicks':max(0.0,(fbb-lp)/GRID)}
 for t,bids,asks in states:
  if t<ready or t>fill:continue
  bb=target_bid(bids,asks,side)
  if bb is None:continue
  path.append((t,max(0.0,(float(bb)-p)/GRID)))
 if not path:return None
 beh=[x[1] for x in path];within1=[t for t,b in path if b<=1.0+EPS];far5=[t for t,b in path if b>=5.0-EPS];first_far=min(far5) if far5 else None
 returned=None
 if first_far is not None:
  z=[t for t,b in path if t>first_far and b<=1.0+EPS]
  returned=min(z) if z else None
 return {**ep,**(first_metric or {'firstFillOppBestBid':None,'firstFillOppBestAsk':None,'firstFillOppEconomicCeiling':None,'firstFillOppLegalPassivePrice':None,'firstFillOppLegalBehindTicks':None}),'frontierReceipts':len(path),'placementBehindTicksV3':beh[0],'minBehindTicksV3':min(beh),'maxBehindTicksV3':max(beh),'within1ReceiptFraction':sum(b<=1.0+EPS for b in beh)/len(beh),'within2ReceiptFraction':sum(b<=2.0+EPS for b in beh)/len(beh),'far5Seen':first_far is not None,'firstToFar5Ms':None if first_far is None else first_far-ready,'returnWithin1AfterFar5':returned is not None,'far5ToReturnWithin1Ms':None if returned is None else returned-first_far}

def block(rr):
 return {'episodes':len(rr),'markets':len({r['marketId'] for r in rr}),'firstFillOppLegalBehindTicks':stats([r.get('firstFillOppLegalBehindTicks') for r in rr]),'placementBehindTicks':stats([r['placementBehindTicksV3'] for r in rr]),'maxBehindTicks':stats([r['maxBehindTicksV3'] for r in rr]),'within1ReceiptFraction':stats([r['within1ReceiptFraction'] for r in rr]),'within2ReceiptFraction':stats([r['within2ReceiptFraction'] for r in rr]),'far5SeenRate':sum(r['far5Seen'] for r in rr)/len(rr) if rr else None,'returnWithin1AfterFar5Rate':sum(r['returnWithin1AfterFar5'] for r in rr if r['far5Seen'])/sum(r['far5Seen'] for r in rr) if any(r['far5Seen'] for r in rr) else None,'firstToFar5Ms':stats([r['firstToFar5Ms'] for r in rr]),'far5ToReturnWithin1Ms':stats([r['far5ToReturnWithin1Ms'] for r in rr]),'secondRestMs':stats([r['secondRestMs'] for r in rr])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();src=json.load(open(a.source,encoding='utf-8'));base=[r for r in src['rows'] if r.get('priceTimeResolved') and r.get('cheapPair') and r.get('placementMechanism')=='POSTFILL_NEW' and r.get('placementReadyMs') is not None];by=defaultdict(list)
 for r in base:by[(r['asset'],int(r['marketId']))].append(r)
 rows=[]
 for asset in ('BTC','ETH'):
  c=sqlite3.connect(f'file:{BOOKS[asset].resolve().as_posix()}?mode=ro',uri=True)
  try:
   keys=sorted(k for k in by if k[0]==asset)
   for i,(_,mid) in enumerate(keys,1):
    eps=by[(asset,mid)];lo=min(min(int(r['placementReadyMs']),int(r['firstEventMs'])) for r in eps)-1000;hi=max(int(r['secondEventMs']) for r in eps)+1000;states=load_states(c,mid,lo,hi)
    for r in eps:
     z=enrich(r,states)
     if z:rows.append(z)
    if i%20==0:print(json.dumps({'asset':asset,'markets':i,'of':len(keys),'rows':len(rows)}),flush=True)
  finally:c.close()
 out={'version':'TARGET_BTC_ETH_SECOND_LEG_FRONTIER_VIABILITY_V3','researchOnly':True,'source':a.source,'coverage':{'inputEpisodes':len(base),'resolvedEpisodes':len(rows)},'summary':{asset:block([r for r in rows if r['asset']==asset]) for asset in ('BTC','ETH')},'rows':rows,'boundary':['Successful official Target cheap post-fill-new Maker second legs only.','Public source-clock best bid path is descriptive anonymous market context, not private queue rank.','No OUR outcome/PnL used; no thresholds transferred between BTC and ETH.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
