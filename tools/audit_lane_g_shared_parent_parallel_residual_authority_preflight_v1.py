from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_partial_payment_residual_carrier_reachability_v1 as base
v2=base.v2;EPS=1e-9
TARGETS={
 1946468:1788534917463,
 1946792:1788538509914,
 1946876:1788539162674,
}
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class SharedParentAuthorityPreflight(base.PartialPaymentResidualReachability):
    def __init__(self,tape,mid):
        super().__init__(tape,1,4);self.mid=int(mid);self.targetT=int(TARGETS[self.mid]);self.capture=None
    def _risk_contract_if_needed(self,t):
        # Parent captures partial seam before native risk-contract mutation.
        super()._risk_contract_if_needed(t)
        if int(t)!=self.targetT or self.capture is not None:return
        seam=next((x for x in self.partialSeams if int(x['t'])==int(t)),None)
        if seam is None:return
        c=dict(seam['residualCarrierCandidate'])
        rows=[]
        for sid,key,o,role in self._live_role_rows():
            if role not in REPAIR_ROLES or int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration) or str(o['side'])!=str(self._repair_side()):continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();leaves=float(s.get('leavesQty')) if s.get('leavesQty') is not None else self._remaining(key)
            except Exception:st='';leaves=self._remaining(key)
            if st in v2.TERMINAL_STATUSES:continue
            rows.append({'slotId':int(sid),'key':str(key),'role':str(role),'price':float(o['price']),'physicalRemaining':max(0.0,float(leaves)),
                         'repairQuotaRemaining':max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0))),
                         'overflowQtyRemaining':max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0))),
                         'overflowNotionalReservation':max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))*float(o['price']),
                         'cancelRequested':bool(o.get('cancelRequested')),'status':st})
        # Active same-generation Repair carriers participate in shared physical debt too.
        active=[]
        for key in sorted(self.activeKeys):
            o=self.orders.get(key)
            if not o or int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration) or str(o['side'])!=str(self._repair_side()):continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();leaves=float(s.get('leavesQty')) if s.get('leavesQty') is not None else self._remaining(key)
            except Exception:st='';leaves=self._remaining(key)
            if st in v2.TERMINAL_STATUSES:continue
            active.append({'key':str(key),'role':str(self.key_role.get(key)),'price':float(o['price']),'physicalRemaining':max(0.0,float(leaves)),
                           'repairQuotaRemaining':max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0))),
                           'overflowQtyRemaining':max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0))),'status':st})
        scope_debt=float(self._scope_debt_qty());cand_q=float(c.get('qty') or 0.0);cand_p=float(c.get('price') or 0.0)
        allrows=rows+active
        physical_existing=sum(float(x['physicalRemaining']) for x in allrows)
        repair_auth_existing=sum(float(x['repairQuotaRemaining']) for x in allrows)
        overflow_auth_existing=sum(float(x['overflowQtyRemaining']) for x in allrows)
        overflow_res_existing=sum(float(x.get('overflowNotionalReservation') or (float(x['overflowQtyRemaining'])*float(x['price']))) for x in allrows)
        aggregate_physical=physical_existing+cand_q
        parent_overflow_cap=max(0.0,aggregate_physical-scope_debt)
        fixed_overflow_auth=overflow_auth_existing+float(c.get('overflowQty') or 0.0)
        fixed_repair_auth=repair_auth_existing+float(c.get('repairQty') or 0.0)
        prices=[float(x['price']) for x in allrows if float(x['physicalRemaining'])>EPS]+([cand_p] if cand_q>EPS else [])
        worst_overflow_notional=parent_overflow_cap*(max(prices) if prices else 0.0)
        best_overflow_notional=parent_overflow_cap*(min(prices) if prices else 0.0)
        available=float(self._available_expand_risk_credit())
        risk_authority=float(self._risk_authority_current_generation())
        committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+float(self._service_claim())
        allowance=float(self.scopeRiskCreditTotal)+risk_authority
        combined_slack=max(0.0,allowance-committed)
        risk_debt=float(self._risk_debt_outstanding())
        # Recover current native authority diagnostics without treating pending Repair as protection.
        raw_unreserved_plus_current_overflow=available+overflow_res_existing
        payoff_before=float(self._physical_floor())
        up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)
        for x in allrows:
            q=float(x['physicalRemaining']);p=float(x['price']);cost+=q*p
            if str(self._repair_side())=='UP':up+=q
            else:dn+=q
        cost+=cand_q*cand_p
        if str(self._repair_side())=='UP':up+=cand_q
        else:dn+=cand_q
        full_bundle_floor=min(up,dn)-cost
        bundle_floor_risk=max(0.0,payoff_before-full_bundle_floor)
        self.capture={'marketId':self.mid,'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,'repairSide':self._repair_side(),
          'scopeDebt':scope_debt,'obligation':seam.get('obligation'),'candidate':c,'livePassiveRepairCarriers':rows,'liveActiveRepairCarriers':active,
          'physicalExisting':physical_existing,'aggregatePhysicalWithCandidate':aggregate_physical,
          'parentOverflowCapIfAllFill':parent_overflow_cap,'fixedRepairAuthorizationTotal':fixed_repair_auth,'fixedOverflowAuthorizationTotal':fixed_overflow_auth,
          'overflowQuantityConservationError':abs(parent_overflow_cap-fixed_overflow_auth),
          'existingOverflowNotionalReservation':overflow_res_existing,'availableExpandCreditAfterExistingReservations':available,
          'riskAuthorityCurrentGeneration':risk_authority,'riskDebtOutstanding':risk_debt,'combinedAuthorityCommitted':committed,'combinedAuthorityAllowance':allowance,'combinedAuthoritySlack':combined_slack,
          'rawAuthorityStockDiagnostic':raw_unreserved_plus_current_overflow,'worstOverflowNotionalByFillOrder':worst_overflow_notional,
          'bestOverflowNotionalByFillOrder':best_overflow_notional,'currentPhysicalFloor':payoff_before,'allFillPhysicalFloor':full_bundle_floor,
          'allFillFloorRiskVsCurrent':bundle_floor_risk,
          'sharedQuantityFeasible':abs(parent_overflow_cap-fixed_overflow_auth)<=1e-7,
          'sharedWorstNotionalCoveredByRawAuthority':worst_overflow_notional<=raw_unreserved_plus_current_overflow+1e-9,
          'sharedWorstNotionalCoveredByCombinedAuthority':worst_overflow_notional<=combined_slack+1e-9,
          'sharedAllFillFloorNonWorse':full_bundle_floor>=payoff_before-EPS}

    def run_preflight(self,w):return self.run_audit(w)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946468,1946792,1946876');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_shared_auth_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=SharedParentAuthorityPreflight(tmp/f'{m}.json.xz',m)
            try:r=s.run_preflight(co[m]['winner'])
            finally:s.close()
            row=s.capture or {'marketId':m,'missing':True};row['correct']=bool(r.get('r264CorrectnessPass'));rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        out={'version':'LANE_G_SHARED_PARENT_PARALLEL_RESIDUAL_AUTHORITY_PREFLIGHT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'rows':rows,
             'gates':{'allCaptured':all(not x.get('missing') for x in rows),'baselineCorrectnessPass':all(x.get('correct') for x in rows),'quantityConservationPass':all(x.get('sharedQuantityFeasible') for x in rows)},
             'boundary':['exact frozen partial-payment mixed seams only','behavior-inert','all current-generation same-side Repair carriers included','candidate from prior outcome-blind residual scan unchanged','no branch outcome/winner used','no fixed time gate','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
