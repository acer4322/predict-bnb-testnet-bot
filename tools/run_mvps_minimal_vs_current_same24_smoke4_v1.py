from __future__ import annotations
import argparse, importlib.util, json, os, shutil, tempfile, zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]

def load(name, path):
    sp=importlib.util.spec_from_file_location(name, path); m=importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m

_PAIR_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_pair_economics_serialization_24hft.py'
_V3B_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'
pair=load('mvps_pair', _PAIR_STAGED if _PAIR_STAGED.exists() else HERE/'run_eth_pair_economics_serialization_24hft.py')
v3b=load('mvps_v3b', _V3B_STAGED if _V3B_STAGED.exists() else HERE/'run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py')
LadderSim=pair.LadderSim
V3B=v3b.FifoAggregateResponsibilityLadderV3B
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986]

def metrics_from_result(r, winner):
    pnl=float(r['upQty' if str(winner).upper()=='UP' else 'downQty'])-float(r['buyNotional']) if 'upQty' in r else float(r['pnlDiagnosticOnly'])
    notional=float(r.get('buyNotional') or 0.0)
    fills=int(r.get('fillEvents') or r.get('fills') or 0)
    alts=int(r.get('fillSideAlternations') or 0)
    return {'pnl':pnl,'buyNotional':notional,'pnlPer100BuyNotional':(100.0*pnl/notional if notional>EPS else None),'fills':fills,'alternations':alts,'floor':float(r.get('floor') or 0.0),'submits':int(r.get('submits') or 0)}

def summarize(rows, cell):
    xs=[r for r in rows if r['cell']==cell]; pn=[r['pnl'] for r in xs]; nt=sum(r['buyNotional'] for r in xs)
    return {'markets':len(xs),'aggregatePnl':sum(pn),'avgPnl':sum(pn)/len(xs),'wins':sum(x>EPS for x in pn),'winRate':sum(x>EPS for x in pn)/len(xs),'worstPnl':min(pn),'bestPnl':max(pn),'tradeCoverage':sum(r['fills']>0 for r in xs)/len(xs),'totalBuyNotional':nt,'aggregatePnlPer100BuyNotional':(100.0*sum(pn)/nt if nt>EPS else None),'totalFills':sum(r['fills'] for r in xs),'avgFills':sum(r['fills'] for r in xs)/len(xs),'totalAlternations':sum(r['alternations'] for r in xs),'avgAlternations':sum(r['alternations'] for r in xs)/len(xs),'worstFloor':min(r['floor'] for r in xs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='mvps_same24_'))
    rows=[]
    try:
        with zipfile.ZipFile(a.bundle) as z:z.extractall(tmp)
        co=json.load(open(tmp/'cohort.json',encoding='utf-8')); cmap={int(x['marketId']):x for x in co['rows']}
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz'; winner=str(cmap[mid]['winner']).upper()
            cells=[]
            s=LadderSim(tape,1,True,False,False)
            try:r=s.run_ladder(winner)
            finally:s.close()
            cells.append(('PAIR1_MINIMAL',r))
            s=LadderSim(tape,4,True,False,True)
            try:r=s.run_ladder(winner)
            finally:s.close()
            cells.append(('PAIR_SERIAL4',r))
            s=V3B(tape)
            try:r=s.run_qty('__UNSCORED__')
            finally:s.close()
            cells.append(('V3B_FIFO_CURRENT',r))
            for cell,r in cells:
                m=metrics_from_result(r,winner);row={'marketId':mid,'winnerPostHocOnly':winner,'cell':cell,**m};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        summary={c:summarize(rows,c) for c in ('PAIR1_MINIMAL','PAIR_SERIAL4','V3B_FIFO_CURRENT')}
        out={'version':'MVPS_MINIMAL_VS_CURRENT_SAME24_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'summary':summary,'boundary':['fixed first four of frozen same24 cohort','same tape/horizon','PAIR1 minimal Pair economics one slot','PAIR_SERIAL4 same-side serialization risk-reduced comparator','V3B exact-FIFO/RoleSeparated current substrate','winner post-hoc only','realistic HFT/no dream fill/no Target runtime/no8781/no fresh/reserve']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
