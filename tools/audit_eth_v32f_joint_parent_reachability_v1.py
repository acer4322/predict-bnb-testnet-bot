from __future__ import annotations
import json, sqlite3, zlib
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/lan_worker_returns/eth-v24-shadow-quality-unseen20-a/result.json'
DB=ROOT/'data/wallet_maker_book_inference_eth5m.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V32F_JOINT_PARENT_REACHABILITY_SHADOW_V1.json'
TICK=0.01

def dec(b):
    if not b: return None
    return json.loads(zlib.decompress(b).decode('utf-8'))

def apply_changes(book, changes):
    if not isinstance(changes,dict): return
    for key in ('bids','asks'):
        for ch in changes.get(key,[]) or []:
            p=float(ch['price']); after=float(ch['after'])
            if after<=1e-12: book[key].pop(p,None)
            else: book[key][p]=after

def time_bin(s):
    if s<30:return '0-30'
    if s<60:return '30-60'
    if s<120:return '60-120'
    if s<180:return '120-180'
    if s<240:return '180-240'
    return '240-300'

def mid_bin(x):
    if x<.2:return '0-.2'
    if x<.4:return '.2-.4'
    if x<.6:return '.4-.6'
    if x<.8:return '.6-.8'
    return '.8-1'

def reconstruct_market(con, mid, query_times):
    endrow=con.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
    if not endrow:return {},None
    end=int(endrow[0])
    qmin=min(query_times);qmax=max(query_times)
    cp=con.execute('select received_at_ms from maker_book_inference_updates where market_id=? and is_checkpoint=1 and received_at_ms<? order by received_at_ms desc,id desc limit 1',(mid,qmin)).fetchone()
    start=int(cp[0]) if cp else qmin-30000
    rows=list(con.execute('select id,received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms>=? and received_at_ms<=? order by received_at_ms,id',(mid,start,qmax)))
    book={'bids':{},'asks':{}}; qi=0; qs=sorted(query_times); out={}
    for r in rows:
        t=int(r['received_at_ms'])
        while qi<len(qs) and qs[qi]<=t:
            qt=qs[qi]
            if book['bids'] and book['asks']:
                ub=max(book['bids']);ua=min(book['asks'])
                out[qt]=(float(ub),float(ua))
            qi+=1
        if int(r['is_checkpoint']):
            book={'bids':{float(k):float(v) for k,v in (dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r['native_asks_z']) or {}).items()}}
        else:
            apply_changes(book,dec(r['changes_z']) or {})
    while qi<len(qs):
        qt=qs[qi]
        if book['bids'] and book['asks']:
            out[qt]=(float(max(book['bids'])),float(min(book['asks'])))
        qi+=1
    return out,end

def side_book(side, up_bid, up_ask):
    if side=='UP': return up_bid,up_ask
    return 1.0-up_ask,1.0-up_bid

def qsum(v):
    v=[float(x) for x in v if x is not None and np.isfinite(float(x))]
    if not v:return {'n':0}
    a=np.asarray(v)
    return {'n':len(v),'mean':float(a.mean()),'median':float(np.median(a)),'p25':float(np.quantile(a,.25)),'p75':float(np.quantile(a,.75))}

def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    rows=[dict(r) for r in src['rows']]
    bymid=defaultdict(list)
    for r in rows:bymid[int(r['marketId'])].append(r)
    con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
    enriched=[]
    for mid, rr in sorted(bymid.items()):
        rr=sorted(rr,key=lambda x:int(x['placed']))
        states,end=reconstruct_market(con,mid,[int(x['placed']) for x in rr])
        seg=0
        for r in rr:
            t=int(r['placed']); s=states.get(t)
            z=dict(r); z['parentSegment']=seg
            if s and end:
                ub,ua=s; bid,ask=side_book(str(r['side']),ub,ua); midp=(bid+ask)/2
                z.update(secondsLeft=(end-t)/1000.0,liveBid=bid,liveAsk=ask,marketMid=midp,
                         connectivityGap=float(r['price'])-bid,
                         behindAtPlacementTicks=(bid-float(r['price']))/TICK,
                         connectedAtPlacement=bool(float(r['price'])+1e-9>=bid),
                         impliedExtremity=abs(midp-.5),timeBin=time_bin((end-t)/1000.0),midBin=mid_bin(midp))
            enriched.append(z)
            if bool(r['filled']):seg+=1
    con.close()
    # parent segments: a fill closes the segment; last segment may fail
    pmap=defaultdict(list)
    for r in enriched:pmap[(int(r['marketId']),int(r['parentSegment']))].append(r)
    parents=[]
    for (mid,seg), rr in sorted(pmap.items()):
        rr=sorted(rr,key=lambda x:int(x['placed'])); rec=any(bool(x['filled']) for x in rr)
        valid=[x for x in rr if x.get('connectedAtPlacement') is not None]
        if not valid:continue
        first=valid[0]; second=valid[1] if len(valid)>1 else None
        first_disc=not first['connectedAtPlacement']
        second_disc=bool(second is not None and not second['connectedAtPlacement'])
        first_gap=max(0.0,-float(first['connectivityGap']))
        second_gap=max(0.0,-float(second['connectivityGap'])) if second else None
        two_disc_nonimproving=bool(first_disc and second_disc and second_gap is not None and second_gap>=first_gap-1e-12)
        any_reconnect=any(bool(x['connectedAtPlacement']) for x in valid[1:]) if len(valid)>1 else False
        p={
          'marketId':mid,'parentSegment':seg,'recovered':rec,'carriers':len(rr),'firstPlaced':first['placed'],
          'firstSecondsLeft':first.get('secondsLeft'),'firstCarrierPrice':first['price'],'firstLiveBid':first.get('liveBid'),
          'firstMarketMid':first.get('marketMid'),'firstImpliedExtremity':first.get('impliedExtremity'),
          'firstConnected':bool(first['connectedAtPlacement']),'firstGap':first_gap,
          'secondConnected':None if second is None else bool(second['connectedAtPlacement']),
          'secondGap':second_gap,'anyReconnectAfterFirst':any_reconnect,
          'twoDisconnectedNonImproving':two_disc_nonimproving,
          'maxPlacementBehindTicks':max(float(x.get('behindAtPlacementTicks',0) or 0) for x in valid),
          'marketMidDeltaLastMinusFirst':float(valid[-1]['marketMid']-first['marketMid']) if len(valid)>1 else 0.0,
          'secondsElapsedFirstToLast':(int(valid[-1]['placed'])-int(first['placed']))/1000.0,
          'rows':[{'key':x['key'],'placed':x['placed'],'filled':x['filled'],'price':x['price'],'secondsLeft':x.get('secondsLeft'),'liveBid':x.get('liveBid'),'marketMid':x.get('marketMid'),'connected':x.get('connectedAtPlacement'),'gap':x.get('connectivityGap'),'behindTicks':x.get('behindAtPlacementTicks')} for x in valid]
        }
        parents.append(p)
    recovered=[p for p in parents if p['recovered']]; failed=[p for p in parents if not p['recovered']]
    def rate(arr,key):return float(np.mean([bool(x[key]) for x in arr])) if arr else None
    # natural-zero candidate confusion counts
    cand=[p for p in parents if p['twoDisconnectedNonImproving']]
    firstdisc=[p for p in parents if not p['firstConnected']]
    report={
      'version':'ETH_REPAIR_V32F_JOINT_PARENT_REACHABILITY_SHADOW_V1','researchOnly':True,'actionAuthority':False,
      'source':str(SRC.relative_to(ROOT)),
      'strictPast':'live bid/ask reconstructed from maker_book_inference_updates received_at_ms strictly before each carrier placed timestamp',
      'carrierSummary':{
        'n':len(enriched),'filled':sum(bool(r['filled']) for r in enriched),
        'connectedAtPlacementN':sum(bool(r.get('connectedAtPlacement')) for r in enriched),
        'fillRateConnected':float(np.mean([r['filled'] for r in enriched if r.get('connectedAtPlacement')])) if any(r.get('connectedAtPlacement') for r in enriched) else None,
        'fillRateDisconnected':float(np.mean([r['filled'] for r in enriched if r.get('connectedAtPlacement') is False])) if any(r.get('connectedAtPlacement') is False for r in enriched) else None,
        'secondsLeftFilled':qsum([r.get('secondsLeft') for r in enriched if r['filled']]),
        'secondsLeftUnfilled':qsum([r.get('secondsLeft') for r in enriched if not r['filled']]),
        'impliedExtremityFilled':qsum([r.get('impliedExtremity') for r in enriched if r['filled']]),
        'impliedExtremityUnfilled':qsum([r.get('impliedExtremity') for r in enriched if not r['filled']])
      },
      'parentSummary':{
        'n':len(parents),'recovered':len(recovered),'failed':len(failed),
        'firstDisconnectedRateRecovered':float(np.mean([not p['firstConnected'] for p in recovered])) if recovered else None,
        'firstDisconnectedRateFailed':float(np.mean([not p['firstConnected'] for p in failed])) if failed else None,
        'reconnectAfterFirstDisconnectRecoveredRate':float(np.mean([p['anyReconnectAfterFirst'] for p in recovered if not p['firstConnected']])) if any(not p['firstConnected'] for p in recovered) else None,
        'reconnectAfterFirstDisconnectFailedRate':float(np.mean([p['anyReconnectAfterFirst'] for p in failed if not p['firstConnected']])) if any(not p['firstConnected'] for p in failed) else None,
        'twoDisconnectedNonImprovingRecoveredRate':rate(recovered,'twoDisconnectedNonImproving'),
        'twoDisconnectedNonImprovingFailedRate':rate(failed,'twoDisconnectedNonImproving'),
        'firstSecondsLeftRecovered':qsum([p['firstSecondsLeft'] for p in recovered]),
        'firstSecondsLeftFailed':qsum([p['firstSecondsLeft'] for p in failed]),
        'firstImpliedExtremityRecovered':qsum([p['firstImpliedExtremity'] for p in recovered]),
        'firstImpliedExtremityFailed':qsum([p['firstImpliedExtremity'] for p in failed])
      },
      'candidateComparison':{
        'firstDisconnectAsHardActive':{'flagged':len(firstdisc),'recoveredFalsePositives':sum(p['recovered'] for p in firstdisc),'failedCaptured':sum(not p['recovered'] for p in firstdisc)},
        'twoDisconnectedNonImproving':{'flagged':len(cand),'recoveredFalsePositives':sum(p['recovered'] for p in cand),'failedCaptured':sum(not p['recovered'] for p in cand)},
        'note':'natural zero/sign rule only; no time/tick threshold fitted'
      },
      'targetArchitectureCrosscheck':{
        'artifact':'V32F_TARGET_STRIKE_TIME_PASSIVE_REACHABILITY_V1.json',
        'finding':'Target BTC passive placement reachability collapses jointly when time is short and |spot-strike| is far; use as architecture evidence only, never ETH numeric transfer.'
      },
      'parents':parents,
      'boundary':['consumed HFT only','future filled/recovered used only as scoring labels','no winner/PnL','no fixed-age/tick threshold sweep','binary market mid is an operational market-position proxy where asset-correct ETH spot/strike is unavailable; it does not replace the Target strike-distance teacher']
    }
    OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'carrierSummary':report['carrierSummary'],'parentSummary':report['parentSummary'],'candidateComparison':report['candidateComparison'],'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__':main()
