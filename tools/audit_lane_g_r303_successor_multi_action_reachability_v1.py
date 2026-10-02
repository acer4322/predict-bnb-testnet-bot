from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_lane_g_r303_shared_parent_residual_cancel_guard_v1 as guard
EPS=guard.EPS; v2=guard.v2

class SuccessorReachabilityAudit(guard.ResidualCancelGuardSim):
    """Behavior-inert audit on V1C KEEP path: confirmed R303 overflow -> successor Repair -> next R2.64 seam."""
    def __init__(self,tape,mid):
        super().__init__(tape,mid,'KEEP_SHARED_PARENT_RESIDUAL')
        self._overflowSeen=0.0
        self.successorBirths=[]
        self.successorGenerations=set()
        self.successorSeams=[]
        self.cancelRequests=defaultdict(list)

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid))
        self.cancelRequests[int(t)].append({'key':key,'slotId':int(sid),'reason':str(reason)})
        return super()._request_cancel(t,sid,reason)

    def _r303_pre(self,t,end):
        ob=self._obligation_current()
        if not ob:return {'ok':False,'reason':'NO_OBLIGATION'}
        gen=int(ob['generation'])
        live=self._live_dedicated_repair_rows(gen)
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        base={'generation':gen,'scopeSide':self.scopeSide,'obligationOutstanding':outstanding,'obligationBornQty':float(ob.get('bornQty') or 0.0),
              'obligationRepaidQty':float(ob.get('repaidQty') or 0.0),'liveRepairCount':len(live),'representedQuota':quota,
              'repairKeys':[str(k) for k,_ in live],'repairPrices':[float(o['price']) for _,o in live],
              'slots':len(self.slot_key),'active':len(self.activeKeys),'maxSlots':int(self.max_slots),
              'availableExpandCredit':float(self._available_expand_risk_credit()),'scopeDebt':float(self._scope_debt_qty()),
              'thesisSide':self.intentThesisSide,'repairSide':self._repair_side()}
        if gen in self.r263GenerationUsed:return {**base,'ok':False,'reason':'GENERATION_ALREADY_USED'}
        if float(ob.get('repaidQty') or 0.0)>EPS:return {**base,'ok':False,'reason':'REPAIR_ALREADY_PAID'}
        if not live:return {**base,'ok':False,'reason':'NO_LIVE_DEDICATED_REPAIR'}
        if quota+EPS<outstanding:return {**base,'ok':False,'reason':'OBLIGATION_NOT_FULLY_REPRESENTED'}
        thesis=self.intentThesisSide; repair=self._repair_side()
        if thesis not in {'UP','DOWN'}:return {**base,'ok':False,'reason':'NO_THESIS'}
        if thesis!=repair:return {**base,'ok':False,'reason':'THESIS_NOT_REPAIR_SIDE'}
        if len(live)!=1:return {**base,'ok':False,'reason':'REQUIRES_EXACT_ONE_REPAIR'}
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return {**base,'ok':False,'reason':'LATE'}
        if self._has_stale_scope_reservation():return {**base,'ok':False,'reason':'STALE_SCOPE'}
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return {**base,'ok':False,'reason':'CAPACITY_FULL'}
        scope_side=str(ob.get('scopeSideAtBirth') or self.scopeSide)
        if self._samegen_expand_live(gen,scope_side):return {**base,'ok':False,'reason':'SAMEGEN_EXPAND_LIVE'}
        sibling_key,sibling_o=live[0]
        used=self._used_prices(thesis); p=None
        for raw in self._live_price_levels(thesis):
            px=float(v2.kprice(raw))
            if px>EPS and px not in used:p=px;break
        if p is None:return {**base,'ok':False,'reason':'NO_DISTINCT_THESIS_PRICE'}
        try:ss=self.snap(sibling_o); sib_rem=float(ss.get('leavesQty')) if ss.get('leavesQty') is not None else self._remaining(sibling_key)
        except Exception:sib_rem=self._remaining(sibling_key)
        debt=max(0.0,float(self._scope_debt_qty())); venue=1.0/p; unreserved=max(0.0,debt-max(0.0,sib_rem)); q=unreserved+venue
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:return {**base,'ok':False,'reason':'STRUCTURAL_QTY_INFEASIBLE','compositePhysicalQty':q}
        return {**base,'ok':True,'reason':'R303_STRUCTURALLY_ADMISSIBLE','siblingKey':str(sibling_key),'siblingPrice':float(sibling_o['price']),
                'siblingRemaining':float(sib_rem),'compositePrice':p,'compositePhysicalQty':q,'unreservedDebt':unreserved,
                'venueMinOverflowCap':venue,'riskCap':float(venue*p)}

    def process(self,t):
        before=float(self.r303OverflowQty)
        super().process(t)
        after=float(self.r303OverflowQty)
        if after>before+EPS:
            ob=self._obligation_current()
            ev={'t':int(t),'overflowQtyDelta':after-before,'overflowRiskTotal':float(self.r303OverflowRisk),'scopeGeneration':int(self.scopeGeneration),
                'scopeSide':self.scopeSide,'obligation':dict(ob) if ob else None}
            self.successorBirths.append(ev)
            if ob:self.successorGenerations.add(int(ob['generation']))

    def _try_r263(self,t,end):
        ob=self._obligation_current(); gen=int(ob['generation']) if ob else None
        successor=(gen in self.successorGenerations) if gen is not None else False
        pre=self._r303_pre(t,end) if successor else None
        live=self._live_dedicated_repair_rows(gen) if successor else []
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live) if successor else 0.0
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0)) if successor else 0.0
        r264ready=bool(successor and live and float(ob.get('repaidQty') or 0.0)<=EPS and quota+EPS>=outstanding and gen not in self.r263GenerationUsed)
        n0=int(self.n); s0=int(self.submits)
        ok=super()._try_r263(t,end)
        if successor and (r264ready or (pre and pre.get('ok'))):
            self.successorSeams.append({'t':int(t),'generation':gen,'r264Ready':r264ready,'r303':pre,
              'ordinaryReturned':bool(ok),'ordinarySubmitDelta':int(self.submits)-s0,'ordinaryNDelta':int(self.n)-n0,
              'nativeCancelRequestsSameReceipt':list(self.cancelRequests.get(int(t),[])),
              'inheritedActiveLiveCount':len(self.activeKeys),'inheritedActiveKeys':sorted(self.activeKeys)})
        return ok

    def run_audit(self,winner):
        return self.run_guard(winner)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_successor_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=SuccessorReachabilityAudit(tmp/f'{m}.json.xz',m)
            try:r=s.run_audit(co[m]['winner'])
            finally:s.close()
            seams=[]; seen=set()
            for x in s.successorSeams:
                k=(x['generation'],x['t'])
                if k not in seen:seen.add(k);seams.append(x)
            first_by_gen=[]
            for g in sorted(s.successorGenerations):
                xs=[x for x in seams if x['generation']==g and x['r264Ready']]
                if xs:first_by_gen.append(xs[0])
            row={'marketId':m,'successorBirths':s.successorBirths,'successorGenerations':sorted(s.successorGenerations),
                 'firstR264ReadyByGeneration':first_by_gen,'allSuccessorSeams':seams,'residualCorrect':bool(r.get('residualCorrect')),
                 'terminalSecondary':r.get('terminal')}
            rows.append(row)
            print(json.dumps({'marketId':m,'births':s.successorBirths,'firstReady':first_by_gen,'correct':row['residualCorrect']},ensure_ascii=False),flush=True)
        ready=[{'marketId':r['marketId'],**x} for r in rows for x in r['firstR264ReadyByGeneration']]
        out={'version':'LANE_G_R303_SUCCESSOR_MULTI_ACTION_REACHABILITY_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'rows':rows,'summary':{'markets':len(mids),'marketsWithConfirmedR303OverflowBirth':sum(bool(r['successorBirths']) for r in rows),
             'successorGenerations':sum(len(r['successorGenerations']) for r in rows),'successorR264ReadySeams':len(ready),
             'successorR303AdmissibleSeams':sum(bool(x.get('r303',{}).get('ok')) for x in ready)},
             'preregisterSuggestions':ready,'gates':{'correctnessPass':all(r['residualCorrect'] for r in rows)},
             'selectionBoundary':['V1C KEEP_SHARED_PARENT_RESIDUAL lineage only','trigger only on confirmed R303 overflow allocation that births authoritative successor obligation','next-cycle seam requires successor obligation + live dedicated Repair + represented quota >= outstanding before any R263 mutation','R303 predicate evaluated on exact same pre-call strict-past state','no winner/terminal outcome in selection'],
             'policyBoundary':['observation only; inherited behavior unchanged','no fixed seconds/windows/rank/age gate','native reanchor only reported if naturally requested on same receipt','inherited Active only reported if naturally live','same authority/max4/<=180s','pending gives zero payment/protection/credit','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary'],'gates':out['gates'],'preregisterSuggestions':ready},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
