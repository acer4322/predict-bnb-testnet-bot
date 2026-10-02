from __future__ import annotations
import argparse,json,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
EPS=1e-9

class CycleBalancePairSim(base.MinimalPairRoleSim):
    """Research-only scheduler intervention.

    Single semantic change: when native minimal Pair-only wants SATELLITE_EXPAND and
    confirmed Expand fills are already >=60% of all confirmed role fills (minimum 5 fills),
    reroute only the next option to current weak-side repair service. No action veto, no
    fill cap, no winner/Target/future input, Pair economics remains the only hard strategy safety.
    """
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.cycleBalanceChecks=0; self.cycleBalanceReroutes=0; self.cycleBalanceNoWeak=0
        self.cycleBalanceEvents=[]

    def _role_decision(self,qv):
        side,role,req_pair,req_budget=super()._role_decision(qv)
        if role!='SATELLITE_EXPAND':
            return side,role,req_pair,req_budget
        self.cycleBalanceChecks+=1
        total=sum(int(v) for v in self.role_fills.values())
        exp=int(self.role_fills.get('SATELLITE_EXPAND',0))
        share=(exp/total) if total>0 else 0.0
        if total < 5 or share < 0.60:
            return side,role,req_pair,req_budget
        state,held_or_none,repair_or_weak=self._state()
        if state=='ONE_SIDED':
            weak=repair_or_weak
        else:
            weak=repair_or_weak
        if weak is None:
            self.cycleBalanceNoWeak+=1
            return side,role,req_pair,req_budget
        newrole='ECONOMIC_CORE' if self._core_for_side(weak) is None else 'SATELLITE_REPAIR'
        self.cycleBalanceReroutes+=1
        self.cycleBalanceEvents.append({'fillCount':total,'expandFillCount':exp,'expandShare':share,'fromSide':side,'toSide':weak,'toRole':newrole})
        return weak,newrole,True,False

    def run_cycle_balance(self,winner):
        r=self.run_minimal(winner)
        r.update({'cycleBalanceChecks':self.cycleBalanceChecks,'cycleBalanceReroutes':self.cycleBalanceReroutes,'cycleBalanceNoWeak':self.cycleBalanceNoWeak,'cycleBalanceEvents':self.cycleBalanceEvents[:200]})
        return r

def slim(r):
    z=base.slim(r)
    z.update({'cycleBalanceChecks':int(r.get('cycleBalanceChecks') or 0),'cycleBalanceReroutes':int(r.get('cycleBalanceReroutes') or 0)})
    return z

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='pair_cycle_balance_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try: br=b.run_minimal(cr['winner'])
            finally: b.close()
            c=CycleBalancePairSim(tape,4,False)
            try: rr=c.run_cycle_balance(cr['winner'])
            finally: c.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'BASE_PAIR_ONLY',**br})
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'CYCLE_BALANCE_REROUTE',**rr})
            print(json.dumps({'marketId':mid,'baseline':slim(br),'candidate':slim(rr)},ensure_ascii=False),flush=True)
        def agg(cell):
            xs=[x for x in rows if x['cell']==cell]; pn=[float(x.get('pnlDiagnosticOnly') or 0.0) for x in xs]
            return {'markets':len(xs),'avgFills':sum(int(x.get('fillEvents') or 0) for x in xs)/len(xs),'avgAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs)/len(xs),'totalPnl':sum(pn),'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'avgFloor':sum(float(x.get('floor') or 0) for x in xs)/len(xs),'worstFloor':min(float(x.get('floor') or 0) for x in xs),'twoSidedCoverage':sum(bool(x.get('twoSidedMaterialized')) for x in xs)/len(xs),'reroutes':sum(int(x.get('cycleBalanceReroutes') or 0) for x in xs)}
        out={'version':'ETH_MINIMAL_PAIR_CYCLE_BALANCE_SCHEDULER_SMOKE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('BASE_PAIR_ONLY'),'candidate':agg('CYCLE_BALANCE_REROUTE')},'rows':rows,'boundary':['single semantic change = next-action role reroute only when confirmed Expand fills >=60% after >=5 fills','no action veto/no fill-count cap/no serial safety stack','Pair economics remains only hard strategy safety','all trigger inputs strict-past confirmed controller state','winner only post-hoc scoring; no Target/future runtime input','realistic HFT/no dream fill/no 8781','development smoke only; 60% came from consumed100 diagnostic and is not promotion-valid']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
