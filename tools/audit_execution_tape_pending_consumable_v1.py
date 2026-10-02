from __future__ import annotations
import argparse,json,math,sqlite3,zlib
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/wallet_maker_book_inference.db'
def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None
def iso_ms(s):
 d=datetime.fromisoformat(str(s).replace('Z','+00:00')); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc); return int(d.timestamp()*1000)
def wei(v): return float(int(str(v)))/1e18
def native_maker(leg):
 out=str((leg.get('outcome') or {}).get('name') or '').upper(); qt=str(leg.get('quoteType') or '').upper(); p=wei(leg.get('price')); yes=out in {'UP','YES'}
 if yes:return ('bids' if qt=='BID' else 'asks',round(p,8))
 return ('asks' if qt=='BID' else 'bids',round(1-p,8))
def pct(xs,q):
 if not xs:return None
 a=sorted(xs); p=(len(a)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p)); return a[lo] if lo==hi else a[lo]*(hi-p)+a[hi]*(p-lo)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--early-ms',type=int,default=-500); ap.add_argument('--late-ms',type=int,default=1800); ap.add_argument('--target-offset-ms',type=int,default=330); a=ap.parse_args(); m=a.market_id
 con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
 metas=con.execute('select source_timestamp_ms,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,id',(m,)).fetchall()
 raws=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(m,))]; con.close()
 if not metas: raise SystemExit('no execution metadata')
 lo=min(int(r['source_timestamp_ms']) for r in metas); hi=max(int(r['source_timestamp_ms']) for r in metas)
 # positive pending deltas become consumable capacities
 prev={'bids':{},'asks':{}}; anchors=[]
 for r in metas:
  cur=dec(r['settlements_pending_z']) or {'bids':{},'asks':{}}
  for side in ('bids','asks'):
   c={round(float(k),8):float(v) for k,v in (cur.get(side) or {}).items()}; p={round(float(k),8):float(v) for k,v in (prev.get(side) or {}).items()}
   for pr in set(c)|set(p):
    d=c.get(pr,0)-p.get(pr,0)
    if d>1e-9: anchors.append({'sourceMs':int(r['source_timestamp_ms']),'side':side,'price':pr,'qty':d,'remaining':d})
  prev=cur
 # maker leg demand. each raw match contributes exactly amountFilled across makers.
 demands=[]; raw_info={};
 for ri,r in enumerate(raws):
  sec=iso_ms(r.get('executedAt'))
  if not (lo-1000<=sec<=hi+1000): continue
  q=wei(r['amountFilled']); raw_info[ri]={'sec':sec,'qty':q,'allocated':0.0,'allocs':[]}
  for mi,mk in enumerate(r.get('makers') or []):
   side,pr=native_maker(mk); ql=wei(mk['amount']); demands.append({'raw':ri,'maker':mi,'sec':sec,'side':side,'price':pr,'qty':ql,'remaining':ql})
 # allocate each anchor to nearest eligible outstanding maker demand; capacities cannot be reused.
 by_level=defaultdict(list)
 for d in demands: by_level[(d['side'],d['price'])].append(d)
 for vals in by_level.values(): vals.sort(key=lambda x:(x['sec'],x['raw'],x['maker']))
 allocated=0.0
 for an in sorted(anchors,key=lambda x:x['sourceMs']):
  vals=by_level.get((an['side'],an['price']),[])
  while an['remaining']>1e-9:
   cand=[d for d in vals if d['remaining']>1e-9 and a.early_ms<=an['sourceMs']-d['sec']<=a.late_ms]
   if not cand: break
   cand.sort(key=lambda d:(abs((an['sourceMs']-d['sec'])-a.target_offset_ms),abs(d['remaining']-an['remaining']),d['sec']))
   d=cand[0]; q=min(an['remaining'],d['remaining']); an['remaining']-=q; d['remaining']-=q; allocated+=q
   info=raw_info[d['raw']]; info['allocated']+=q; info['allocs'].append((an['sourceMs'],q,an['side'],an['price']))
 total=sum(d['qty'] for d in demands); anchor_total=sum(x['qty'] for x in anchors); residual=sum(x['remaining'] for x in anchors)
 fracs=[]; offs=[]; inferred=[]
 for ri,x in raw_info.items():
  frac=x['allocated']/x['qty'] if x['qty']>0 else 0; fracs.append(frac)
  if x['allocs']:
   ts=sum(t*q for t,q,*_ in x['allocs'])/sum(q for _,q,*_ in x['allocs']); off=ts-x['sec']; offs.append(off); inferred.append({'rawIndex':ri,'filledQty':x['qty'],'allocatedQty':x['allocated'],'coverage':frac,'inferredMs':round(ts,3),'offsetMs':round(off,3),'anchorCount':len(x['allocs'])})
 summary={'captureStartMs':lo,'captureEndMs':hi,'captureRawMatches':len(raw_info),'makerLegDemands':len(demands),'pendingIncreaseAnchors':len(anchors),'rawMakerDemandQty':total,'pendingIncreaseQty':anchor_total,'allocatedQty':allocated,'quantityCoverage':allocated/total if total else None,'pendingCapacityUsedRate':allocated/anchor_total if anchor_total else None,'unusedPendingQty':residual,'rawMatchesCoverageGE50':sum(f>=.5 for f in fracs)/len(fracs) if fracs else None,'rawMatchesCoverageGE80':sum(f>=.8 for f in fracs)/len(fracs) if fracs else None,'rawMatchesCoverageGE95':sum(f>=.95 for f in fracs)/len(fracs) if fracs else None,'rawMatchesAnyAllocation':sum(f>0 for f in fracs)/len(fracs) if fracs else None,'inferredOffsetMedianMs':pct(offs,.5),'inferredOffsetP10Ms':pct(offs,.1),'inferredOffsetP90Ms':pct(offs,.9)}
 out=ROOT/f'data/research/hftbacktest_execution_shift_v0/execution_tape_pending_consumable_market{m}_v1.json'; out.write_text(json.dumps({'version':'EXECUTION_TAPE_PENDING_CONSUMABLE_V1','marketId':m,'config':vars(a),'summary':summary,'sampleInferred':inferred[:80],'boundary':'Diagnostic quantity-conserving allocation. No pending capacity is reused. Not yet promoted to HftBacktest trade timestamps.'},indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(out),'summary':summary}))
if __name__=='__main__':main()
