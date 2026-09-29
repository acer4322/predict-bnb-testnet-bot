from __future__ import annotations
import argparse,json,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
import tools.run_eth_minimal_pair_prebase_active_expand_relay_1946683 as relay
EPS=1e-9

def slim(r):
    z=base.slim(r)
    z.update({
        'prebaseActiveRelayUsed':bool(r.get('prebaseActiveRelayUsed')),
        'activeRelaySubmits':int(r.get('activeRelaySubmits') or 0),
        'activeRelayFillQty':float(r.get('activeRelayFillQty') or 0.0),
    })
    return z

def aggregate(rows, side):
    xs=[x[side] for x in rows]
    pn=[float(x.get('pnlDiagnosticOnly') or 0.0) for x in xs]
    return {
        'markets':len(xs),
        'totalSubmits':sum(int(x.get('submits') or 0) for x in xs),
        'totalFills':sum(int(x.get('fillEvents') or 0) for x in xs),
        'avgFills':sum(int(x.get('fillEvents') or 0) for x in xs)/len(xs),
        'totalAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs),
        'avgAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs)/len(xs),
        'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs),
        'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),
        'relayUsed':sum(bool(x.get('prebaseActiveRelayUsed')) for x in xs),
        'relayFillQty':sum(float(x.get('activeRelayFillQty') or 0.0) for x in xs),
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='prebase_active_relay_smoke3_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=relay.PrebaseActiveExpandRelaySim(tape,4,False)
            try:rr=c.run_relay(cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'baseline':br,'candidate':rr})
            print(json.dumps({'marketId':mid,'baseline':slim(br),'candidate':slim(rr),'relayEvents':rr.get('activeRelayEvents',[])},ensure_ascii=False),flush=True)
        out={
            'version':'ETH_MINIMAL_PAIR_PREBASE_ACTIVE_EXPAND_RELAY_SMOKE3_V1','date':'2026-09-05',
            'researchOnly':True,'runtimeAuthority':False,'markets':mids,
            'summary':{'baseline':aggregate(rows,'baseline'),'candidate':aggregate(rows,'candidate')},
            'rows':rows,
            'boundary':[
                'single execution mutation only: one terminal zero-fill same-side SATELLITE_EXPAND can relay to venue-min Active before two-sided materialization',
                'explicit terminal statuses only; submit-latency NONE is not terminal',
                'Pair economics only hard strategy safety','<=180/max4/reanchor/250ms realistic-HFT frozen',
                'no Target/winner runtime input; no dream fill; no 8781'
            ]
        }
        Path(a.output).parent.mkdir(parents=True,exist_ok=True)
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
