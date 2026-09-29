from __future__ import annotations
import argparse,json,math,os,sys,tempfile,zipfile
from pathlib import Path
try:
    import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class CompositeShadow(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape);self.comp_shadow=[]
    def _mid(self,qv,side):
        return (float(qv[side]['bid'])+float(qv[side]['ask']))/2.0
    def _live_claims(self,repair_side):
        rows=[];total=0.0
        for sid,key in self.slot_key.items():
            o=self.orders.get(key)
            if not o or str(o.get('side'))!=str(repair_side):continue
            role=str(self.key_role.get(key,'UNASSIGNED'))
            if role not in REPAIR_ROLES:continue
            try:rem=max(0.0,float(self._remaining(key)))
            except Exception:rem=max(0.0,float(o.get('qty') or 0)-float(o.get('cum') or 0))
            if rem<=EPS:continue
            rows.append({'slotId':int(sid),'key':str(key),'role':role,'price':float(o.get('price') or 0),'remainingQty':rem,'cancelRequested':bool(o.get('cancelRequested'))})
            total+=rem
        return rows,float(total)
    def _submit_role(self,t,side,role,p,q,proj,source):
        a=self.q_arm;candidate=None
        if a is not None and side==a.get('side') and role==a.get('role') and 'passivePrice' in a and abs(float(p)-float(a['passivePrice']))<=EPS:
            claims,claim_qty=self._live_claims(side)
            agg=float(self._aggregate_for_repair_side(side));residual=max(0.0,agg-claim_qty);venue_min=1.0/float(p) if float(p)>EPS else math.inf;comp=residual+venue_min
            qv=a.get('qv');mid=self._mid(qv,side) if qv else None
            avg=None
            expand_side=str(a.get('expandSide'))
            dq=list(self.resp_queues.get(expand_side,[]))
            if dq:
                den=sum(float(x['remainingQty']) for x in dq)
                if den>EPS:avg=sum(float(x['remainingQty'])*float(x['price']) for x in dq)/den
            candidate={'t':int(t),'repairSide':str(side),'role':str(role),'normalPrice':float(p),'normalQty':float(q),'aggregateDebt':agg,'existingRepairClaimQty':claim_qty,'existingRepairClaims':claims,'unclaimedResidualDebt':residual,'venueMinOverflowQty':venue_min,'proposedCompositeQty':comp,'compositeQtyLe12':bool(math.isfinite(comp) and comp<=12.0+EPS),'residualCoversNormalManagedQty':bool(residual+EPS>=float(q)),'repairSideMid':mid,'repairSidePriceSupported':bool(mid is not None and mid>.5+EPS),'weightedDebtPrice':avg,'pairSumRepairPortion':(avg+float(p) if avg is not None else None),'source':str(source)}
        before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
        if candidate is not None:
            candidate['baselineSubmitted']=bool(ok);candidate['key']=f'{side}_{before_n}' if ok else None
            self.comp_shadow.append(candidate)
        return ok
    def run_shadow(self):
        r=self.run_qty('__UNSCORED__')
        fills={};first={}
        for f in r.get('fillSideSequence',[]):
            k=str(f.get('key'));fills[k]=fills.get(k,0.0)+float(f.get('incQty') or 0);first.setdefault(k,int(f.get('t')))
        for x in self.comp_shadow:
            k=x.get('key');fq=float(fills.get(k,0.0)) if k else 0.0
            x['baselinePhysicalFillQty']=fq;x['baselinePhysicalFill']=fq>EPS;x['baselineFullFill']=bool(k and fq+1e-9>=float(x['normalQty']));x['baselineFirstFillT']=first.get(k);x['baselineFillLatencyMs']=(first[k]-int(x['t']) if k in first else None)
            x['structurallyEligible']=bool(x['baselineSubmitted'] and x['compositeQtyLe12'] and x['residualCoversNormalManagedQty'] and x['unclaimedResidualDebt']>EPS)
            x['priceSupportedEligible']=bool(x['structurallyEligible'] and x['repairSidePriceSupported'])
            x['filledStructuralEligible']=bool(x['structurallyEligible'] and x['baselinePhysicalFill'])
            x['filledPriceSupportedEligible']=bool(x['priceSupportedEligible'] and x['baselinePhysicalFill'])
        r['atomicCompositeShadow']=self.comp_shadow;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='comp_shadow_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            sim=CompositeShadow(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_shadow()
            finally:sim.close()
            sh=r.pop('atomicCompositeShadow');led=r.get('quantityLedgerSummary') or {};inv=led.get('invariantViolations') or {}
            row={'marketId':mid,'shadow':sh,'baseline':{'submits':int(r['submits']),'fills':int(r['fillEvents']),'filledQty':float(r['filledQty']),'floor':float(r['floor']),'best':float(r['best']),'alternations':int(r.get('fillSideAlternations') or 0),'maxSlots':int(r.get('maxSimultaneousSlots') or 0),'twoSided':bool(r.get('twoSidedMaterialized')),'ledgerViolations':inv}}
            rows.append(row);print(json.dumps({'progress':i,'of':len(mids),'marketId':mid,'managedSubmits':len(sh),'structural':sum(x['structurallyEligible'] for x in sh),'priceSupported':sum(x['priceSupportedEligible'] for x in sh),'filledStructural':sum(x['filledStructuralEligible'] for x in sh),'filledPriceSupported':sum(x['filledPriceSupportedEligible'] for x in sh),'ledgerViolations':inv},ensure_ascii=False),flush=True)
    allsh=[x for r in rows for x in r['shadow']]
    agg={'markets':len(rows),'managedSubmits':len(allsh),'structurallyEligible':sum(x['structurallyEligible'] for x in allsh),'priceSupportedEligible':sum(x['priceSupportedEligible'] for x in allsh),'filledStructuralEligible':sum(x['filledStructuralEligible'] for x in allsh),'filledPriceSupportedEligible':sum(x['filledPriceSupportedEligible'] for x in allsh),'marketsWithFilledStructural':sum(any(x['filledStructuralEligible'] for x in r['shadow']) for r in rows),'marketsWithFilledPriceSupported':sum(any(x['filledPriceSupportedEligible'] for x in r['shadow']) for r in rows),'allLedgerClean':all(not r['baseline']['ledgerViolations'] for r in rows),'allMax4':all(r['baseline']['maxSlots']<=4 for r in rows)}
    out={'version':'V3B_DECONTAMINATED_ATOMIC_COMPOSITE_SERVICE_SHADOW_STAGEA16','date':'2026-09-07','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,'aggregate':agg,'boundary':['behavior-inert V3B shadow','existing live/cancel-pending Repair claims reserved before residual assignment','proposed qty = unclaimed residual debt + one venue-min overflow at same managed price','normal V3B price/role/timing/qty remain physically executed','repair-side midpoint support is diagnostic only','no winner/PnL used','no old Floor/credit/scope/recoverability gates','NEW24-B untouched','no 8781','realistic HFT/no dream fill']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
