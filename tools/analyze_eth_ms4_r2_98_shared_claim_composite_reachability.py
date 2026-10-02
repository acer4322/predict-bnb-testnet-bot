from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class SharedClaimCompositeReachabilityShadow(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.shadow=[]
        self._favorableContext=False
        super().__init__(tape,fanout_limit,max_slots)

    def _try_favorable_replenishment(self,t,end):
        self._favorableContext=True
        try:return super()._try_favorable_replenishment(t,end)
        finally:self._favorableContext=False

    def _live_repair_detail(self,repair_side):
        rows=[]
        for sid,key,o,role in self._live_role_rows(side=repair_side):
            if role not in REPAIR_ROLES:continue
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            rem=max(0.0,float(self._remaining(key)))
            rq=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
            oq=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
            rows.append({'slotId':int(sid),'key':key,'role':role,'price':float(o['price']),'physicalRemaining':rem,
                         'repairQuotaRemaining':rq,'overflowQuotaRemaining':oq})
        return rows

    def _first_thesis_price(self,thesis):
        used=self._used_prices(thesis)
        for raw in self._live_price_levels(thesis):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if math.isfinite(q) and q>EPS and q<=12.0+EPS:return p,q
        return None,None

    def _project(self,side,p,q):
        return float(self._candidate_alone_floor(side,p,q))

    def _shadow_opportunity(self,t,orig_side,orig_p,orig_q):
        thesis=self.intentThesisSide
        if thesis not in {'UP','DOWN'} or self.scopeSide is None or thesis==orig_side:return
        ob=self.riskRepairObligations.get(int(self.scopeGeneration))
        if not ob or str(ob.get('closeReason'))!='REPAID':return
        repair_side=self._repair_side()
        if thesis!=repair_side:return
        debt=max(0.0,float(self._scope_debt_qty()));reserved=max(0.0,float(self._reserved_repair_quota(thesis)))
        unreserved=max(0.0,debt-reserved);credit=max(0.0,float(self._available_expand_risk_credit()))
        floor0=float(self._physical_floor());orig_floor=self._project(orig_side,float(orig_p),float(orig_q));orig_risk=max(0.0,floor0-orig_floor)
        live=self._live_repair_detail(thesis);p,venue=self._first_thesis_price(thesis)
        row={'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,'thesisSide':thesis,
             'originalExpand':{'side':str(orig_side),'price':float(orig_p),'qty':float(orig_q),'riskCost':orig_risk},
             'floor':floor0,'debt':debt,'reservedRepair':reserved,'unreservedRepairDebt':unreserved,'availableCredit':credit,
             'liveRepairCarriers':live,'liveRepairCarrierCount':len(live),'slotsUsed':len(self.slot_key),'activeKeys':len(self.activeKeys),
             'thesisPrice':p,'venueMinQty':venue}
        if p is None:
            row['classification']='NO_THESIS_PRICE';self.shadow.append(row);return
        # Current exclusive V8.2 structural candidate: only unreserved debt may be assigned.
        eq=unreserved+venue
        e_repair_floor=self._project(thesis,p,unreserved) if unreserved>EPS else floor0
        e_full_floor=self._project(thesis,p,eq)
        e_overflow_risk=max(0.0,e_repair_floor-e_full_floor)
        exclusive={'physicalQty':eq,'repairQty':unreserved,'overflowQty':venue,'repairOnlyFloor':e_repair_floor,
                   'fullFloor':e_full_floor,'overflowRisk':e_overflow_risk,'qtyFeasible':eq<=12.0+EPS,
                   'repairImprovesFloor':unreserved>EPS and e_repair_floor>floor0+EPS,
                   'creditEnough':credit+EPS>=e_overflow_risk,
                   'v82ReservationExclusivePass':reserved<=EPS}
        exclusive['admissibleAll']=all([exclusive['qtyFeasible'],exclusive['repairImprovesFloor'],exclusive['creditEnough'],exclusive['v82ReservationExclusivePass']])
        row['exclusive']=exclusive
        # Shared-parent hypothetical if the new carrier may claim against the entire parent debt at fill time.
        sq=debt+venue
        s_repair_floor=self._project(thesis,p,debt) if debt>EPS else floor0
        s_full_floor=self._project(thesis,p,sq)
        s_overflow_risk=max(0.0,s_repair_floor-s_full_floor)
        sibling_phys=sum(float(x['physicalRemaining']) for x in live)
        sibling_repair_quota=sum(float(x['repairQuotaRemaining']) for x in live)
        worst_overflow_qty=max(0.0,sibling_phys+sq-debt)
        intended_overflow=venue
        contingent_extra=max(0.0,worst_overflow_qty-intended_overflow)
        shared={'physicalQty':sq,'repairQtyIfFillsFirst':debt,'intendedOverflowQty':venue,'repairOnlyFloor':s_repair_floor,
                'fullFloor':s_full_floor,'intendedOverflowRisk':s_overflow_risk,'qtyFeasible':sq<=12.0+EPS,
                'repairImprovesFloor':debt>EPS and s_repair_floor>floor0+EPS,'creditEnoughForIntendedOverflow':credit+EPS>=s_overflow_risk,
                'siblingPhysicalRemaining':sibling_phys,'siblingRepairQuotaRemaining':sibling_repair_quota,
                'worstAllSiblingOverflowQty':worst_overflow_qty,'contingentOverflowBeyondIntended':contingent_extra}
        shared['intentAdmissibleIgnoringSiblingOverhang']=all([shared['qtyFeasible'],shared['repairImprovesFloor'],shared['creditEnoughForIntendedOverflow']])
        row['sharedParent']=shared
        # Cancel-confirmed replacement of the only live Repair carrier, if there is exactly one sibling.
        if len(live)==1:
            freed=float(live[0]['repairQuotaRemaining']);rr=max(0.0,debt-max(0.0,reserved-freed));rq=rr+venue
            rrf=self._project(thesis,p,rr) if rr>EPS else floor0; rff=self._project(thesis,p,rq); rrisk=max(0.0,rrf-rff)
            row['singleCarrierReplacement']={'replaceKey':live[0]['key'],'freedRepairQuota':freed,'repairQty':rr,'physicalQty':rq,
                'overflowQty':venue,'qtyFeasible':rq<=12.0+EPS,'repairImprovesFloor':rr>EPS and rrf>floor0+EPS,
                'overflowRisk':rrisk,'creditEnough':credit+EPS>=rrisk,
                'admissibleAfterCancelConfirmed':rq<=12.0+EPS and rr>EPS and rrf>floor0+EPS and credit+EPS>=rrisk and reserved-freed<=EPS}
        # Compact classification.
        if exclusive['admissibleAll']:cls='CURRENT_EXCLUSIVE_ADMISSIBLE'
        elif shared['intentAdmissibleIgnoringSiblingOverhang'] and contingent_extra<=EPS:cls='SHARED_CLAIM_CLEAN'
        elif shared['intentAdmissibleIgnoringSiblingOverhang']:cls='SHARED_CLAIM_REQUIRES_CONTINGENT_OVERFLOW_AUTHORITY_OR_RECONCILIATION'
        elif row.get('singleCarrierReplacement',{}).get('admissibleAfterCancelConfirmed'):cls='SINGLE_CARRIER_REPLACEMENT_REACHABLE'
        elif not shared['qtyFeasible']:cls='STRUCTURAL_QTY_GT_12'
        elif not shared['repairImprovesFloor']:cls='REPAIR_COMPONENT_NOT_FLOOR_IMPROVING'
        elif not shared['creditEnoughForIntendedOverflow']:cls='INTENDED_OVERFLOW_CREDIT_INSUFFICIENT'
        else:cls='OTHER_BLOCK'
        row['classification']=cls;self.shadow.append(row)

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if role=='SATELLITE_EXPAND' and not self._favorableContext and self.intentThesisSide in {'UP','DOWN'} and str(side)!=str(self.intentThesisSide):
            self._shadow_opportunity(t,side,p,q)
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
        return ok

    def run_shadow(self,winner):
        r=super().run_r264(winner)
        r.update({'r298Version':'MS4_R2_98_SHARED_CLAIM_COMPOSITE_REACHABILITY_SHADOW_V1','r298IntentThesisSide':self.intentThesisSide,
                  'r298Rows':self.shadow[:2000],'r298OpportunityCount':len(self.shadow)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r298_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];summary=[]
        for m in mids:
            w=co[m]['winner'];s=SharedClaimCompositeReachabilityShadow(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(w)
            finally:s.close()
            sh=r.get('r298Rows',[]);counts={}
            for x in sh:counts[x['classification']]=counts.get(x['classification'],0)+1
            z={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r298IntentThesisSide'),'opportunities':len(sh),'classificationCounts':counts,
               'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'fills':int(r['fillEvents']),
               'correct':float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS}
            summary.append(z);rows.append({'marketId':m,'winnerPostHocOnly':w,**r});print(json.dumps(z,ensure_ascii=False),flush=True)
        allsh=[x for r in rows for x in r.get('r298Rows',[])]
        agg={}
        for x in allsh:agg[x['classification']]=agg.get(x['classification'],0)+1
        out={'version':'MS4_R2_98_SHARED_CLAIM_COMPOSITE_REACHABILITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,
             'markets':mids,'summary':summary,'rows':rows,'aggregate':{'opportunities':len(allsh),'classificationCounts':agg,
                'sharedIntentReachable':sum(bool(x.get('sharedParent',{}).get('intentAdmissibleIgnoringSiblingOverhang')) for x in allsh),
                'sharedClean':sum(x['classification']=='SHARED_CLAIM_CLEAN' for x in allsh),
                'sharedNeedsReconciliation':sum(x['classification']=='SHARED_CLAIM_REQUIRES_CONTINGENT_OVERFLOW_AUTHORITY_OR_RECONCILIATION' for x in allsh),
                'singleCarrierReplacementReachable':sum(bool(x.get('singleCarrierReplacement',{}).get('admissibleAfterCancelConfirmed')) for x in allsh)},
             'boundary':['behavior-inert exact R2.64 replay','first PROBE submit thesis is research identity only','post-repaid off-thesis ordinary Expand opportunities only','exclusive=current V8.2 reservation semantics','shared-parent is counterfactual fill-time Repair-first claim and does not authorize behavior','worst sibling overflow assumes all current sibling physical remainders plus new composite fill','no winner/Target/future runtime input','no 8781','realistic HFT']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
