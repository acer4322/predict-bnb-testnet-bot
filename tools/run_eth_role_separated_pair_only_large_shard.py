from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil,statistics,time,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
EPS=1e-9

def pct(xs,p):
    if not xs:return None
    ys=sorted(float(x) for x in xs)
    if len(ys)==1:return ys[0]
    k=(len(ys)-1)*p
    a=int(k);b=min(a+1,len(ys)-1);w=k-a
    return ys[a]*(1-w)+ys[b]*w

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--num-shards',type=int,default=4);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='paironly_large_'));stop=threading.Event();t0=time.time()
    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat':'PAIR_ONLY_LARGE','shard':a.shard_index,'elapsedSeconds':round(time.time()-t0,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start()
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']
        selected=[r for i,r in enumerate(cohort) if i%a.num_shards==a.shard_index]
        rows=[]
        print(json.dumps({'heartbeat':'PAIR_ONLY_LARGE_START','shard':a.shard_index,'markets':len(selected)}),flush=True)
        for i,cr in enumerate(selected,1):
            mid=int(cr['marketId']);tape=tmp/'tapes'/f'{mid}.json.xz';sim=base.MinimalPairRoleSim(tape,4,False)
            try:r=sim.run_minimal(cr['winner'])
            finally:sim.close()
            s=base.slim(r)
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'targetPnlPostHocOnly':cr.get('targetPnlScoringOnly'),'targetBuyPostHocOnly':cr.get('targetBuyScoringOnly'),**s};rows.append(row)
            print(json.dumps({'progress':i,'of':len(selected),'marketId':mid,'fills':s['fills'],'alts':s['fillSideAlternations'],'twoSided':s['twoSidedMaterialized'],'pnl':s['pnl'],'floor':s['floor']},ensure_ascii=False),flush=True)
        fills=[r['fills'] for r in rows];alts=[r['fillSideAlternations'] for r in rows];pn=[r['pnl'] for r in rows];flo=[r['floor'] for r in rows]
        sm={'markets':len(rows),'tradeCoverage':sum(x>0 for x in fills)/len(rows),'twoSidedCoverage':sum(r['twoSidedMaterialized'] for r in rows)/len(rows),'totalFills':sum(fills),'avgFills':statistics.mean(fills),'medianFills':statistics.median(fills),'p10Fills':pct(fills,.1),'p90Fills':pct(fills,.9),'avgAlternations':statistics.mean(alts),'medianAlternations':statistics.median(alts),'totalPnl':sum(pn),'avgPnl':statistics.mean(pn),'winRate':sum(x>EPS for x in pn)/len(rows),'avgFloor':statistics.mean(flo),'worstFloor':min(flo),'avgSubmits':statistics.mean(r['submits'] for r in rows),'avgReanchors':statistics.mean(r['reanchors'] for r in rows)}
        out={'version':'ETH_ROLE_SEPARATED_PAIR_ONLY_LARGE_SHARD_V1','researchOnly':True,'runtimeAuthority':False,'shardIndex':a.shard_index,'numShards':a.num_shards,'summary':sm,'rows':rows,'boundary':['Pair economics only hard strategy safety','no shared Floor/risk-contract/serialization/one-new-per-receipt action veto','max4 structural capacity + rolling reanchor retained','<=180s no-new-exposure time boundary retained','realistic HFT 250ms/risk queue','no Target runtime input; winner/Target PnL post-hoc only; no dream fill; no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':sm},ensure_ascii=False),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
