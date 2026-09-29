from __future__ import annotations
import argparse,json,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
EPS=1e-9

class SignalRoutedOneSidedPairSim(base.MinimalPairRoleSim):
    """Single semantic change: in ONE_SIDED state do not force missing-side core first.
    Current strict-past signal decides whether to continue held-side Expand or route missing-side Repair.
    Pair economics remains the only hard strategy safety; all other minimal-profile boundaries frozen.
    """
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.oneSidedSignalChecks=0;self.oneSidedContinueHeld=0;self.oneSidedRouteMissing=0
    def _role_decision(self,qv):
        state,held,missing=self._state();signal=self._direction(qv)
        if state=='ONE_SIDED':
            self.oneSidedSignalChecks+=1
            if signal==held:
                self.oneSidedContinueHeld+=1
                return held,'SATELLITE_EXPAND',True,False
            self.oneSidedRouteMissing+=1
            return missing,'ECONOMIC_CORE',True,False
        return super()._role_decision(qv)
    def run_signal(self,winner):
        r=self.run_minimal(winner);r.update({'oneSidedSignalChecks':self.oneSidedSignalChecks,'oneSidedContinueHeld':self.oneSidedContinueHeld,'oneSidedRouteMissing':self.oneSidedRouteMissing});return r

def initial_streak(r):
    seq=[x.get('side') for x in (r.get('fillSideSequence') or [])]
    if not seq:return 0
    s=seq[0];n=0
    for x in seq:
        if x!=s:break
        n+=1
    return n

def slim(r):
    z=base.slim(r);z.update({'initialSameSideFillStreak':initial_streak(r),'oneSidedSignalChecks':int(r.get('oneSidedSignalChecks') or 0),'oneSidedContinueHeld':int(r.get('oneSidedContinueHeld') or 0),'oneSidedRouteMissing':int(r.get('oneSidedRouteMissing') or 0)});return z

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='pair_one_sided_signal_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=SignalRoutedOneSidedPairSim(tape,4,False)
            try:rr=c.run_signal(cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'B_PAIR_ONLY_REPAIR_FIRST_ONE_SIDED',**br});rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'D_PAIR_ONLY_SIGNAL_ROUTED_ONE_SIDED',**rr})
            print(json.dumps({'marketId':mid,'baseline':slim(br),'candidate':slim(rr)},ensure_ascii=False),flush=True)
        def agg(cell):
            xs=[x for x in rows if x['cell']==cell];pn=[float(x.get('pnlDiagnosticOnly') or 0) for x in xs]
            return {'markets':len(xs),'avgSubmits':sum(int(x.get('submits') or 0) for x in xs)/len(xs),'avgFills':sum(int(x.get('fillEvents') or 0) for x in xs)/len(xs),'avgAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs)/len(xs),'avgInitialSameSideFillStreak':sum(initial_streak(x) for x in xs)/len(xs),'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs),'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'twoSidedCoverage':sum(bool(x.get('twoSidedMaterialized')) for x in xs)/len(xs)}
        out={'version':'ETH_MINIMAL_PAIR_ONE_SIDED_SIGNAL_ROUTING_AB_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('B_PAIR_ONLY_REPAIR_FIRST_ONE_SIDED'),'candidate':agg('D_PAIR_ONLY_SIGNAL_ROUTED_ONE_SIDED')},'rows':rows,'boundary':['single semantic change: ONE_SIDED role routing only','Pair economics remains only hard strategy safety','signal is current strict-past book direction; no Target/winner runtime input','shared Floor/risk contraction/one-new-per-receipt disabled as in minimal profile','max4/reanchor/<=180s/execution legality frozen','realistic HFT; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
