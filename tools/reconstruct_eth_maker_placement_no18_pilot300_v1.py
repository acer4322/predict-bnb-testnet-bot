from __future__ import annotations
import json,sqlite3,zlib,statistics,math
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/wallet_maker_book_inference_eth5m.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'
NMARKETS=300
LOOKBACK_MS=60_000
EPS=1e-9

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else None

def finite(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None

def qtile(xs,q):
    if not xs: return None
    ys=sorted(float(x) for x in xs); i=min(len(ys)-1,max(0,round((len(ys)-1)*q))); return ys[i]

def parse_hashes(payload):
    out=[]
    if isinstance(payload,list): items=payload
    elif isinstance(payload,dict):
        items=[]
        for k in ('matches','data','results'):
            if isinstance(payload.get(k),list): items.extend(payload[k])
        if not items and any(k in payload for k in ('maker','taker','priceExecuted','amountFilled')): items=[payload]
    else: items=[]
    for m in items:
        if not isinstance(m,dict): continue
        makers=[]
        if isinstance(m.get('maker'),dict): makers.append(m.get('maker'))
        if isinstance(m.get('makers'),list): makers.extend(x for x in m.get('makers') if isinstance(x,dict))
        for maker in makers:
            h=str(maker.get('hash') or maker.get('orderHash') or '').lower()
            if h: out.append((h,m))
    return out

def main():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    mids=[int(r[0]) for r in c.execute('select distinct market_id from maker_book_inference_updates order by market_id desc limit ?',(NMARKETS,))]
    result=[]; by_price_bucket=defaultdict(lambda:{'n':0,'supported':0,'hash':0,'lead':[]})
    total_updates=0
    for mi,mid in enumerate(sorted(mids)):
        parents=[dict(r) for r in c.execute("""select market_id,coalesce(nullif(order_hash,''),source_leg_id) parent_id,max(nullif(order_hash,'')) order_hash,side,price,min(event_ms) first_fill_ms,max(event_ms) last_fill_ms,sum(shares) observed_fill_shares,count(*) fill_legs from maker_book_inference_wallet_events where market_id=? and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') group by market_id,coalesce(nullif(order_hash,''),source_leg_id),side,price order by first_fill_ms,parent_id""",(mid,))]
        if not parents: continue
        events=[]
        for r in c.execute('select source_timestamp_ms,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)):
            total_updates+=1; ch=dec(r['changes_z']) or {}; t=int(r['source_timestamp_ms'])
            for key,side in [('bids','BID'),('asks','ASK')]:
                vals=ch.get(key) if isinstance(ch,dict) else None
                if not isinstance(vals,list): continue
                for x in vals:
                    if not isinstance(x,dict): continue
                    p=finite(x.get('price')); d=finite(x.get('delta'))
                    if p is None or d is None or d<=EPS: continue
                    events.append({'t':t,'side':side,'price':round(p,8),'qty':d,'key':f'{t}:{side}:{p:.8f}:{len(events)}'})
        rem={e['key']:float(e['qty']) for e in events}
        maker_hashes=set()
        for rr in c.execute('select raw_json_z from maker_execution_matches_v1 where market_id=?',(mid,)):
            for h,_ in parse_hashes(dec(rr[0])): maker_hashes.add(h)
        for p in parents:
            px=float(p['price']); obs=float(p['observed_fill_shares']); first=int(p['first_fill_ms']); side=str(p['side'])
            native_side='BID' if side=='UP' else 'ASK'; native_price=round(px if side=='UP' else 1.0-px,8)
            legal=1.0/max(px,1e-9); lb=max(obs,legal)
            cand=[e for e in events if e['side']==native_side and abs(e['price']-native_price)<=1e-8 and first-LOOKBACK_MS<=e['t']<first and rem[e['key']]>EPS]
            cand.sort(key=lambda e:e['t'],reverse=True)
            sel=[]; need=lb
            for e in cand:
                if need<=EPS: break
                q=min(need,rem[e['key']]);
                if q<=EPS: continue
                rem[e['key']]-=q; need-=q; sel.append((e['t'],q,e['qty']))
            alloc=lb-max(0.0,need); cov=min(1.0,alloc/lb) if lb>EPS else 0.0
            ready=min((x[0] for x in sel),default=None); nearest=max((x[0] for x in sel),default=None)
            order_hash=str(p.get('order_hash') or '').lower(); hash_match=bool(order_hash and order_hash in maker_hashes)
            lead=first-ready if ready is not None else None
            if px<.25: bucket='<.25'
            elif px<.5: bucket='.25-.50'
            elif px<.75: bucket='.50-.75'
            else: bucket='>=.75'
            b=by_price_bucket[bucket]; b['n']+=1; b['supported']+=int(cov>=.999); b['hash']+=int(hash_match)
            if lead is not None: b['lead'].append(lead)
            result.append({'marketId':mid,'parentId':p['parent_id'],'orderHash':p.get('order_hash'),'side':side,'targetPrice':px,'firstFillMs':first,'observedFillShares':obs,'minimumLegalQty':legal,'intentLowerBound':lb,'nativeSide':native_side,'nativePrice':native_price,'hashConfirmedInRawMatches':hash_match,'placementLowerBoundAllocated':alloc,'placementLowerBoundCoverage':cov,'placementCarrierReadyMs':ready,'nearestSupportingAddMs':nearest,'placementLeadMs':lead,'supportingAdds':len(sel),'highConfidencePlacement':bool(hash_match and cov>=.999 and ready is not None)})
        if (mi+1)%50==0: print(json.dumps({'progressMarkets':mi+1,'of':len(mids),'parents':len(result)}),flush=True)
    c.close()
    leads=[r['placementLeadMs'] for r in result if r['highConfidencePlacement'] and r['placementLeadMs'] is not None]
    supp=[r for r in result if r['placementLowerBoundCoverage']>=.999]
    h=[r for r in result if r['hashConfirmedInRawMatches']]
    hi=[r for r in result if r['highConfidencePlacement']]
    out={'version':'ETH_MAKER_PLACEMENT_NO18_PILOT300_V1','boundary':['offline/read-only','no TARGET_UNIT=18 or placement_supports_18 used','placement quantity target is only hard lower bound max(observed Maker fill, 1/targetPrice)','public +depth ownership remains probabilistic; order hash confirms later Maker fill identity, not placement ownership','same-level public +depth capacity is consumable across parents'],'marketsRequested':len(mids),'marketsWithParents':len(set(r['marketId'] for r in result)),'parents':len(result),'updatesScanned':total_updates,'rawMakerHashConfirmed':len(h),'rawMakerHashConfirmedRate':len(h)/len(result) if result else None,'lowerBoundFullySupported':len(supp),'lowerBoundFullySupportedRate':len(supp)/len(result) if result else None,'highConfidencePlacements':len(hi),'highConfidencePlacementRate':len(hi)/len(result) if result else None,'highConfidenceLeadMs':{'p10':qtile(leads,.1),'median':qtile(leads,.5),'p90':qtile(leads,.9)},'priceBuckets':{k:{'parents':v['n'],'lowerBoundSupportRate':v['supported']/v['n'] if v['n'] else None,'hashConfirmRate':v['hash']/v['n'] if v['n'] else None,'medianLeadMs':qtile(v['lead'],.5)} for k,v in sorted(by_price_bucket.items())},'rows':result}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__': main()
