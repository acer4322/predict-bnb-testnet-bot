from __future__ import annotations
import json,sqlite3,zlib,statistics
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'
DB=ROOT/'data/wallet_maker_book_inference_eth5m.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_no18_placement_price_geometry_v1.json'

def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None

def apply(book,ch):
    if not isinstance(ch,dict): return
    for k in ('bids','asks'):
        for x in ch.get(k,[]) or []:
            p=float(x['price']); a=float(x['after'])
            if a<=1e-12: book[k].pop(p,None)
            else: book[k][p]=a

def q(xs,q):
    if not xs:return None
    ys=sorted(xs); return float(ys[min(len(ys)-1,max(0,round((len(ys)-1)*q)))])

def main():
    src=json.loads(SRC.read_text()); rows=[r for r in src['rows'] if r.get('highConfidencePlacement') and r.get('placementCarrierReadyMs') is not None]
    by=defaultdict(list)
    for r in rows: by[int(r['marketId'])].append(r)
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; out=[]
    for i,(mid,rr) in enumerate(sorted(by.items()),1):
        rr=sorted(rr,key=lambda r:int(r['placementCarrierReadyMs'])); ups=list(c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
        book={'bids':{},'asks':{}}; ui=0
        for r in rr:
            t=int(r['placementCarrierReadyMs'])
            while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<t:
                u=ups[ui]
                if int(u['is_checkpoint']): book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
                else: apply(book,dec(u['changes_z']) or {})
                ui+=1
            if not book['bids'] or not book['asks']: continue
            up_bid=max(book['bids']); up_ask=min(book['asks']); side=r['side']; tb=up_bid if side=='UP' else 1.0-up_ask
            offset=(float(r['targetPrice'])-tb)/0.01
            opp_bid=(1.0-up_ask) if side=='UP' else up_bid
            out.append({'marketId':mid,'side':side,'targetPrice':r['targetPrice'],'sideBestBid':tb,'offsetTicks':offset,'pairBidSumAtPlacement':tb+opp_bid,'leadMs':r['placementLeadMs']})
        if i%50==0: print(json.dumps({'progressMarkets':i,'rows':len(out)}),flush=True)
    c.close(); offs=[r['offsetTicks'] for r in out]; rounded=[round(x) for x in offs]; exact=sum(abs(x)<=.25 for x in offs); within1=sum(abs(x)<=1.25 for x in offs); within2=sum(abs(x)<=2.25 for x in offs)
    hist=defaultdict(int)
    for x in rounded:
        if -5<=x<=5: hist[str(int(x))]+=1
        elif x<-5: hist['<-5']+=1
        else: hist['>5']+=1
    result={'version':'ETH_MAKER_NO18_PLACEMENT_PRICE_GEOMETRY_V1','rows':len(out),'bestBidExactRate':exact/len(out) if out else None,'within1TickRate':within1/len(out) if out else None,'within2TickRate':within2/len(out) if out else None,'offsetTicks':{'p10':q(offs,.1),'median':q(offs,.5),'p90':q(offs,.9),'histRounded':dict(sorted(hist.items()))},'rowsDetail':out}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in result.items() if k!='rowsDetail'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
