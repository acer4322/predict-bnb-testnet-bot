from __future__ import annotations
import argparse, copy, json, math, statistics, tempfile, zipfile, shutil, sys
from pathlib import Path
import numpy as np, joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import importlib.util
V4_PATH=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py')
spec=importlib.util.spec_from_file_location('eth_v4_parallel_base',V4_PATH)
v4=importlib.util.module_from_spec(spec);spec.loader.exec_module(v4)
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1

EPS=1e-9

class ParallelCarrierSim(v4.PersistentCarrierSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.arbiterConflictCount=0
        self.parallelCommits=0
        self.parallelBlocks=0
        self.parallelSnapshots=0

    def _snapshot_lanes(self,t,end):
        # Observational updates are allowed before snapshot formation; no policy/objective commit here.
        self._refresh_carrier_ledger(t)
        cur,seq,mask,debt,lag=self.lifecycle_features(t,end)
        p=self.lifecycle.predict(cur,seq,mask,debt,lag)
        self.lifecyclePredictions.append(p)
        ai=self.auth_inv()
        weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None
        dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        gap=abs(float(ai['UP'])-float(ai['DOWN'])) if weak else 0.0
        obj=copy.deepcopy(self.activeObjective)
        outstanding=self.outstanding_total()
        owned_by_side={s:self.reserved_authoritative(s) for s in ('UP','DOWN')}
        desired='EXPAND' if weak is None else ('REPAIR' if p['repair']>=0.5 else 'EXPAND')
        snap={
            'truth':{'ai':ai,'weak':weak,'dom':dom,'gap':gap},
            'policy':{'p':p,'desiredRole':desired},
            'responsibility':{
                'activeObjective':obj,'outstandingTotal':outstanding,'ownedBySide':owned_by_side,
                'unresolved':[(k,dict(e),float(rem)) for k,e,rem in self.unresolved()]
            },
            'capacity':{'proposedOutstanding':outstanding}
        }
        self.parallelSnapshots+=1
        return snap

    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        s=self._snapshot_lanes(t,end)
        truth=s['truth']; policy=s['policy']; resp=s['responsibility']; p=policy['p']
        weak=truth['weak']; dom=truth['dom']; desired=policy['desiredRole']
        obj=resp['activeObjective']

        # Arbiter phase 1: structural completion based on the frozen authoritative snapshot.
        if obj and obj.get('role')=='REPAIR':
            side=obj.get('side'); opp='DOWN' if side=='UP' else 'UP'
            ai=truth['ai']
            if side is None or ai[side]>=ai[opp]-EPS:
                oid=obj['id']; self.objectiveCompletions+=1
                if side is not None and ai[side]>ai[opp]+EPS:self.objectiveInvalidations+=1
                self.activeObjective=None; self.awaitingReentry=True
                self._cancel_objective_carriers(t,oid)
                obj=None

        # Capacity reconciliation is a ledger-side commit, not a policy decision.
        self._continuous_capacity_reconcile(t)
        outstanding=self.outstanding_total()

        # Arbiter phase 2: combine policy proposal with responsibility ownership.
        if obj is None:
            if outstanding>EPS:
                self.incompatibleOwnershipBlocks+=1; self.parallelBlocks+=1; return None
            if desired=='EXPAND' and self.awaitingReentry:
                if p['reentry']<0.5:
                    self.reauthBlocks+=1; self.parallelBlocks+=1; return None
                self.awaitingReentry=False
            self.activeObjective=self._new_objective(desired,weak if desired=='REPAIR' else dom)
            obj=self.activeObjective
        elif obj['role']!=desired:
            self.arbiterConflictCount+=1
            allow=False
            if obj['role']=='REPAIR' and desired=='EXPAND':allow=(p['switch']>=0.5 and p['reentry']>=0.5)
            elif obj['role']=='EXPAND' and desired=='REPAIR':allow=(p['switch']>=0.5 and p['repair']>=0.5)
            if allow:
                if outstanding>EPS:
                    self.incompatibleOwnershipBlocks+=1; self.parallelBlocks+=1; return None
                self.objectiveSwitches+=1
                self.activeObjective=self._new_objective(desired,weak if desired=='REPAIR' else dom)
                obj=self.activeObjective
            else:
                self.reauthBlocks+=1
                desired=obj['role']

        side=weak if desired=='REPAIR' else dom
        if side is None:
            self.parallelBlocks+=1; return None
        self.activeObjective['side']=side
        oid=self.activeObjective.get('id')

        incompatible=self._incompatible_unresolved(oid,side)
        if incompatible:
            self.incompatibleOwnershipBlocks+=1; self.parallelBlocks+=1; return None

        qty=float(proposed_qty)
        if desired=='REPAIR':
            # Re-read only materialized execution truth after reconciliation; policy remains from the frozen snapshot.
            ai=self.auth_inv(); gap=max(0.0,abs(float(ai['UP'])-float(ai['DOWN'])))
            owned=sum(rem for _,_,rem in self.unresolved(side=side,objective_id=oid))
            remaining=max(0.0,gap-owned)
            if remaining<=EPS:
                self.ambiguousOwnershipBlocks+=1; self.parallelBlocks+=1; return None
            qty=min(qty,remaining)
        elif self.outstanding_total()>EPS:
            self.incompatibleOwnershipBlocks+=1; self.parallelBlocks+=1; return None

        self.parallelCommits+=1
        return side,qty,p,desired,oid

    def run_exam_v2(self,models,winner):
        r=super().run_exam_v2(models,winner)
        r.update({
            'arbiterConflictCount':self.arbiterConflictCount,
            'parallelCommits':self.parallelCommits,
            'parallelBlocks':self.parallelBlocks,
            'parallelSnapshots':self.parallelSnapshots,
        })
        return r

def perf(rows):
    pnl=[float(r['pnlDiagnosticOnly']) for r in rows]
    active=[x for x in pnl if abs(x)>1e-12]
    wins=sum(x>0 for x in pnl);losses=sum(x<0 for x in pnl);flats=sum(abs(x)<=1e-12 for x in pnl)
    eq=0.0;peak=0.0;mdd=0.0
    for x in pnl:
        eq+=x;peak=max(peak,eq);mdd=max(mdd,peak-eq)
    pos=sum(x for x in pnl if x>0);neg=-sum(x for x in pnl if x<0)
    return {
        'markets':len(rows),'activeMarkets':len(active),'wins':wins,'losses':losses,'flats':flats,
        'activeWinRate':wins/max(1,wins+losses),'totalPnl':sum(pnl),'avgPnl':statistics.mean(pnl) if pnl else 0.0,
        'maxWin':max(pnl) if pnl else 0.0,'maxLoss':min(pnl) if pnl else 0.0,'maxDrawdown':mdd,
        'profitFactor':pos/neg if neg>0 else None,'meanSubmits':statistics.mean(float(r['submits']) for r in rows) if rows else 0.0,
        'totalBuyNotional':sum(float(r['buyNotional']) for r in rows),
        'repairToExpandAtFirstFill':sum(int(r['repairToExpandAtFirstFill']) for r in rows),
        'overOwnedSubmitViolations':sum(int(r.get('overOwnedSubmitViolations',0)) for r in rows),
        'authorizedSubmitWithTruthRoleMismatch':sum(int(r['authorizedSubmitWithTruthRoleMismatch']) for r in rows),
        'objectiveCompletions':sum(int(r['objectiveCompletions']) for r in rows),
        'partialFillEvents':sum(int(r['partialFillEvents']) for r in rows),
        'lateFillAfterCancelEvents':sum(int(r['lateFillAfterCancelEvents']) for r in rows),
        'arbiterConflictCount':sum(int(r.get('arbiterConflictCount',0)) for r in rows),
        'parallelCommits':sum(int(r.get('parallelCommits',0)) for r in rows),
        'parallelBlocks':sum(int(r.get('parallelBlocks',0)) for r in rows),
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_repair_parallel_ab_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']
        models,off1,off2=joblib.load(a.dagger_cache)
        life=v4.v3.LifecycleRuntime(Path(a.lifecycle_model))
        test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
        cfg=ex1.SCENARIOS['CONTROL']
        outrows={'V4_SERIAL':[],'V5_PARALLEL':[]}
        for label,cls in [('V4_SERIAL',v4.PersistentCarrierSim),('V5_PARALLEL',ParallelCarrierSim)]:
            for i,cr in enumerate(selected,1):
                sim=cls(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'])
                try:r=sim.run_exam_v2(models,cr['winner'])
                finally:sim.close()
                r.update({'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});outrows[label].append(r)
                if i%8==0:
                    print(json.dumps({'variant':label,'progress':i,'pnl':sum(float(x['pnlDiagnosticOnly']) for x in outrows[label]),'active':sum(abs(float(x['pnlDiagnosticOnly']))>1e-12 for x in outrows[label]),'repairToExpand':sum(int(x['repairToExpandAtFirstFill']) for x in outrows[label])}),flush=True)
        metrics={k:perf(v) for k,v in outrows.items()}
        common=[]
        for arow,brow in zip(outrows['V4_SERIAL'],outrows['V5_PARALLEL']):
            common.append({'marketId':arow['marketId'],'v4Pnl':arow['pnlDiagnosticOnly'],'v5Pnl':brow['pnlDiagnosticOnly'],'v4Buy':arow['buyNotional'],'v5Buy':brow['buyNotional'],'v4Submits':arow['submits'],'v5Submits':brow['submits']})
        safety=(metrics['V5_PARALLEL']['repairToExpandAtFirstFill']==0 and metrics['V5_PARALLEL']['overOwnedSubmitViolations']==0 and metrics['V5_PARALLEL']['authorizedSubmitWithTruthRoleMismatch']==0)
        participation_ratio=metrics['V5_PARALLEL']['activeMarkets']/max(1,metrics['V4_SERIAL']['activeMarkets'])
        out={'version':'ETH_REPAIR_V5_PARALLEL_LANES_AB','researchOnly':True,'cohort':'consumed Fresh101 CONTROL realistic-HFT 24-market A/B','selectedMarketIds':[int(r['marketId']) for r in selected],
             'architecture':{'V4_SERIAL':'serial lifecycle→objective→ownership→capacity chain','V5_PARALLEL':'same-timestamp frozen snapshot lanes: execution truth + lifecycle policy + responsibility + risk/capacity → single arbiter commit'},
             'metrics':metrics,'safetyPass':safety,'participationRatio':participation_ratio,'commonMarkets':common,'rows':outrows,
             'boundary':['No dream fill','No retraining','No threshold/quantity/delay tuning','Parallelization isolated from economic policy changes','Development evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'safetyPass':safety,'participationRatio':participation_ratio,'metrics':metrics},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
