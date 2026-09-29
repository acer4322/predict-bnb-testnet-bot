from __future__ import annotations
import argparse, collections, json, math, os, shutil, sys, tempfile, zipfile
from pathlib import Path
import joblib

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))

import importlib.util
def _sib(name,filename):
    q=Path(__file__).resolve().with_name(filename); sp=importlib.util.spec_from_file_location(name,q)
    if sp is None or sp.loader is None: raise ImportError(q)
    m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m); return m
allocrun=_sib('exact_policy_allocrun','run_eth_repair_modular_allocation_v2_generic_hft.py')
from tools.eth_repair_modular.profile import ModularPolicyProfile
from tools.eth_repair_modular.completion import LegacyShareGapCompletionPolicy, EconomicResponsibilityCompletionPolicy
from tools.eth_repair_modular.handoff import LegacyAlwaysAllowActiveHandoffPolicy, RecoverabilityActiveHandoffPolicy
from tools.eth_repair_modular.generation import LegacyRecursiveGenerationPolicy, SingleResponsibilityGenerationPolicy
from tools.eth_repair_modular.scheduler import ContinuousResponsibilitySchedulerPolicy, EventCompletedOnlySchedulerPolicy
from tools.eth_repair_modular.ownership import RecoverablePreSafeOwnershipPolicy
from tools.eth_repair_modular.contracts import OwnershipDecision
routerv2=_sib('exact_policy_routerv2','repair_execution_router_v2.py')

EPS=1e-9
MID=1912961

class PermissiveOwnershipPolicy:
    name='minimal_signal_side_ownership_v1'
    def evaluate(self,ctx):
        if ctx.has_thesis:return OwnershipDecision(False,None,'EXISTING_THESIS')
        if ctx.seconds_left<=180.0:return OwnershipDecision(False,None,'LATE')
        if ctx.signal_side not in ('UP','DOWN'):return OwnershipDecision(False,None,'NO_SIGNAL_SIDE')
        return OwnershipDecision(True,ctx.signal_side,'MINIMAL_SIGNAL_SIDE_BIRTH')

class MinimalRepairExecutionRouter:
    name='minimal_existing_debt_physical_router_v1'
    def evaluate(self,ctx):
        D=routerv2.RepairExecutionDecisionV2
        if ctx.parent_id is None or ctx.parent_side not in ('UP','DOWN'):
            return D(False,0.0,'NO_REPAIR_PARENT')
        if not ctx.overflow_born_parent:
            return D(False,0.0,'ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION')
        if ctx.active_already_owned or ctx.hard_confirmed:
            return D(False,0.0,'ACTIVE_ALREADY_OWNED')
        if ctx.seconds_left<=180.0:
            return D(False,0.0,'LATE_NO_NEW_ACTIVE_EXPOSURE')
        if ctx.manager_debt<=EPS:
            return D(False,0.0,'NO_MANAGER_DEBT')
        if ctx.live_ask is None or ctx.legal_physical_qty is None:
            return D(False,0.0,'NO_EXECUTABLE_ACTIVE_FRONTIER')
        q=float(ctx.legal_physical_qty)
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:
            return D(False,0.0,'ILLEGAL_PHYSICAL_SLICE')
        return D(True,q,'MINIMAL_EXISTING_DEBT_ACTIVE_ALLOW')

def profile(label,ownership,generation,handoff,scheduler,completion):
    return ModularPolicyProfile(
        name=label,
        completion=completion,
        ownership=ownership,
        handoff=handoff,
        generation=generation,
        repair_execution=None,
        scheduler=scheduler,
    )

def cells():
    own_min=PermissiveOwnershipPolicy(); own_safe=RecoverablePreSafeOwnershipPolicy()
    gen_min=LegacyRecursiveGenerationPolicy(); gen_safe=SingleResponsibilityGenerationPolicy()
    hand_min=LegacyAlwaysAllowActiveHandoffPolicy(); hand_safe=RecoverabilityActiveHandoffPolicy()
    sched_min=ContinuousResponsibilitySchedulerPolicy(); sched_safe=EventCompletedOnlySchedulerPolicy()
    comp_safe=EconomicResponsibilityCompletionPolicy()
    rmin=MinimalRepairExecutionRouter(); rsafe=routerv2.RecursiveCompositeRepairExecutionRouterV2()
    return [
        ('Q0_MINIMAL_WITH_ECONOMIC_COMPLETION', profile('Q0_MINIMAL_WITH_ECONOMIC_COMPLETION',own_min,gen_min,hand_min,sched_min,comp_safe), rmin),
        ('Q1_ADD_OWNERSHIP_RECOVERABILITY', profile('Q1_ADD_OWNERSHIP_RECOVERABILITY',own_safe,gen_min,hand_min,sched_min,comp_safe), rmin),
        ('Q2_ADD_SINGLE_GENERATION', profile('Q2_ADD_SINGLE_GENERATION',own_safe,gen_safe,hand_min,sched_min,comp_safe), rmin),
        ('Q3_ADD_HANDOFF_RECOVERABILITY', profile('Q3_ADD_HANDOFF_RECOVERABILITY',own_safe,gen_safe,hand_safe,sched_min,comp_safe), rmin),
        ('Q4_ADD_REPAIR_EXECUTION_HARD_CHAIN', profile('Q4_ADD_REPAIR_EXECUTION_HARD_CHAIN',own_safe,gen_safe,hand_safe,sched_min,comp_safe), rsafe),
        ('Q5_ADD_EVENT_ONLY_SCHEDULER', profile('Q5_ADD_EVENT_ONLY_SCHEDULER',own_safe,gen_safe,hand_safe,sched_safe,comp_safe), rsafe),
    ]

def summarize(label,r,sim):
    ss=allocrun.safety(r)
    parents=r.get('allocationV2Parents') or {}
    cons=abs(float(r.get('v84CompositeFillQty') or 0)-float(r.get('v84RepairAllocatedQty') or 0)-float(r.get('v84OverflowAllocatedQty') or 0))<=1e-7
    bounded=all(float(x.get('repairPaid') or 0)<=float(x.get('initialDebt') or 0)+1e-7 and float(x.get('remainingDebt') or 0)>=-EPS for x in parents.values())
    own_counts=collections.Counter(str(x.get('decision') or x.get('reason') or '') for x in (r.get('v80OwnershipEvents') or []))
    hand_counts=collections.Counter(str(x.get('decision') or x.get('reason') or '') for x in (r.get('v80HandoffEvents') or []))
    rex_counts=collections.Counter(str(x.get('decision') or x.get('reason') or '') for x in (r.get('modularRepairExecutionEvents') or []))
    return {
        'cell':label,
        'submits':int(r.get('submits') or 0),
        'fills':int(r.get('actualFillEvents') or 0),
        'semanticRounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),
        'repairParentBirths':int(r.get('repairParentBirths') or 0),
        'repairParentCompletions':int(r.get('repairParentCompletions') or 0),
        'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),
        'floor':float(r.get('floor') or 0.0),
        'best':float(r.get('bestPnl') or r.get('best') or 0.0),
        'v44Submits':int(r.get('v44Submits') or 0),
        'generationBirths':int(r.get('v48GenerationBirths') or 0),
        'generationDebtQty':float(r.get('v48GenerationDebtQty') or 0.0),
        'generationPaidQty':float(r.get('v48GenerationPaidQty') or 0.0),
        'activeCompositeSubmits':int(r.get('modularActiveCompositeSubmits') or 0),
        'activeCompositeFillQty':float(r.get('modularActiveCompositeFillQty') or 0.0),
        'overflowAllocated':float(r.get('v84OverflowAllocatedQty') or 0.0),
        'overflowPaid':float(r.get('v84OverflowPaidQty') or 0.0),
        'allocationConservation':bool(cons),
        'allocationParentDebtBounded':bool(bounded),
        'safetyZero':all(float(v or 0)<=EPS for v in ss.values()),
        'safety':ss,
        'policyProfile':r.get('modularRepairExecutionProfile') or r.get('v80PolicyProfile'),
        'ownershipReasonCounts':dict(own_counts),
        'handoffReasonCounts':dict(hand_counts),
        'repairExecutionReasonCounts':dict(rex_counts),
        'managementCompletions':int(r.get('v80ManagementCompletions') or 0),
        'shareRepairSettlements':int(r.get('v80ShareRepairSettlements') or 0),
        'completionDeferrals':int(r.get('v80CompletionDeferrals') or 0),
    }

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID); ap.add_argument('--output',required=True); a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='exact_policy_reintro_1912961_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=allocrun.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{MID}.json.xz'
        rows=[]
        for label,prof,router in cells():
            sim=allocrun.ModularAllocationLedgerV2(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=prof,repair_execution_router=router)
            try:r=sim.run_allocation_v2(models,cr['winner']); row=summarize(label,r,sim)
            finally:sim.close()
            rows.append(row)
            print(json.dumps({'progress':label,'submits':row['submits'],'fills':row['fills'],'rounds':row['semanticRounds'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'births':row['repairParentBirths'],'completions':row['repairParentCompletions'],'activeSubmits':row['activeCompositeSubmits'],'safe':row['safetyZero'],'own':row['ownershipReasonCounts'],'rex':row['repairExecutionReasonCounts']},ensure_ascii=False),flush=True)
        b=rows[0]
        for row in rows:
            row['submitRetentionVsP0']=float(row['submits']/b['submits']) if b['submits'] else None
            row['fillRetentionVsP0']=float(row['fills']/b['fills']) if b['fills'] else None
            row['roundRetentionVsP0']=float(row['semanticRounds']/b['semanticRounds']) if b['semanticRounds'] else None
            row['pnlDeltaVsP0']=row['pnlDiagnosticOnly']-b['pnlDiagnosticOnly']; row['floorDeltaVsP0']=row['floor']-b['floor']
        out={'version':'ETH_EXACT_POLICY_SAFETY_REINTRODUCTION_1912961_V2','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'rows':rows,'boundary':['exact existing management policy classes added cumulatively on ModularAllocationLedgerV2 substrate','Q0 keeps EconomicResponsibilityCompletion fixed ON; permissive ownership/router remove behavior vetoes while accounting/identity/AllocationLedger/physical legality/<=180 remain','Q1 exact RecoverablePreSafeOwnershipPolicy','Q2 exact SingleResponsibilityGenerationPolicy','Q3 exact RecoverabilityActiveHandoffPolicy','Q4 exact RecursiveCompositeRepairExecutionRouterV2','Q5 exact EventCompletedOnlySchedulerPolicy','consumed development market diagnostic only','realistic HFT','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'rows':[{k:x[k] for k in ['cell','submits','fills','semanticRounds','repairParentBirths','repairParentCompletions','pnlDiagnosticOnly','floor','activeCompositeSubmits','activeCompositeFillQty','submitRetentionVsP0','fillRetentionVsP0','safetyZero']} for x in rows]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
