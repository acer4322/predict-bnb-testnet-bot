from __future__ import annotations
import argparse, json, math, shutil, sys, tempfile, threading, time, zipfile, os
from pathlib import Path
import joblib
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
    import importlib.util
    q=Path(__file__).with_name('parallel_cycle_capacity.py'); spec=importlib.util.spec_from_file_location('tools.eth_repair_modular.parallel_cycle_capacity',q); mod=importlib.util.module_from_spec(spec); sys.modules['tools.eth_repair_modular.parallel_cycle_capacity']=mod; spec.loader.exec_module(mod); PhaseAdaptiveParallelCycleCapacityPolicy=mod.PhaseAdaptiveParallelCycleCapacityPolicy
from tools.eth_repair_modular.responsibility_transition import ResponsibilityTransitionContext
EPS=1e-9
pe=pg.pe; v80=pg.v80

class ParallelSerializationShadow(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw); self.parallelShadow=[]; self.parallelShadowSeen=set(); self.phaseCapacity=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,4),(0.5,3),(0.9,2)))
    def _ownership_if_needed(self,t,pE,qv):
        th=getattr(self,'thesis',None)
        if th is not None:
            side=self._signal_side(qv); key=(int(t),str(side))
            if key not in self.parallelShadowSeen:
                self.parallelShadowSeen.add(key)
                rec=self._v75_recoverability(t,side,qv)
                phase=min(1.0,max(0.0,(int(t)-(int(self.capEnd)-300000))/300000.0))
                px=float(qv[side]['bid']) if side in ('UP','DOWN') else 0.0; qty=(1.0/px if px>EPS else math.inf)
                debt,debt_rows=self._live_repair_debt_by_side()
                td=self.transitionPolicy.evaluate(ResponsibilityTransitionContext(side,float(debt['UP']),float(debt['DOWN'])))
                # Two views: legacy ownership policy (contains old fixed-seconds fence) and phase-native structural candidate.
                legacy=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=False,p_expand=float(pE),signal_side=side,recoverable=bool(rec.get('recoverable')))
                legacy_dec=self.policyProfile.ownership.evaluate(legacy)
                venue=bool(math.isfinite(qty) and qty>EPS and qty<=12.+EPS)
                structural=bool(float(pE)>=.5 and side in ('UP','DOWN') and bool(rec.get('recoverable')) and venue)
                cap=self.phaseCapacity.capacity_at(phase); second_slot=cap>=2
                self.parallelShadow.append({'t':int(t),'normalizedPhase':phase,'existingThesisId':th.get('id'),'existingThesisSide':th.get('side'),'candidateSide':side,'pExpand':float(pE),'recoverable':bool(rec.get('recoverable')),'recoverabilityReason':rec.get('reason'),'price':px,'venueMinQty':qty if math.isfinite(qty) else None,'venueMinFeasible':venue,'legacyOwnershipAllow':bool(legacy_dec.create_thesis),'legacyOwnershipReason':legacy_dec.reason,'phaseNativeStructuralCandidate':structural,'developmentCapacity':cap,'developmentCapacityAllowsSecondSlot':second_slot,'currentGlobalTransitionAllow':bool(td.allow_expand_ownership),'currentGlobalTransitionRole':td.bind_role,'currentGlobalTransitionReason':td.reason,'repairDebtBySide':dict(debt),'debtRows':debt_rows[:8]})
            return None
        return super()._ownership_if_needed(t,pE,qv)
    def run_shadow(self,models,winner):
        r=self.run_guard(models,winner); r['parallelSerializationShadow']=self.parallelShadow; return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='AUTO');a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='parallel_serial_shadow_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'PARALLEL_SERIAL_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'PARALLEL_SERIAL_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); by={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid];sim=pe.make(ParallelSerializationShadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=sim.run_shadow(models,cr['winner']); sh=list(sim.parallelShadow)
            finally:sim.close()
            structural=[x for x in sh if x['phaseNativeStructuralCandidate'] and x['developmentCapacityAllowsSecondSlot']]
            trans_block=[x for x in structural if not x['currentGlobalTransitionAllow']]
            legacy_late=[x for x in structural if x['legacyOwnershipReason']=='LATE']
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'checksWhileExistingThesis':len(sh),'phaseNativeIndependentSlotCandidates':len(structural),'currentGlobalTransitionBlocksAmongCandidates':len(trans_block),'legacyHardTimeBlocksAmongCandidates':len(legacy_late),'candidatePhaseMin':min((x['normalizedPhase'] for x in structural),default=None),'candidatePhaseMedian':sorted([x['normalizedPhase'] for x in structural])[len(structural)//2] if structural else None,'candidatePhaseMax':max((x['normalizedPhase'] for x in structural),default=None),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'sampleCandidates':structural[:30]})
            print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'checks':len(sh),'parallelCandidates':len(structural),'transitionBlocks':len(trans_block),'legacyLateBlocks':len(legacy_late),'fills':rows[-1]['fills'],'rounds':rows[-1]['rounds']},ensure_ascii=False),flush=True)
        total=sum(x['phaseNativeIndependentSlotCandidates'] for x in rows); trans=sum(x['currentGlobalTransitionBlocksAmongCandidates'] for x in rows); late=sum(x['legacyHardTimeBlocksAmongCandidates'] for x in rows)
        active=sum(x['fills']>0 for x in rows); markets_with=sum(x['phaseNativeIndependentSlotCandidates']>0 for x in rows)
        out={'version':'PARALLEL_CYCLE_SERIALIZATION_OPPORTUNITY_SHADOW_LATEST8_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'behaviorChange':False,'marketIds':mids,
             'summary':{'markets':len(rows),'executionActiveMarkets':active,'marketsWithIndependentSlotCandidate':markets_with,'phaseNativeIndependentSlotCandidates':total,'currentGlobalTransitionBlocksAmongCandidates':trans,'legacyHardTimeBlocksAmongCandidates':late},
             'decision':'SUPPORT_TWO_SLOT_INTEGRATION_SMOKE' if markets_with>=2 and total>=4 else 'SERIALIZATION_NOT_YET_DOMINANT_OR_NEEDS_MORE_COVERAGE','rows':rows,
             'boundary':['behavior inert','same pExpand model and frozen .50 opportunity threshold','same V75 recoverability and venue-min sizing','normalized phase reported separately from inherited fixed-seconds ownership fence','development capacity 4/3/2 is shadow-only','no Target runtime input','no dream fill','no 8781']}
        outpath=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output=='AUTO' else Path(a.output);outpath.parent.mkdir(parents=True,exist_ok=True);outpath.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
