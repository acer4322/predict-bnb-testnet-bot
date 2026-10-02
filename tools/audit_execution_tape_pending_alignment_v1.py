from __future__ import annotations
import argparse,json,math,sqlite3,zlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/wallet_maker_book_inference.db'
def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None
def iso_ms(s):
 d=datetime.fromisoformat(str(s).replace('Z','+00:00')); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc); return int(d.timestamp()*1000)
def wei(v): return float(int(str(v)))/1e18
def native_leg(leg):
 if not isinstance(leg,dict): return None
 out=str((leg.get('outcome') or {}).get('name') or '').upper(); qt=str(leg.get('quoteType') or '').upper(); p=wei(leg.get('price'))
 yes=out in {'UP','YES'}
 if yes: side='bids' if qt=='BID' else 'asks'; np=p
 else: side='asks' if qt=='BID' else 'bids'; np=1-p
 return (side,round(np,8))
def pct(xs,q):
 if not xs:return None
 a=sorted(xs); p=(len(a)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p)); return a[lo] if lo==hi else a[lo]*(hi-p)+a[hi]*(p-lo)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--window-ms',type=int,default=1800); a=ap.parse_args(); m=a.market_id
 con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
 metas=con.execute('select source_timestamp_ms,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,id',(m,)).fetchall()
 raws=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(m,))]; con.close()
 prev={'bids':{},'asks':{}}; anchors=[]
 for r in metas:
  cur=dec(r['settlements_pending_z']) or {'bids':{},'asks':{}}
  for side in ('bids','asks'):
   c={round(float(k),8):float(v) for k,v in (cur.get(side) or {}).items()}; p={round(float(k),8):float(v) for k,v in (prev.get(side) or {}).items()}
   for pr in set(c)|set(p):
    d=c.get(pr,0.0)-p.get(pr,0.0)
    if d>1e-8: anchors.append({'sourceMs':int(r['source_timestamp_ms']),'side':side,'price':pr,'qtyAdded':d})
  prev=cur
 aligned=[]; amb=0; offsets=[]
 for i,r in enumerate(raws):
  try: sec=iso_ms(r.get('executedAt'))
  except Exception: continue
  legs=[]
  for leg in [r.get('taker')]+list(r.get('makers') or []):
   nl=native_leg(leg)
   if nl: legs.append(nl)
  cand=[x for x in anchors if (x['side'],x['price']) in legs and -500<=x['sourceMs']-sec<=a.window_ms]
  if not cand: continue
  cand.sort(key=lambda x:(abs((x['sourceMs']-sec)-500),-x['qtyAdded']))
  b=cand[0]; off=b['sourceMs']-sec; offsets.append(off); amb+=int(len(cand)>1); aligned.append({'rawIndex':i,'offsetMs':off,'candidateCount':len(cand),'anchor':b})
 summary={'metaRows':len(metas),'pendingIncreaseAnchors':len(anchors),'rawMatches':len(raws),'alignedRawMatches':len(aligned),'alignmentRate':len(aligned)/len(raws) if raws else None,'uniqueAlignmentRate':(len(aligned)-amb)/len(raws) if raws else None,'ambiguousRawMatches':amb,'offsetMedianMs':pct(offsets,.5),'offsetP10Ms':pct(offsets,.1),'offsetP90Ms':pct(offsets,.9),'offsetMinMs':min(offsets) if offsets else None,'offsetMaxMs':max(offsets) if offsets else None}
 out=ROOT/f'data/research/hftbacktest_execution_shift_v0/execution_tape_pending_alignment_market{m}_v1.json'; out.write_text(json.dumps({'version':'EXECUTION_TAPE_PENDING_ALIGNMENT_V1','marketId':m,'summary':summary,'sampleAligned':aligned[:50],'boundary':'Diagnostic only; positive settlementsPending deltas are candidate settlement-entry anchors, not assumed one-to-one fills.'},indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(out),'summary':summary}))
if __name__=='__main__':main()
