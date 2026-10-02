from __future__ import annotations
import argparse,json,sys,tempfile,zipfile,shutil,time,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
EPS=1e-9
FULL=[1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899]

def agg(rows,cell):
    xs=[x for x in rows if x['cell']==cell];pn=[float(x.get('pnlDiagnosticOnly') or 0) for x in xs]
    return {
        'markets':len(xs),'tradeCoverage':sum(int(x.get('fillEvents') or 0)>0 for x in xs)/len(xs),
        'twoSidedCoverage':sum(bool(x.get('twoSidedMaterialized')) for x in xs)/len(xs),
        'totalSubmits':sum(int(x.get('submits') or 0) for x in xs),'totalFills':sum(int(x.get('fillEvents') or 0) for x in xs),
        'avgSubmits':sum(int(x.get('submits') or 0) for x in xs)/len(xs),'avgFills':sum(int(x.get('fillEvents') or 0) for x in xs)/len(xs),
        'totalAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs),'avgAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs)/len(xs),
        'avgReanchors':sum(int(x.get('reanchors') or 0) for x in xs)/len(xs),
        'avgMaxDistinct':sum(int(x.get('maxSimultaneousDistinctPrices') or 0) for x in xs)/len(xs),
        'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs),'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'flats':sum(abs(x)<=EPS for x in pn),'winRate':sum(x>EPS for x in pn)/len(xs),
        'avgFloor':sum(float(x.get('floor') or 0) for x in xs)/len(xs),'worstFloor':min(float(x.get('floor') or 0) for x in xs),
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='role_pair24_'));stop=threading.Event();start=time.time()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'ROLE_PAIR24','elapsedSeconds':round(time.time()-start,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ROLE_PAIR24_START','markets':24,'cells':['PAIR_ONLY','PAIR_PLUS_SERIALIZATION']}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for i,mid in enumerate(FULL,1):
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            for cell,serial in [('PAIR_ONLY',False),('PAIR_PLUS_SERIALIZATION',True)]:
                sim=base.MinimalPairRoleSim(tape,4,serial)
                try:r=sim.run_minimal(cr['winner'])
                finally:sim.close()
                rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':cell,**r})
            b=rows[-2];c=rows[-1]
            print(json.dumps({'progress':i,'of':24,'marketId':mid,'pairOnly':{'fills':b['fillEvents'],'alts':b['fillSideAlternations'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor']},'serialized':{'fills':c['fillEvents'],'alts':c['fillSideAlternations'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor']}},ensure_ascii=False),flush=True)
        out={'version':'ETH_ROLE_SEPARATED_MINIMAL_PAIR_SAFETY_24HFT_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':FULL,'summary':{'PAIR_ONLY':agg(rows,'PAIR_ONLY'),'PAIR_PLUS_SERIALIZATION':agg(rows,'PAIR_PLUS_SERIALIZATION')},'rows':rows,'boundary':['actual role-separated distinct-price lifecycle controller','Pair economics only primary cell','Pair + same-side serialization matched comparator','shared Floor/risk-contract/other historical safety vetoes not action authority','max4 structural capacity + rolling reanchor + <=180s time boundary retained','realistic HFT 250ms queue; no dream fill; no Target runtime; winner post-hoc only; no 8781','fill-side alternation is liveness/anatomy proxy, not semantic responsibility-round authority']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
