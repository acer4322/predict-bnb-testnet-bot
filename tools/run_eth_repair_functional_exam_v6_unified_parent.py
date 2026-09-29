from __future__ import annotations
import argparse,json,statistics,tempfile,zipfile,shutil,sys,importlib.util
from pathlib import Path
import numpy as np,joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def _load_sibling(name,filename):
    p=Path(__file__).resolve().with_name(filename);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
    from tools import run_eth_repair_functional_exam_v4_persistent_carrier_ledger as v4
except ImportError:
    v4=_load_sibling('v4_parent','run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py')
try:
    from tools import eth_persistent_repair_online_capability_runtime as caprt
except ImportError:
    caprt=_load_sibling('caprt_parent','eth_persistent_repair_online_capability_runtime.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1

EPS=1e-9

class UnifiedRepairParentSim(v4.PersistentCarrierSim):
    def __init__(self,*a,capability=None,**kw):
        super().__init__(*a,**kw)
        self.capability=capability
        self.capState=caprt.OnlineCapabilityState()
        self.capEnd=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        self.repairParent=None;self.nextRepairParentId=1
        self.repairParentBirths=0;self.repairParentCompletions=0;self.repairParentInvalidations=0
        self.repairParentTicks=0;self.repairParentPredBelowHalfTicks=0
        self.repairChildCommits=0;self.expandChildCommitsUnderRepairParent=0;self.repairResumeAfterExpandCommits=0
        self.lastCommittedChildRole=None;self.parallelCapabilityTicks=0;self.capabilityNoActionBlocks=0
        self.capPred=[]
    def _append_truth_fill_token(self,t,side,qty,price,pre_inv,pre_cost):
        super()._append_truth_fill_token(t,side,qty,price,pre_inv,pre_cost)
        self.capState.apply_event('MAKER',side,t,qty,price,self.capEnd)
    def _complete_parent_if_structural(self,t,ai):
        p=self.repairParent
        if not p:return
        side=p['side'];opp='DOWN' if side=='UP' else 'UP'
        if side is None or float(ai[side])>=float(ai[opp])-EPS:
            if float(ai[side])>float(ai[opp])+EPS:self.repairParentInvalidations+=1
            self.repairParentCompletions+=1;self.repairParent=None
    def _maybe_birth_parent(self,t,weak,cap):
        if self.repairParent is None and weak is not None and float(cap['repair_obligation_30s'])>=0.5:
            self.repairParent={'id':self.nextRepairParentId,'side':weak,'bornAt':int(t)};self.nextRepairParentId+=1;self.repairParentBirths+=1
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        # Keep the proven execution/debt lifecycle as supporting state, but Repair parent authority comes from one role-invariant capability lane.
        cur,seq,mask,debt,lag=self.lifecycle_features(t,end);oldp=self.lifecycle.predict(cur,seq,mask,debt,lag);self.lifecyclePredictions.append(oldp)
        self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
        ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        self._complete_parent_if_structural(t,ai)
        cs=self.capState.snapshot(t,end);cap=self.capability.predict(cs);self.capPred.append(cap)
        self._maybe_birth_parent(t,weak,cap)
        parent=self.repairParent
        if parent is not None:
            self.repairParentTicks+=1
            if float(cap['repair_obligation_30s'])<0.5:self.repairParentPredBelowHalfTicks+=1
            # Parent side tracks the structural weak side only after old responsibility structurally completed.
            if weak is None:
                self._complete_parent_if_structural(t,ai);parent=self.repairParent
        # Non-exclusive child capabilities. Repair parent survives an EXPAND child; model role changes never kill it.
        repair_available=bool(parent is not None and weak is not None and parent['side']==weak)
        expand_available=bool(dom is not None and float(cap['expand_opportunity_30s'])>=0.5)
        both_authorized=bool(repair_available and expand_available and float(cap['both_responsibilities_30s'])>=0.5)
        chosen_role=None
        if both_authorized and proposed_side==dom:
            chosen_role='EXPAND'
        elif repair_available:
            chosen_role='REPAIR'
        elif expand_available and float(cap['activity_urgency_30s'])>=0.5:
            chosen_role='EXPAND'
        else:
            self.capabilityNoActionBlocks+=1;return None
        chosen_side=weak if chosen_role=='REPAIR' else dom
        if chosen_side is None:return None
        # A child can change only after all previous child carriers are truly resolved.
        if self.activeObjective is None:
            if self.outstanding_total()>EPS:self.incompatibleOwnershipBlocks+=1;return None
            if chosen_role=='EXPAND' and self.awaitingReentry and parent is None:
                if oldp['reentry']<0.5:self.reauthBlocks+=1;return None
                self.awaitingReentry=False
            self.activeObjective=self._new_objective(chosen_role,chosen_side)
        elif self.activeObjective['role']!=chosen_role or self.activeObjective.get('side')!=chosen_side:
            if self.outstanding_total()>EPS:self.incompatibleOwnershipBlocks+=1;return None
            if chosen_role=='EXPAND':
                # Expansion still uses the learned/economic handoff primitive; Repair return does not need a categorical switch gate.
                if not both_authorized and parent is not None:self.reauthBlocks+=1;return None
                if oldp['switch']<0.5 or oldp['reentry']<0.5:self.reauthBlocks+=1;return None
            self.objectiveSwitches+=1;self.activeObjective=self._new_objective(chosen_role,chosen_side)
        self.activeObjective['side']=chosen_side;oid=self.activeObjective['id']
        if self._incompatible_unresolved(oid,chosen_side):self.incompatibleOwnershipBlocks+=1;return None
        qty=float(proposed_qty)
        if chosen_role=='REPAIR':
            gap=max(0.0,abs(float(ai['UP'])-float(ai['DOWN'])));owned=sum(rem for _,_,rem in self.unresolved(side=chosen_side,objective_id=oid));remaining=max(0.0,gap-owned)
            if remaining<=EPS:self.ambiguousOwnershipBlocks+=1;return None
            qty=min(qty,remaining)
        elif self.outstanding_total()>EPS:
            self.incompatibleOwnershipBlocks+=1;return None
        if qty<=EPS:return None
        if chosen_role=='REPAIR':
            self.repairChildCommits+=1
            if self.lastCommittedChildRole=='EXPAND':self.repairResumeAfterExpandCommits+=1
        elif parent is not None:
            self.expandChildCommitsUnderRepairParent+=1
        self.lastCommittedChildRole=chosen_role
        return chosen_side,qty,oldp,chosen_role,oid
    def run_exam_v2(self,models,winner):
        r=super().run_exam_v2(models,winner)
        r.update({'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentInvalidations':self.repairParentInvalidations,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairParentPredBelowHalfTicks':self.repairParentPredBelowHalfTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks})
        return r

def aggregate(rows):
    base=v4.aggregate(rows)
    for k in ['repairParentBirths','repairParentCompletions','repairParentInvalidations','repairParentActiveAtEnd','repairParentTicks','repairParentPredBelowHalfTicks','repairChildCommits','expandChildCommitsUnderRepairParent','repairResumeAfterExpandCommits','parallelCapabilityTicks','capabilityNoActionBlocks']:
        base[k]=int(sum(int(r.get(k) or 0) for r in rows))
    return base

def pnl_summary(rows):
    vals=[float(r.get('pnlDiagnosticOnly') or 0) for r in rows];active=[x for x in vals if abs(x)>1e-12];wins=sum(x>0 for x in active);losses=sum(x<0 for x in active);eq=peak=dd=0.
    for x in vals:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
    return {'markets':len(vals),'activeMarketsPnlNonzero':len(active),'wins':wins,'losses':losses,'flats':len(vals)-len(active),'winRateActive':wins/len(active) if active else None,'totalPnl':sum(vals),'maxWin':max(vals) if vals else 0.,'maxLoss':min(vals) if vals else 0.,'maxDrawdown':dd}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_repair_v6_parent_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);print(json.dumps({'daggerCache':'loaded'}),flush=True)
        life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=caprt.UnifiedCapabilityRuntime(a.capability_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
        summaries={};allrows=[]
        for scenario,cfg in ex1.SCENARIOS.items():
            rows=[]
            for i,cr in enumerate(selected,1):
                sim=UnifiedRepairParentSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap)
                try:r=sim.run_exam_v2(models,cr['winner'])
                finally:sim.close()
                r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
                if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'parentBirths':sum(x['repairParentBirths'] for x in rows),'expandUnderParent':sum(x['expandChildCommitsUnderRepairParent'] for x in rows),'repairResumes':sum(x['repairResumeAfterExpandCommits'] for x in rows)}),flush=True)
            s=aggregate(rows);s['pnl']=pnl_summary(rows);summaries[scenario]=s
        control=summaries['CONTROL'];gates={
            'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),
            'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),
            'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),
            'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),
            'repairParentCanComplete':control['repairParentCompletions']>0,
            'repairParentPersistsDespitePredictionDip':control['repairParentPredBelowHalfTicks']>0,
            'ackLagDoesNotCollapseActivity':summaries['ACK_RELEASE_3000']['meanSubmits']>=0.5*max(control['meanSubmits'],EPS),
            'compoundDoesNotCollapseActivity':summaries['COMPOUND_3000']['meanSubmits']>=0.5*max(control['meanSubmits'],EPS)
        }
        out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V6_UNIFIED_REPAIR_PARENT','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 24-market closed-loop development cohort','selectedMarketIds':[int(r['marketId']) for r in selected],'architecture':['V4 persistent carrier ownership kernel retained','one role-invariant neural Repair Obligation parent lane','Repair parent dies only on structural completion','REPAIR/EXPAND are child roles under persistent parent','EXPAND child may interleave only when both-capability + legacy switch/reentry authorize','actual-fill updates online capability state with exact offline parity'],'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No BTC runtime policy labels/gradients/thresholds/qty','Same consumed development cohort; not graduation','No new model gradients in closed-loop integration','Partial-fill randomized disturbance exam still required after fixed-scenario pass']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':control},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
