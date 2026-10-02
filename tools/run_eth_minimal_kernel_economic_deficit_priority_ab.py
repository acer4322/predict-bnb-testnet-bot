from __future__ import annotations
import argparse, json, math, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib, numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

import tools.run_eth_frontier_disconnected_existing_debt_active_ab_1945869 as fd
from tools.eth_repair_modular.portfolio_economic_deficit_priority import (
    PortfolioEconomicDeficitPriorityV1, PortfolioEconomicDeficitPriorityContext,
)
from tools.eth_repair_modular.multi_slot_bundle_guard import (
    MultiSlotBundleRecoverabilityPolicyV1, MultiSlotBundleRecoverabilityContext,
    PendingExpandLeg, OwnedRepairLeg,
)

pe=fd.pe
qfn=fd.g2.prev.fcr.basev1.quotes

class MinimalKernelEconomicDeficitPriorityHFT(fd.FrontierDisconnectedSalvageHFT):
    def __init__(self,*a,**kw):
        self.portfolioDeficitPriority=PortfolioEconomicDeficitPriorityV1()
        self.bundleProjection=MultiSlotBundleRecoverabilityPolicyV1()
        self.portfolioPriorityChecks=0;self.portfolioPriorityAllows=0;self.portfolioPriorityBlocks=0
        self.portfolioPriorityEvents=[]
        super().__init__(*a,**kw)

    def _owned_repair_legs(self,repair_side):
        legs=[];rows=[]
        try: src=self.lane_unresolved('REPAIR')
        except Exception: src=[]
        for key,e,rem in src:
            rem=float(rem or 0.0)
            if rem<=EPS or str(e.get('side') or '').upper()!=repair_side: continue
            o=getattr(self,'orders',{}).get(str(key),{})
            px=float(o.get('price') or e.get('price') or 0.0)
            if not math.isfinite(px) or px<=EPS or px>=1.0-EPS: continue
            legs.append(OwnedRepairLeg(rem,px));rows.append({'key':str(key),'qty':rem,'price':px})
        return tuple(legs),rows

    def _portfolio_expand_preflight(self,t,after_kind):
        # Mirror V83 prerequisites only far enough to identify a candidate that
        # would otherwise reach its Expand submit seam. No new authority here.
        if after_kind!='REPAIR' or float(getattr(self,'_coordDebt',0.0) or 0.0)<=EPS or getattr(self,'repairParent',None) is None or getattr(self,'teacher',None) is None:
            return None
        # Match the authoritative V83 admission revision before evaluating the
        # portfolio priority. Without this, stale generation occupancy can make
        # the preflight silently skip while V83 advances and submits afterward.
        if hasattr(self,'_refresh_carrier_ledger_no_v70d'):
            self._refresh_carrier_ledger_no_v70d()
        else:
            self._refresh_carrier_ledger(int(t))
        if hasattr(self,'_maybe_advance_generation'):
            self._maybe_advance_generation(int(t))
        f=self._coord_feature(int(t));x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32)
        pE=float(self.teacher['model'].predict_proba(x)[0,1])
        if pE<.5 or int(self.capEnd)-int(t)<=180000: return None
        qv=qfn(self.book)
        if not qv: return None
        # V83 ownership is authoritative and occurs before its Expand submit
        # checks. Reuse that exact ownership decision here so portfolio priority
        # evaluates the same candidate side instead of skipping on thesis=None.
        if hasattr(self,'_ownership_if_needed'):
            self._ownership_if_needed(int(t),pE,qv)
        if hasattr(self,'_refresh_carrier_ledger_no_v70d'):
            self._refresh_carrier_ledger_no_v70d()
        if hasattr(self,'_maybe_advance_generation'):
            self._maybe_advance_generation(int(t))
        if bool(getattr(self,'v70gGenerationAuthorized',False)): return None
        try:
            if self._expand_occupied(): return None
        except Exception: pass
        th=getattr(self,'thesis',None);side=th.get('side') if isinstance(th,dict) else None
        if side not in ('UP','DOWN'): return None
        try:
            rec=self._v75_recoverability(int(t),side,qv)
            if not bool(rec.get('recoverable')): return None
        except Exception: return None
        px=float(qv[side].get('bid') or 0.0);qty=1.0/px if px>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS: return None
        floor,u,d,cost=self._raw_floor();floor=float(floor)
        repair='DOWN' if side=='UP' else 'UP';rbid=qv.get(repair,{}).get('bid')
        owned,owned_rows=self._owned_repair_legs(repair)
        bctx=MultiSlotBundleRecoverabilityContext(
            thesis_side=side,floor_before=floor,up_qty=float(u),down_qty=float(d),cost=float(cost),
            pending_expand_legs=(PendingExpandLeg(qty,px),),owned_repair_legs=owned,
            repair_bid=None if rbid is None else float(rbid),max_future_venue_qty=12.0,
        )
        bdec=self.bundleProjection.evaluate(bctx)
        pctx=PortfolioEconomicDeficitPriorityContext(
            economic_deficit_open=bool(getattr(self,'v80EconomicDeficit',None) is not None),
            floor_before=floor,
            projected_floor_after_candidate_and_owned_repair=float(bdec.projected_floor_after_owned_repair),
            candidate_role='EXPAND',
        )
        pdec=self.portfolioDeficitPriority.evaluate(pctx)
        row={'t':int(t),'afterKind':after_kind,'pExpand':pE,'side':side,'price':px,'qty':qty,
             'economicDeficit':dict(self.v80EconomicDeficit) if isinstance(getattr(self,'v80EconomicDeficit',None),dict) else None,
             'ownedRepair':owned_rows,'bundle':bdec.__dict__,'priority':pdec.__dict__}
        self.portfolioPriorityChecks+=1
        if pdec.allow_candidate:self.portfolioPriorityAllows+=1
        else:self.portfolioPriorityBlocks+=1
        self.portfolioPriorityEvents.append(row)
        return pdec

    def _score_state(self,t,after_kind):
        d=self._portfolio_expand_preflight(int(t),after_kind)
        if d is not None and not d.allow_candidate:
            return None
        return super()._score_state(t,after_kind)

    def run_kernel(self,models,winner):
        r=self.run_first_carrier_relay(models,winner)
        r.update({'portfolioEconomicDeficitPriority':self.portfolioDeficitPriority.name,
                  'portfolioPriorityChecks':self.portfolioPriorityChecks,
                  'portfolioPriorityAllows':self.portfolioPriorityAllows,
                  'portfolioPriorityBlocks':self.portfolioPriorityBlocks,
                  'portfolioPriorityEvents':self.portfolioPriorityEvents[:800]})
        return r

def sm(sim,r):
    m=fd.summarize(sim,r)
    m.update({'portfolioPriorityChecks':int(r.get('portfolioPriorityChecks') or 0),'portfolioPriorityAllows':int(r.get('portfolioPriorityAllows') or 0),'portfolioPriorityBlocks':int(r.get('portfolioPriorityBlocks') or 0)})
    return m

def compact(m):
    return {k:m.get(k) for k in ['fills','submits','rounds','pnl','floor','repairPaid','remainingDebt','parallelRepairActiveFillQty','generationDebtAttachApplied','generationActiveRearmArchives','safe','allocationConservation','allocationBounded','occupancyBounded','frontierDisconnectedActiveAllows','portfolioPriorityChecks','portfolioPriorityAllows','portfolioPriorityBlocks']}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',default='1946475,1946683');ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='minimal_kernel_deficit_priority_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'MINIMAL_KERNEL_DEFICIT_PRIORITY','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MINIMAL_KERNEL_DEFICIT_PRIORITY_START','markets':mids,'cells':['A_STRICT','B_SALVAGE','C_SALVAGE_PLUS_DEFICIT_PRIORITY']}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for mid in mids:
            tape=tmp/'tapes'/f'{mid}.json.xz';cr=co[mid];cells=[]
            for label,cls,runner in [
                ('A_STRICT',fd.g2.D_Both,'run_first_carrier_relay'),
                ('B_SALVAGE',fd.FrontierDisconnectedSalvageHFT,'run_first_carrier_relay'),
                ('C_SALVAGE_PLUS_DEFICIT_PRIORITY',MinimalKernelEconomicDeficitPriorityHFT,'run_kernel')]:
                s=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47);ts=time.time()
                try:r=getattr(s,runner)(models,cr['winner']);m=sm(s,r)
                finally:s.close()
                cells.append({'cell':label,'metrics':m,'physical':m.get('physical',[]),'priorityEvents':r.get('portfolioPriorityEvents',[])[:120]})
                print(json.dumps({'marketId':mid,'cell':label,'elapsed':round(time.time()-ts,1),'metrics':compact(m),'physical':m.get('physical',[])},ensure_ascii=False),flush=True)
            A,B,C=[x['metrics'] for x in cells]
            gates={'C_safe':bool(C['safe'] and C['allocationConservation'] and C['allocationBounded'] and C['occupancyBounded']),
                   'priorityExercised':C['portfolioPriorityBlocks']>0,
                   'C_retainsMoreLivenessThanStrict':C['fills']>=A['fills'],
                   'C_floorNonWorseThanSalvage':C['floor']>=B['floor']-1e-9,
                   'C_pnlNonWorseThanSalvage':C['pnl']>=B['pnl']-1e-9}
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'gates':gates,'cells':cells})
        allsafe=all(x['gates']['C_safe'] for x in rows);ex=all(x['gates']['priorityExercised'] for x in rows);econ=all(x['gates']['C_floorNonWorseThanSalvage'] and x['gates']['C_pnlNonWorseThanSalvage'] for x in rows);live=all(x['gates']['C_retainsMoreLivenessThanStrict'] for x in rows)
        decision='KEEP_MINIMAL_KERNEL_DEFICIT_PRIORITY_FOR_NONREGRESSION_REPLICATION' if allsafe and ex and econ and live else ('REJECT_MINIMAL_KERNEL_SAFETY' if not allsafe else 'DIAGNOSE_MINIMAL_KERNEL_TRADEOFF')
        out={'version':'MINIMAL_SAFETY_KERNEL_ECONOMIC_DEFICIT_PRIORITY_AB_V1_20260905','researchOnly':True,'marketIds':mids,'decision':decision,'aggregateGates':{'candidateSafetyAll':allsafe,'priorityExercisedBoth':ex,'candidateEconomicNonWorseThanSalvageBoth':econ,'candidateLivenessAtLeastStrictBoth':live},'rows':rows,'boundary':['A strict generation-debt/rearm control','B adds frontier-disconnected existing-debt Active salvage','C adds only centralized V80 economic-deficit priority before V83 Expand objective creation','Repair execution never blocked by new policy','no deficit => V83 Expand admission unchanged','open deficit permits concurrent Expand only when already-owned Repair makes joint prospective Floor non-worsening','all accounting/ownership/occupancy/Repair-first-overflow-second/<=180s invariants frozen','no threshold/qty/price/delay tuning','winner posthoc only; no Target runtime input; realistic HFT; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregateGates':out['aggregateGates']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
