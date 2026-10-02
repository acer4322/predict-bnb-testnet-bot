from __future__ import annotations
import json,lzma,zipfile,tempfile,shutil,statistics,math
from pathlib import Path
import sys
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import run_eth_dagger60_smoke_v1 as base
EPS=1e-9
BUNDLE=Path('data/research/r4_v0/p0_provenance_v1/v16_consumed_holdout100_bundle.zip')
CASES={
  1823755: {'result':'data/research/lan_worker_returns/passive-exhaustion-route-relay-1823755-20260906-v1/PASSIVE_EXHAUSTION_SAME_INTENT_ROUTE_RELAY_1823755_V1_20260906_traces/1823755_A_INSIDE_SPREAD_TTL_NO_RELAY_result.json','expected':'EXHAUSTED'},
  1826386: {'result':'data/research/lan_worker_returns/passive-exhaustion-route-relay-repl3-20260906-v1/PASSIVE_EXHAUSTION_ROUTE_RELAY_REPLICATION3_V1_20260906_traces/1826386_A_INSIDE_SPREAD_TTL_NO_RELAY_result.json','expected':'EXHAUSTED'},
  1827418: {'result':'data/research/lan_worker_returns/passive-exhaustion-route-relay-repl3-20260906-v1/PASSIVE_EXHAUSTION_ROUTE_RELAY_REPLICATION3_V1_20260906_traces/1827418_A_INSIDE_SPREAD_TTL_NO_RELAY_result.json','expected':'EXHAUSTED'},
  1827135: {'result':'data/research/lan_worker_returns/passive-exhaustion-route-relay-repl3-20260906-v1/PASSIVE_EXHAUSTION_ROUTE_RELAY_REPLICATION3_V1_20260906_traces/1827135_A_INSIDE_SPREAD_TTL_NO_RELAY_result.json','expected':'SUCCESS'},
}
def side_depth(book,side,p):
    if side=='UP': return float(book['bids'].get(round(p,10),book['bids'].get(float(p),0.0)) or 0.0)
    native=round(1.0-float(p),10)
    return float(book['asks'].get(native,book['asks'].get(float(native),0.0)) or 0.0)
def side_bid_depth(book,side):
    q=base.quotes(book)
    if not q:return None
    p=float(q[side]['bid']);return side_depth(book,side,p)
def rows_for(tape,side,own,start,end):
    payload=json.loads(lzma.decompress(Path(tape).read_bytes()).decode('utf-8'))
    book={'bids':{},'asks':{}};rows=[]
    for u in sorted(payload['updates'],key=lambda x:(int(x[1]),int(x[0]))):
        t=int(u[1]);base.apply(book,u);q=base.quotes(book)
        if not q or t<start or t>end:continue
        bid=float(q[side]['bid']);ask=float(q[side]['ask'])
        rows.append({'t':t,'dt':t-start,'bid':bid,'ask':ask,'spread':ask-bid,'bidGap':bid-own,'askGap':ask-own,'ownDepth':side_depth(book,side,own),'bestBidDepth':side_bid_depth(book,side)})
    return rows
def summarize(mid,cfg,tape):
    r=json.load(open(cfg['result'],encoding='utf-8'))
    pc=r.get('probeCandidate') or r.get('sourceCandidate') or {}
    po=r.get('probeOutcome') or r.get('sourceOutcome') or {}
    if not pc:
        # replication3 stores source under one-shot probe fields
        pc=r.get('probeCandidate') or {}
    key=r.get('probeKey') or pc.get('key')
    if not po and key:
        po=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==key),{})
    side=str(pc.get('side') or po.get('side'))
    own=float(pc.get('probePrice') if pc.get('probePrice') is not None else po.get('submittedPrice'))
    start=int(pc.get('submittedAt') or po.get('submittedAt'))
    fills=po.get('fills') or []
    fill_at=int(fills[0]['t']) if fills else None
    term=po.get('terminal') or {}
    term_at=int(term.get('t')) if term.get('t') is not None else None
    end=fill_at or term_at
    rr=rows_for(tape,side,own,start,end)
    loss=[x for x in rr if x['bid']>own+EPS]
    first=loss[0] if loss else None
    if first:
        after=[x for x in rr if x['t']>=first['t']]
        persist=sum(1 for x in after if x['bid']>own+EPS)/len(after) if after else None
        maxgap=max(x['bidGap'] for x in after)
        mingap=min(x['bidGap'] for x in after)
        minask=min(x['ask'] for x in after);maxask=max(x['ask'] for x in after)
        own_depth_vals=[x['ownDepth'] for x in after]
        first_return=next((x for x in after[1:] if x['bid']<=own+EPS),None)
        return_ms=(first_return['t']-first['t']) if first_return else None
        duration=(end-first['t']) if end else None
        # short horizons from first loss
        horizons={}
        for h in (200,500,1000,2000):
            xs=[x for x in after if x['t']<=first['t']+h]
            if xs:
                horizons[str(h)]={'lastBid':xs[-1]['bid'],'lastAsk':xs[-1]['ask'],'maxBidGap':max(x['bidGap'] for x in xs),'minBidGap':min(x['bidGap'] for x in xs),'lossShare':sum(x['bid']>own+EPS for x in xs)/len(xs),'ownDepthMax':max(x['ownDepth'] for x in xs),'spreadMedian':statistics.median(x['spread'] for x in xs)}
        fsum={'firstLossAt':first['t'],'ageAtLossMs':first['t']-start,'bidAtLoss':first['bid'],'askAtLoss':first['ask'],'gapTicks':(first['bid']-own)/0.01,'durationLossToEndMs':duration,'lossPersistenceShare':persist,'firstReturnMs':return_ms,'maxBidGapAfter':maxgap,'minBidGapAfter':mingap,'minAskAfter':minask,'maxAskAfter':maxask,'ownDepthAtLoss':first['ownDepth'],'ownDepthMedianAfter':statistics.median(own_depth_vals) if own_depth_vals else None,'ownDepthMaxAfter':max(own_depth_vals) if own_depth_vals else None,'horizons':horizons}
    else:fsum=None
    return {'marketId':mid,'expected':cfg['expected'],'side':side,'ownPrice':own,'submittedAt':start,'endAt':end,'fillAt':fill_at,'terminalStatus':term.get('status'),'fillQty':sum(float(x.get('confirmedQty') or 0) for x in fills),'lifetimeMs':(end-start) if end else None,'priorityLoss':fsum,'observations':len(rr)}
def main():
    tmp=Path(tempfile.mkdtemp(prefix='passive_value_'))
    try:
        out=[]
        with zipfile.ZipFile(BUNDLE) as z:
            for mid in CASES:z.extract(f'tapes/{mid}.json.xz',tmp)
        for mid,cfg in CASES.items():out.append(summarize(mid,cfg,tmp/'tapes'/f'{mid}.json.xz'))
        result={'version':'PASSIVE_OPTION_VALUE_SUCCESS_VS_EXHAUSTED_V1','researchOnly':True,'behaviorInert':True,'cases':out,'guards':['no strategy execution','no winner/PnL selection','strict-past L2 only','features descriptive; no threshold authority']}
        op=Path('data/research/r4_v0/p0_provenance_v1/PASSIVE_OPTION_VALUE_SUCCESS_VS_EXHAUSTED_V1_20260906.json');op.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
