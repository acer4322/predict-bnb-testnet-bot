from __future__ import annotations
import argparse,json,sqlite3,zlib,statistics,math
from pathlib import Path
from collections import defaultdict
EPS=1e-9;LOOKBACK_MS=60000

def dec(b):
 if not b:return None
 try:return json.loads(zlib.decompress(b).decode('utf-8'))
 except Exception:return None

def finite(v):
 try:
  x=float(v);return x if math.isfinite(x) else None
 except Exception:return None

def qtile(xs,q):
 if not xs:return None
 ys=sorted(float(x) for x in xs);i=min(len(ys)-1,max(0,round((len(ys)-1)*q)));return ys[i]

def parse_hashes(payload):
 out=[];items=payload if isinstance(payload,list) else []
 if isinstance(payload,dict):
  items=[]
  for k in ('matches','data','results'):
   if isinstance(payload.get(k),list):items.extend(payload[k])
  if not items and any(k in payload for k in ('maker','makers','priceExecuted','amountFilled')):items=[payload]
 for m in items:
  if not isinstance(m,dict):continue
  makers=[]
  if isinstance(m.get('maker'),dict):makers.append(m['maker'])
  if isinstance(m.get('makers'),list):makers.extend(x for x in m['makers'] if isinstance(x,dict))
  for maker in makers:
   h=str(maker.get('hash') or maker.get('orderHash') or '').lower()
   if h:out.append(h)
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--n-markets',type=int,default=150);a=ap.parse_args();c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row;mids=[int(r[0]) for r in c.execute('select distinct market_id from maker_book_inference_updates order by market_id desc limit ?',(a.n_markets,))];result=[];updates=0
 for mi,mid in enumerate(sorted(mids)):
  parents=[dict(r) for r in c.execute("select market_id,coalesce(nullif(order_hash,''),source_leg_id) parent_id,max(nullif(order_hash,'')) order_hash,side,price,min(event_ms) first_fill_ms,max(event_ms) last_fill_ms,sum(shares) observed_fill_shares,count(*) fill_legs from maker_book_inference_wallet_events where market_id=? and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') group by market_id,coalesce(nullif(order_hash,''),source_leg_id),side,price order by first_fill_ms,parent_id",(mid,))]
  if not parents:continue
  events=[]
  for r in c.execute('select source_timestamp_ms,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)):
   updates+=1;ch=dec(r['changes_z']) or {};t=int(r['source_timestamp_ms'])
   for key,side in (('bids','BID'),('asks','ASK')):
    vals=ch.get(key) if isinstance(ch,dict) else None
    if not isinstance(vals,list):continue
    for x in vals:
     if not isinstance(x,dict):continue
     p=finite(x.get('price'));d=finite(x.get('delta'))
     if p is None or d is None or d<=EPS:continue
     events.append({'t':t,'side':side,'price':round(p,8),'qty':d,'key':f'{t}:{side}:{p:.8f}:{len(events)}'})
  rem={e['key']:float(e['qty']) for e in events};maker_hashes=set()
  for rr in c.execute('select raw_json_z from maker_execution_matches_v1 where market_id=?',(mid,)):
   maker_hashes.update(parse_hashes(dec(rr[0])))
  for p in parents:
   px=float(p['price']);obs=float(p['observed_fill_shares']);first=int(p['first_fill_ms']);side=str(p['side']);native_side='BID' if side=='UP' else 'ASK';native_price=round(px if side=='UP' else 1.0-px,8);legal=1.0/max(px,1e-9);lb=max(obs,legal);cand=[e for e in events if e['side']==native_side and abs(e['price']-native_price)<=1e-8 and first-LOOKBACK_MS<=e['t']<first and rem[e['key']]>EPS];cand.sort(key=lambda e:e['t'],reverse=True);sel=[];need=lb
   for e in cand:
    if need<=EPS:break
    q=min(need,rem[e['key']])
    if q<=EPS:continue
    rem[e['key']]-=q;need-=q;sel.append((e['t'],q,e['qty']))
   alloc=lb-max(0.0,need);cov=min(1.0,alloc/lb) if lb>EPS else 0.0;ready=min((x[0] for x in sel),default=None);nearest=max((x[0] for x in sel),default=None);oh=str(p.get('order_hash') or '').lower();hm=bool(oh and oh in maker_hashes);lead=first-ready if ready is not None else None;result.append({'marketId':mid,'parentId':p['parent_id'],'orderHash':p.get('order_hash'),'side':side,'targetPrice':px,'firstFillMs':first,'observedFillShares':obs,'minimumLegalQty':legal,'intentLowerBound':lb,'nativeSide':native_side,'nativePrice':native_price,'hashConfirmedInRawMatches':hm,'placementLowerBoundAllocated':alloc,'placementLowerBoundCoverage':cov,'placementCarrierReadyMs':ready,'nearestSupportingAddMs':nearest,'placementLeadMs':lead,'supportingAdds':len(sel),'highConfidencePlacement':bool(hm and cov>=.999 and ready is not None)})
  if (mi+1)%25==0:print(json.dumps({'progressMarkets':mi+1,'of':len(mids),'parents':len(result)}),flush=True)
 c.close();hi=[r for r in result if r['highConfidencePlacement']];leads=[r['placementLeadMs'] for r in hi if r['placementLeadMs'] is not None];out={'version':'ETH_MAKER_PLACEMENT_NO18_FRESH_V2','researchOnly':True,'marketsRequested':len(mids),'marketRange':[min(mids),max(mids)] if mids else None,'marketsWithParents':len({r['marketId'] for r in result}),'parents':len(result),'updatesScanned':updates,'highConfidencePlacements':len(hi),'highConfidencePlacementRate':len(hi)/len(result) if result else None,'highConfidenceLeadMs':{'p10':qtile(leads,.1),'median':qtile(leads,.5),'p90':qtile(leads,.9)},'rows':result,'boundary':['chronology-forward fresh ETH markets only','no TARGET_UNIT=18','placement lower bound=max(observed Maker fill,1/price)','public depth ownership remains probabilistic; raw Maker hash is identity confirmation']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
