from __future__ import annotations
import argparse,json,sqlite3,zlib,math,statistics
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
GRID=.01; EPS=1e-9
BOOKS={'BTC':ROOT/'data/wallet_maker_book_inference.db','ETH':ROOT/'data/wallet_maker_book_inference_eth5m.db'}

def dec(b):
    if not b:return None
    try:return json.loads(zlib.decompress(b).decode('utf-8'))
    except Exception:return None

def qtile(xs,q):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    z=(len(ys)-1)*q; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def apply_changes(book,ch):
    if not isinstance(ch,dict):return
    for key in ('bids','asks'):
        vals=ch.get(key)
        if not isinstance(vals,list):continue
        for x in vals:
            if isinstance(x,dict):
                try:p=float(x.get('price')); after=x.get('after'); d=x.get('delta')
                except Exception:continue
                if after is not None:
                    try:a=float(after)
                    except Exception:continue
                elif d is not None:
                    try:a=float(book[key].get(p,0.0))+float(d)
                    except Exception:continue
                else:continue
            elif isinstance(x,(list,tuple)) and len(x)>=3:
                try:p=float(x[0]);a=float(x[2])
                except Exception:continue
            else:continue
            if a<=EPS:book[key].pop(p,None)
            else:book[key][p]=a

def target_bid(book,side):
    if not book['bids'] or not book['asks']:return None
    if side=='UP':return float(max(book['bids']))
    return 1.0-float(min(book['asks']))

def native_level(side,target_price):
    return ('bids',round(float(target_price),8)) if side=='UP' else ('asks',round(1.0-float(target_price),8))

def build_market_states(c,mid,start,end):
    rows=list(c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and source_timestamp_ms between ? and ? order by source_timestamp_ms,id',(mid,int(start),int(end))))
    if not rows:return []
    # Need an anchor checkpoint before start to reconstruct state correctly.
    anchor=c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and source_timestamp_ms<? and is_checkpoint=1 order by source_timestamp_ms desc,id desc limit 1',(mid,int(start))).fetchone()
    stream=[]
    if anchor is not None:stream.append(anchor)
    stream.extend(rows)
    book={'bids':{},'asks':{}};out=[]
    for r in stream:
        t=int(r['source_timestamp_ms'])
        if int(r['is_checkpoint']):
            book={'bids':{float(k):float(v) for k,v in (dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r['native_asks_z']) or {}).items()}}
        else:apply_changes(book,dec(r['changes_z']) or {})
        if t>=start:out.append((t,dict(book['bids']),dict(book['asks'])))
    return out

def enrich_episode(ep,states):
    side=str(ep['secondSide']);p=float(ep['secondPrice']);ready=int(ep['placementReadyMs']);fill=int(ep['secondEventMs']);key,np=native_level(side,p)
    path=[]
    for t,bids,asks in states:
        if t<ready or t>fill:continue
        book={'bids':bids,'asks':asks};bb=target_bid(book,side)
        if bb is None:continue
        behind=max(0.0,(float(bb)-p)/GRID); depth=float((bids if key=='bids' else asks).get(np,0.0))
        path.append({'t':t,'bestBid':bb,'behindTicks':behind,'depthAtOrderPrice':depth})
    if not path:return {**ep,'priceTimeResolved':False}
    first=path[0];last=path[-1];deps=[x['depthAtOrderPrice'] for x in path];beh=[x['behindTicks'] for x in path]
    first_behind=next((x['t'] for x in path if x['behindTicks']>=.5),None)
    placement_at_best=first['behindTicks']<.5; prefill_behind=last['behindTicks']>=.5; ever_behind=any(x>=.5 for x in beh)
    maxdep=max(0.0,float(first['depthAtOrderPrice'])-min(deps)) if deps else 0.; depfrac=maxdep/max(float(first['depthAtOrderPrice']),EPS)
    return {**ep,'priceTimeResolved':True,'placementBestBid':float(first['bestBid']),'placementBehindTicks':float(first['behindTicks']),'placementAtBest':placement_at_best,
            'prefillBestBid':float(last['bestBid']),'prefillBehindTicks':float(last['behindTicks']),'prefillBehind':prefill_behind,'everBehind':ever_behind,'maxBehindTicks':float(max(beh)) if beh else None,
            'atBestReceiptFraction':sum(x<.5 for x in beh)/len(beh) if beh else None,'restReceipts':len(path),'firstBehindAfterPlacementMs':int(first_behind-ready) if first_behind is not None else None,
            'behindToFillMs':int(fill-first_behind) if first_behind is not None else None,'depthAtPlacement':float(first['depthAtOrderPrice']),'depthBeforeFill':float(last['depthAtOrderPrice']),
            'maxPublicLevelDepletion':float(maxdep),'publicLevelDepletionFraction':float(depfrac),'orderLevelZeroSeen':any(x<=EPS for x in deps)}

def block(rr):
    n=len(rr);z=[r for r in rr if r.get('priceTimeResolved')];
    return {'episodes':n,'resolved':len(z),'resolvedRate':len(z)/n if n else None,'markets':len({r['marketId'] for r in rr}),
            'placementAtBestRate':sum(bool(r['placementAtBest']) for r in z)/len(z) if z else None,'everBehindRate':sum(bool(r['everBehind']) for r in z)/len(z) if z else None,
            'prefillBehindRate':sum(bool(r['prefillBehind']) for r in z)/len(z) if z else None,'orderLevelZeroSeenRate':sum(bool(r['orderLevelZeroSeen']) for r in z)/len(z) if z else None,
            'placementBehindTicks':stats([r['placementBehindTicks'] for r in z]),'prefillBehindTicks':stats([r['prefillBehindTicks'] for r in z]),'maxBehindTicks':stats([r['maxBehindTicks'] for r in z]),
            'atBestReceiptFraction':stats([r['atBestReceiptFraction'] for r in z]),'firstBehindAfterPlacementMs':stats([r['firstBehindAfterPlacementMs'] for r in z if r['firstBehindAfterPlacementMs'] is not None]),
            'behindToFillMs':stats([r['behindToFillMs'] for r in z if r['behindToFillMs'] is not None]),'depthAtPlacement':stats([r['depthAtPlacement'] for r in z]),
            'publicLevelDepletionFraction':stats([r['publicLevelDepletionFraction'] for r in z]),'secondRestMs':stats([r['secondRestMs'] for r in z])}

def summarize(rows):
    out={}
    for asset in ('BTC','ETH'):
        aa=[r for r in rows if r['asset']==asset]
        cheap=[r for r in aa if r['cheapPair']]
        cheap_post=[r for r in cheap if r['placementMechanism']=='POSTFILL_NEW']
        cheap_pre=[r for r in cheap if r['placementMechanism']=='PREPOSITIONED']
        out[asset]={'overall':block(aa),'cheapPair':block(cheap),'cheapPostfillNew':block(cheap_post),'cheapPrepositioned':block(cheap_pre)}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--episodes',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=json.load(open(a.episodes,encoding='utf-8'));base=[r for r in src['rows'] if r.get('placementHighConfidence') and r.get('placementReadyMs') is not None]
    by=defaultdict(list)
    for r in base:by[(str(r['asset']),int(r['marketId']))].append(r)
    outrows=[]
    for asset in ('BTC','ETH'):
        c=sqlite3.connect(f'file:{BOOKS[asset].resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:
            keys=sorted(k for k in by if k[0]==asset)
            for j,(_,mid) in enumerate(keys,1):
                eps=by[(asset,mid)];lo=min(int(r['placementReadyMs']) for r in eps)-2000;hi=max(int(r['secondEventMs']) for r in eps)+1000;states=build_market_states(c,mid,lo,hi)
                for r in eps:outrows.append(enrich_episode(r,states))
                if j%25==0:print(json.dumps({'asset':asset,'progressMarkets':j,'of':len(keys),'episodes':len(outrows)}),flush=True)
        finally:c.close()
    summ=summarize(outrows)
    shared={'cheapPostfillPlacementAtBestRate':{a:summ[a]['cheapPostfillNew']['placementAtBestRate'] for a in ('BTC','ETH')},
            'cheapPostfillPrefillBehindRate':{a:summ[a]['cheapPostfillNew']['prefillBehindRate'] for a in ('BTC','ETH')},
            'cheapPostfillAtBestReceiptFractionMedian':{a:summ[a]['cheapPostfillNew']['atBestReceiptFraction']['median'] for a in ('BTC','ETH')},
            'cheapPostfillRestMedianMs':{a:summ[a]['cheapPostfillNew']['secondRestMs']['median'] for a in ('BTC','ETH')}}
    out={'version':'TARGET_BTC_ETH_SECOND_LEG_PRICE_TIME_LIFECYCLE_V2','researchOnly':True,'sourceEpisodes':a.episodes,'coverage':{'baseHighConfidenceEpisodes':len(base),'enrichedEpisodes':len(outrows),'priceTimeResolved':sum(bool(r.get('priceTimeResolved')) for r in outrows)},'summary':summ,'crossAssetNormalized':shared,'rows':outrows,
         'boundary':['Target official actual Maker fills and no-18 high-confidence public placement reconstruction only.','Public book path uses source timestamp and anonymous aggregate depth; depth is queue-context proxy, not private queue rank.','Order price relative to target-side public best bid is descriptive execution anatomy, not a transferred action threshold.','No winner/PnL/future settlement used. BTC and ETH numeric values are not cross-transferred.']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'coverage':out['coverage'],'summary':summ,'crossAssetNormalized':shared},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
