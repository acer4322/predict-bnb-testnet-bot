from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;base=v3b.base

class E1QuantityCoverageShadow(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape)
        self.events=[]
        self.counts={'expandDecisionClocks':0,'repairSideDebtClocks':0,'residualDebtPositive':0,'residualSupportsNativeTranche':0,'eligibleReplacementSeams':0}

    def _pure_first_pair_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used:continue
            if not self._pair_ok(side,p):continue
            return {'side':side,'price':float(p),'qty':float(1.0/p)}
        return None

    def _coverage_for_repair_side(self,side):
        debt=float(self._aggregate_for_repair_side(side))
        live=[];live_remaining=0.0;live_keys=set()
        for sid,key,o,role in self._live_role_rows(side=side):
            if role not in v3b.REPAIR_ROLES:continue
            qty=float(o.get('qty') or 0.0);cum=float(o.get('cum') or 0.0);rem=max(0.0,qty-cum)
            live_keys.add(key);live_remaining+=rem
            live.append({'slotId':int(sid),'key':key,'role':role,'price':float(o.get('price') or 0.0),'qty':qty,'cum':cum,'remainingQty':rem,'cancelRequested':bool(o.get('cancelRequested'))})
        pending_remaining=0.0;pending=None
        p=self.q_pending_active
        if p is not None and str(p.get('side'))==side:
            src=str(p.get('sourceKey') or '')
            if src not in live_keys:
                pending_remaining=max(0.0,float(p.get('sourceRemainingQty') or 0.0))
                pending={'sourceKey':src,'remainingQty':pending_remaining,'role':p.get('role'),'originResponsibilityId':p.get('originResponsibilityId',p.get('responsibilityId'))}
        projected_claim=min(debt,live_remaining+pending_remaining)
        residual=max(0.0,debt-projected_claim)
        return {'aggregateOutstandingDebt':debt,'liveRepairRemainingQty':live_remaining,'pendingManagedRemainingQty':pending_remaining,'projectedClaimQty':projected_claim,'residualUnclaimedQty':residual,'liveRepairCarriers':live,'pendingManagedClaim':pending,'managedLadderPresent':self.q_ladder is not None}

    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)>base.v2.NO_NEW_EXPOSURE_MS:
            side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
            if role=='SATELLITE_EXPAND':
                self.counts['expandDecisionClocks']+=1
                baseline=self._pure_first_pair_candidate(side)
                free=max(0,int(self.max_slots)-len(self.slot_key))
                for repair_side in ('UP','DOWN'):
                    cov=self._coverage_for_repair_side(repair_side)
                    if cov['aggregateOutstandingDebt']<=EPS:continue
                    self.counts['repairSideDebtClocks']+=1
                    cand=self._pure_first_pair_candidate(repair_side)
                    if cov['residualUnclaimedQty']>EPS:self.counts['residualDebtPositive']+=1
                    tranche_ok=bool(cand is not None and cov['residualUnclaimedQty']+EPS>=float(cand['qty']))
                    if tranche_ok:self.counts['residualSupportsNativeTranche']+=1
                    eligible=bool(baseline is not None and free>0 and tranche_ok)
                    if eligible:self.counts['eligibleReplacementSeams']+=1
                    self.events.append({'t':int(t),'baselineSide':side,'baselineRole':role,'baselineCandidate':baseline,'repairSide':repair_side,'repairRole':'ECONOMIC_CORE' if self._core_for_side(repair_side) is None else 'SATELLITE_REPAIR','repairCandidate':cand,'freeSlotsBeforeOpen':free,'eligibleReplacement':eligible,**cov,'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'cost':float(self.cost),'branchPayoffs':{'UP':float(self.inv['UP']-self.cost),'DOWN':float(self.inv['DOWN']-self.cost)}})
        return super()._open_one_option(t,qv,end)

    def run_shadow(self):
        r=super().run_qty('__UNSCORED__');r['e1QuantityCoverageShadow']=True;r['e1QuantityCounts']=self.counts;r['e1QuantityEvents']=self.events[:15000];return r

def fields(r):
    return {k:r.get(k) for k in ('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','slotHistory','fillSideSequence','quantityPaymentRows','quantityLedgerSummary','quantityResponsibilities')}
def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-10)
    return a==b

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='e1_qtycov_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz'
            A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:ra=A.run_qty('__UNSCORED__')
            finally:A.close()
            B=E1QuantityCoverageShadow(tape)
            try:rb=B.run_shadow()
            finally:B.close()
            fa,fb=fields(ra),fields(rb);par={k:eq(fa[k],fb[k]) for k in fa}
            row={'marketId':mid,'allBehaviorParity':all(par.values()),'parity':par,'ledgerViolations':rb['quantityLedgerSummary'].get('invariantViolations'),'counts':rb['e1QuantityCounts'],'events':rb['e1QuantityEvents']};rows.append(row)
            print(json.dumps({'progress':i,'marketId':mid,'parity':row['allBehaviorParity'],'ledgerViolations':row['ledgerViolations'],'counts':row['counts']},ensure_ascii=False),flush=True)
    seams=[]
    for r in rows:
        for e in r['events']:
            if e['eligibleReplacement']:
                seams.append({'marketId':r['marketId'],'t':e['t'],'baselineSide':e['baselineSide'],'baselineCandidate':e['baselineCandidate'],'repairSide':e['repairSide'],'repairRole':e['repairRole'],'repairCandidate':e['repairCandidate'],'aggregateOutstandingDebt':e['aggregateOutstandingDebt'],'projectedClaimQty':e['projectedClaimQty'],'residualUnclaimedQty':e['residualUnclaimedQty'],'freeSlotsBeforeOpen':e['freeSlotsBeforeOpen'],'branchPayoffs':e['branchPayoffs']})
    seams.sort(key=lambda x:(x['t'],x['marketId'],x['repairSide']))
    out={'version':'GPT6_E1_V3B_QUANTITY_COVERAGE_SHADOW_V2','date':'2026-09-06','researchOnly':True,'markets':mids,'allBehaviorParity':all(r['allBehaviorParity'] for r in rows),'rows':rows,'eligibleReplacementSeams':seams,'firstEligibleSeams':seams[:20],'boundary':['behavior inert','cancel-pending remains quantity-reserved','all live Repair remaining qty counted before residual','pending Active source remaining counted if no live key','replacement tranche requires residual>=1/p to avoid intentional overflow','no action mutation','no winner/PnL/Target authority','realistic HFT','max4 unchanged','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'eligibleSeams':len(seams),'firstEligible':seams[:3]},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
