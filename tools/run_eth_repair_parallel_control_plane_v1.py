from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,importlib.util
from pathlib import Path
import numpy as np,joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
# V4 may be staged beside this script before being promoted to worker tools.
try:
    from tools import run_eth_repair_functional_exam_v4_persistent_carrier_ledger as v4
except ImportError:
    vp=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py')
    spec=importlib.util.spec_from_file_location('eth_v4_staged',vp);v4=importlib.util.module_from_spec(spec);spec.loader.exec_module(v4)
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1

EPS=1e-9

class ParallelControlPlaneSim(v4.PersistentCarrierSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.parallelTicks=0;self.repairProposalTicks=0;self.expandProposalTicks=0;self.dualProposalTicks=0
        self.arbiterOwnershipVeto=0;self.arbiterReauthVeto=0;self.arbiterNoAction=0;self.sameTickCancelAndDecision=0
    def _parallel_snapshot(self,t,end):
        cur,seq,mask,debt,lag=self.lifecycle_features(t,end);p=self.lifecycle.predict(cur,seq,mask,debt,lag);self.lifecyclePredictions.append(p)
        self._refresh_carrier_ledger(t);self._reconcile_objective(t);before=self.excessCarrierCancelRequests;self._continuous_capacity_reconcile(t);cancels_now=self.excessCarrierCancelRequests-before
        ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        gap=abs(float(ai['UP'])-float(ai['DOWN'])) if weak else 0.0;obj=None if self.activeObjective is None else dict(self.activeObjective);unresolved=[(k,dict(e),float(r)) for k,e,r in self.unresolved()]
        return {'t':int(t),'p':p,'ai':dict(ai),'weak':weak,'dom':dom,'gap':gap,'objective':obj,'unresolved':unresolved,'cancelsNow':int(cancels_now)}
    def _repair_lane(self,snap,proposed_qty):
        weak=snap['weak']
        if weak is None:return None
        p=snap['p'];oid=snap['objective']['id'] if snap['objective'] and snap['objective'].get('role')=='REPAIR' else None
        owned=sum(r for _,e,r in snap['unresolved'] if e.get('side')==weak and (oid is None or e.get('objectiveId')==oid));remaining=max(0.0,float(snap['gap'])-owned)
        return {'role':'REPAIR','side':weak,'score':float(p['repair']),'remaining':remaining,'qty':min(float(proposed_qty),remaining)}
    def _expand_lane(self,snap,proposed_qty):
        dom=snap['dom']
        if dom is None:return None
        p=snap['p'];return {'role':'EXPAND','side':dom,'score':float(max(0.0,1.0-float(p['repair']))),'qty':float(proposed_qty),'switch':float(p['switch']),'reentry':float(p['reentry'])}
    def _ownership_lane(self,snap):
        unresolved=snap['unresolved'];return {'outstandingTotal':sum(r for _,_,r in unresolved),'entries':len(unresolved)}
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        self.parallelTicks+=1;snap=self._parallel_snapshot(t,end);p=snap['p'];repair=self._repair_lane(snap,proposed_qty);expand=self._expand_lane(snap,proposed_qty);own=self._ownership_lane(snap)
        if repair is not None:self.repairProposalTicks+=1
        if expand is not None:self.expandProposalTicks+=1
        if repair is not None and expand is not None:self.dualProposalTicks+=1
        obj=self.activeObjective
        if obj is None:
            if own['outstandingTotal']>EPS:self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
            if repair is not None and p['repair']>=0.5:chosen=repair
            elif expand is not None:
                if self.awaitingReentry and p['reentry']<0.5:self.reauthBlocks+=1;self.arbiterReauthVeto+=1;return None
                self.awaitingReentry=False;chosen=expand
            else:self.arbiterNoAction+=1;return None
            self.activeObjective=self._new_objective(chosen['role'],chosen['side']);obj=self.activeObjective
        else:
            if obj['role']=='REPAIR':
                if repair is None:self.arbiterNoAction+=1;return None
                if repair['side']!=obj.get('side'):
                    if own['outstandingTotal']>EPS:self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
                    if p['switch']<0.5 or p['repair']<0.5:self.reauthBlocks+=1;self.arbiterReauthVeto+=1;return None
                    self.objectiveSwitches+=1;self.activeObjective=self._new_objective('REPAIR',repair['side']);obj=self.activeObjective;chosen=repair
                elif expand is not None and p['repair']<0.5:
                    if own['outstandingTotal']>EPS:self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
                    if p['switch']<0.5 or p['reentry']<0.5:self.reauthBlocks+=1;self.arbiterReauthVeto+=1;chosen=repair
                    else:self.objectiveSwitches+=1;self.activeObjective=self._new_objective('EXPAND',expand['side']);obj=self.activeObjective;chosen=expand
                else:chosen=repair
            else:
                if repair is not None and p['repair']>=0.5:
                    if own['outstandingTotal']>EPS:self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
                    if p['switch']<0.5:self.reauthBlocks+=1;self.arbiterReauthVeto+=1;chosen=expand
                    else:self.objectiveSwitches+=1;self.activeObjective=self._new_objective('REPAIR',repair['side']);obj=self.activeObjective;chosen=repair
                else:
                    if expand is None:self.arbiterNoAction+=1;return None
                    chosen=expand
        side=chosen['side'];oid=self.activeObjective.get('id');role=self.activeObjective.get('role');self.activeObjective['side']=side
        if self._incompatible_unresolved(oid,side):self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
        qty=float(chosen['qty'])
        if role=='REPAIR':
            ai=snap['ai'];gap=abs(float(ai['UP'])-float(ai['DOWN']));owned=sum(rem for _,_,rem in self.unresolved(side=side,objective_id=oid));remaining=max(0.0,gap-owned)
            if remaining<=EPS:self.ambiguousOwnershipBlocks+=1;self.arbiterNoAction+=1;return None
            qty=min(qty,remaining)
        elif self.outstanding_total()>EPS:self.incompatibleOwnershipBlocks+=1;self.arbiterOwnershipVeto+=1;return None
        if qty<=EPS:self.arbiterNoAction+=1;return None
        if snap['cancelsNow']>0:self.sameTickCancelAndDecision+=1
        return side,qty,p,role,oid
    def run_exam_v2(self,models,winner):
        r=super().run_exam_v2(models,winner);r.update({'parallelTicks':self.parallelTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'dualProposalTicks':self.dualProposalTicks,'arbiterOwnershipVeto':self.arbiterOwnershipVeto,'arbiterReauthVeto':self.arbiterReauthVeto,'arbiterNoAction':self.arbiterNoAction,'sameTickCancelAndDecision':self.sameTickCancelAndDecision});return r

def aggregate(rows):
    base=v4.aggregate(rows)
    for k in ['parallelTicks','repairProposalTicks','expandProposalTicks','dualProposalTicks','arbiterOwnershipVeto','arbiterReauthVeto','arbiterNoAction','sameTickCancelAndDecision']:base[k]=int(sum(int(r.get(k) or 0) for r in rows))
    return base

def pnl_summary(rows):
    vals=[float(r.get('pnlDiagnosticOnly') or 0) for r in rows];active=[x for x in vals if abs(x)>1e-12];wins=sum(x>0 for x in active);losses=sum(x<0 for x in active);eq=0.0;peak=0.0;dd=0.0
    for x in vals:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
    return {'markets':len(vals),'activeMarketsPnlNonzero':len(active),'wins':wins,'losses':losses,'flats':len(vals)-len(active),'winRateActive':wins/len(active) if active else None,'totalPnl':sum(vals),'avgPnlAll':sum(vals)/len(vals) if vals else 0.0,'avgPnlActive':sum(active)/len(active) if active else 0.0,'maxWin':max(vals) if vals else 0.0,'maxLoss':min(vals) if vals else 0.0,'maxDrawdown':dd}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_parallel_v1_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);print(json.dumps({'daggerCache':'loaded'}),flush=True)
        life=v3.LifecycleRuntime(Path(a.lifecycle_model));test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx];summaries={};allrows=[]
        for scenario,cfg in ex1.SCENARIOS.items():
            rows=[]
            for i,cr in enumerate(selected,1):
                sim=ParallelControlPlaneSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'])
                try:r=sim.run_exam_v2(models,cr['winner'])
                finally:sim.close()
                r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
                if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'pnl':sum(float(x.get('pnlDiagnosticOnly') or 0) for x in rows)}),flush=True)
            s=aggregate(rows);s['pnl']=pnl_summary(rows);summaries[scenario]=s
        gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'objectiveLifecycleCanComplete':summaries['CONTROL']['objectiveCompletions']>0}
        out={'version':'ETH_REPAIR_PARALLEL_CONTROL_PLANE_V1','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 24-market A/B development cohort','selectedMarketIds':[int(r['marketId']) for r in selected],'architecture':['same-event immutable parallel lane snapshots','execution truth lane','repair proposal lane','expansion proposal lane','ownership/risk veto lane','single commit arbiter','persistent V4 carrier ledger retained'],'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':summaries['CONTROL']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
