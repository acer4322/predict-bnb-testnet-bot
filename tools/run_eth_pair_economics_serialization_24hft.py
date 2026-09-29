from __future__ import annotations
import argparse, importlib.util, json, math, os, shutil, tempfile, zipfile
from pathlib import Path
from collections import Counter

HERE=Path(__file__).resolve().parent

def sibling(name,filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

lad=sibling('safety_ladder_for_pair24','run_eth_safety_reintroduction_ladder_1946317.py')
LadderSim=lad.LadderSim
EPS=1e-9
FULL=[1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899]
CELLS=[
    ('PAIR_ECONOMICS_1SLOT',1,True,False),
    ('PAIR_ECONOMICS_4SLOT',4,True,False),
    ('PAIR_PLUS_SERIALIZATION_1SLOT',1,True,True),
    ('PAIR_PLUS_SERIALIZATION_4SLOT',4,True,True),
]

def summarize(rows):
    by={}
    for cell,_,_,_ in CELLS:
        xs=[r for r in rows if r['cell']==cell]
        pnls=[float(r['pnlDiagnosticOnly']) for r in xs]
        wins=[x for x in pnls if x>EPS]; losses=[x for x in pnls if x<-EPS]; flats=[x for x in pnls if abs(x)<=EPS]
        submits=sum(int(r['submits']) for r in xs); fills=sum(int(r['fillEvents']) for r in xs)
        floors=[float(r['floor']) for r in xs]
        by[cell]={
            'markets':len(xs),'tradeCoverage':sum(int(r['fillEvents'])>0 for r in xs)/len(xs) if xs else None,
            'totalSubmits':submits,'totalFills':fills,'avgSubmitsPerMarket':submits/len(xs) if xs else None,'avgFillsPerMarket':fills/len(xs) if xs else None,
            'fillToSubmit':fills/submits if submits else None,
            'wins':len(wins),'losses':len(losses),'flats':len(flats),'winRateAll24':len(wins)/len(xs) if xs else None,
            'totalPnl':sum(pnls),'avgPnl':sum(pnls)/len(xs) if xs else None,
            'avgWin':sum(wins)/len(wins) if wins else None,'avgLoss':sum(losses)/len(losses) if losses else None,
            'worstLoss':min(losses) if losses else 0.0,'bestWin':max(wins) if wins else 0.0,
            'terminalFloorSum':sum(floors),'terminalFloorAvg':sum(floors)/len(floors) if floors else None,'terminalFloorWorst':min(floors) if floors else None,
            'marketsLossMagnitudeLt1':sum(1 for x in pnls if x<0 and abs(x)<1.0),
            'marketsLossMagnitudeLe1':sum(1 for x in pnls if x<0 and abs(x)<=1.0),
            'vetoCounts':dict(Counter(k for r in xs for k,n in (r.get('vetoCounts') or {}).items() for _ in range(int(n))))
        }
    return by

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids or any(x not in FULL for x in mids): raise ValueError(f'invalid market ids {mids}')
    tmp=Path(tempfile.mkdtemp(prefix='pair_serial_24hft_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            for cell,slots,pair,serial in CELLS:
                sim=LadderSim(tape,slots,pair,False,serial)
                try:r=sim.run_ladder(cr['winner'])
                finally:sim.close()
                row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':cell,'maxSlots':slots,'pairEconomics':pair,'sameSideSerialization':serial,**r}
                rows.append(row)
                print(json.dumps({'progress':cell,'marketId':mid,'submits':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor']},ensure_ascii=False),flush=True)
        out={
            'version':'ETH_PAIR_ECONOMICS_SERIALIZATION_24HFT_SHARD_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,
            'marketIds':mids,'cells':[x[0] for x in CELLS],'rows':rows,'summary':summarize(rows),
            'boundary':['Pair economics and Pair+same-side-serialization only','1-slot and 4-slot','same LadderSim semantics as 1946317 reverse-ablation','same realistic HFT tape/risk queue/250ms entry+response latency/5s TTL','<=180s no-new-exposure retained','no Target runtime input; winner post-hoc only','no dream fill; no 8781','execution-capacity safety probe; no semantic responsibility rounds']
        }
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'markets':len(mids),'rows':len(rows),'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
