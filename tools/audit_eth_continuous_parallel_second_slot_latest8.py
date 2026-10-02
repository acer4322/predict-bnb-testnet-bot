from __future__ import annotations
import argparse, json, math, shutil, sys, tempfile, threading, time, zipfile, os
from pathlib import Path
import joblib, numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
import importlib, importlib.util

def _load_or_staged(fullname, filename):
    print(f'IMPORT_STAGE {fullname} START', flush=True)
    try:
        m=importlib.import_module(fullname); print(f'IMPORT_STAGE {fullname} PASS project', flush=True); return m
    except ImportError:
        q=Path(__file__).with_name(filename); spec=importlib.util.spec_from_file_location(fullname,q)
        if spec is None or spec.loader is None: raise ImportError(q)
        m=importlib.util.module_from_spec(spec); sys.modules[fullname]=m; spec.loader.exec_module(m); print(f'IMPORT_STAGE {fullname} PASS staged', flush=True); return m

_load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
_load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
_load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
_load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=_load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
try:
    from tools.eth_repair_modular.parallel_cycle_capacity import PhaseAdaptiveParallelCycleCapacityPolicy
except ImportError:
    q=Path(__file__).with_name('parallel_cycle_capacity.py'); spec=importlib.util.spec_from_file_location('tools.eth_repair_modular.parallel_cycle_capacity',q)
    mod=importlib.util.module_from_spec(spec); sys.modules['tools.eth_repair_modular.parallel_cycle_capacity']=mod; spec.loader.exec_module(mod); PhaseAdaptiveParallelCycleCapacityPolicy=mod.PhaseAdaptiveParallelCycleCapacityPolicy
from tools.eth_repair_modular.responsibility_transition import ResponsibilityTransitionContext
EPS=1e-9
pe=pg.pe

class ContinuousParallelSecondSlotShadow(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.phaseCapacity=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,4),(0.5,3),(0.9,2)))
        self.continuousSlotRows=[]; self.continuousSlotSeen=set(); self._continuousSlotBusy=False

    def _continuous_parallel_slot_check(self,t):
        if self._continuousSlotBusy: return
        th=getattr(self,'thesis',None)
        if not isinstance(th,dict): return
        qv=pe.v38.v36.v34.v30.v1.quotes(self.book)
        if not qv or getattr(self,'teacher',None) is None: return
        key=(int(t), int(th.get('id') or -1), self.n, int(getattr(self,'actualFillEvents',0) or 0))
        if key in self.continuousSlotSeen: return
        self.continuousSlotSeen.add(key); self._continuousSlotBusy=True
        try:
            f=self._coord_feature(int(t))
            x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32)
            pE=float(self.teacher['model'].predict_proba(x)[0,1])
            side=self._signal_side(qv)
            rec=self._v75_recoverability(int(t),side,qv)
            px=float(qv[side]['bid']) if side in ('UP','DOWN') else 0.0
            qty=(1.0/px if px>EPS else math.inf)
            venue=bool(math.isfinite(qty) and qty>EPS and qty<=12.+EPS)
            phase=min(1.0,max(0.0,(int(t)-(int(self.capEnd)-300000))/300000.0))
            cap=self.phaseCapacity.capacity_at(phase)
            debt,debt_rows=self._live_repair_debt_by_side()
            td=self.transitionPolicy.evaluate(ResponsibilityTransitionContext(side,float(debt['UP']),float(debt['DOWN'])))
            structural=bool(pE>=.5 and side in ('UP','DOWN') and bool(rec.get('recoverable')) and venue and cap>=2)
            self.continuousSlotRows.append({
                't':int(t),'normalizedPhase':phase,'existingThesisId':th.get('id'),'existingThesisSide':th.get('side'),
                'candidateSide':side,'pExpand':pE,'recoverable':bool(rec.get('recoverable')),'recoverabilityReason':rec.get('reason'),
                'price':px,'venueMinQty':qty if math.isfinite(qty) else None,'venueMinFeasible':venue,'developmentCapacity':cap,
                'phaseCapacityAllowsSecondSlot':cap>=2,'phaseNativeStructuralCandidate':structural,
                'currentGlobalTransitionAllow':bool(td.allow_expand_ownership),'currentGlobalTransitionRole':td.bind_role,
                'currentGlobalTransitionReason':td.reason,'repairDebtBySide':dict(debt),'debtRows':debt_rows[:6]
            })
        finally:
            self._continuousSlotBusy=False

    def process(self,t):
        r=super().process(t)
        self._continuous_parallel_slot_check(int(t))
        return r

    def run_shadow(self,models,winner):
        r=self.run_guard(models,winner)
        r['continuousParallelSecondSlotRows']=self.continuousSlotRows
        return r

def run_stats(rows):
    cand=[r for r in rows if r['phaseNativeStructuralCandidate']]
    runs=[]; cur=0; last_t=None
    for r in rows:
        if r['phaseNativeStructuralCandidate']:
            if last_t is None or int(r['t'])>=int(last_t): cur+=1
            else: cur=1
            last_t=int(r['t'])
        else:
            if cur>0:runs.append(cur)
            cur=0; last_t=None
    if cur>0:runs.append(cur)
    return cand,runs

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True); ap.add_argument('--output',default='AUTO'); a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='continuous_parallel_second_slot_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'CONTINUOUS_SECOND_SLOT','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'CONTINUOUS_SECOND_SLOT_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); by={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        outrows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid]; sim=pe.make(ContinuousParallelSecondSlotShadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=sim.run_shadow(models,cr['winner']); sh=list(sim.continuousSlotRows)
            finally:sim.close()
            cand,runs=run_stats(sh); trans=sum(not x['currentGlobalTransitionAllow'] for x in cand)
            phases=[x['normalizedPhase'] for x in cand]
            outrows.append({'marketId':mid,'checksWhileExistingThesis':len(sh),'candidateClocks':len(cand),'candidateRuns':len(runs),'longestCandidateRun':max(runs,default=0),'transitionBlocksAmongCandidates':trans,'candidatePhaseMin':min(phases,default=None),'candidatePhaseMedian':sorted(phases)[len(phases)//2] if phases else None,'candidatePhaseMax':max(phases,default=None),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'sampleCandidates':cand[:40]})
            print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'checks':len(sh),'candidates':len(cand),'runs':len(runs),'longestRun':max(runs,default=0),'fills':outrows[-1]['fills'],'rounds':outrows[-1]['rounds']},ensure_ascii=False),flush=True)
        total=sum(x['candidateClocks'] for x in outrows); mw=sum(x['candidateClocks']>0 for x in outrows); rich=sum((x['candidateClocks']>=3 or x['longestCandidateRun']>=2) for x in outrows)
        support=bool(mw>=2 and total>=8 and rich>=1)
        out={'version':'CONTINUOUS_PARALLEL_SECOND_SLOT_OPPORTUNITY_SHADOW_LATEST8_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'behaviorChange':False,'marketIds':mids,'summary':{'markets':len(outrows),'marketsWithCandidate':mw,'independentSecondSlotCandidateClocks':total,'marketsWithRepeatedCandidateEvidence':rich,'transitionBlocksAmongCandidates':sum(x['transitionBlocksAmongCandidates'] for x in outrows)},'decision':'SUPPORT_TWO_SLOT_REALISTIC_HFT_SMOKE' if support else 'DO_NOT_OPEN_ECONOMIC_CONCURRENCY_YET','rows':outrows,'boundary':['continuous strict-past instrumentation only','no order submit from shadow','same frozen pExpand .50 + V75 recoverability + venue-min geometry','development 4/3/2 capacity diagnostic only','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
