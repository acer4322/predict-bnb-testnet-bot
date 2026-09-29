from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9

class PartialPaymentResidualReachability(r264.ExecutionRepresentedPreRepairReexpandSim):
    """Behavior-inert audit of the first Manager clock after an authoritative partial Repair payment.

    Selection is structural only: a current obligation received confirmed Repair payment on this
    receipt and remains outstanding. Candidate geometry is reconstructed without mutating orders,
    authority, or admission counters.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.pendingPartial=[];self.partialSeams=[]

    def _pure_residual_candidate(self,side):
        if side not in {'UP','DOWN'}:return {'ok':False,'reason':'NO_REPAIR_SIDE'}
        used={v2.kprice(o['price']) for _,_,o,role in self._live_role_rows(side=side)}
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));available=max(0.0,debt-reserved)
        before=float(self._physical_floor());credit=float(self._available_expand_risk_credit())
        rejects=[]
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p in used:continue
            q=1.0/p
            rq=min(q,available)
            if rq<=EPS:
                rejects.append({'price':p,'reason':'NO_UNRESERVED_REPAIR_DEBT'});continue
            repair_floor=float(self._candidate_alone_floor(side,p,rq))
            if repair_floor<=before+EPS:
                rejects.append({'price':p,'reason':'REPAIR_PORTION_NOT_FLOOR_IMPROVING','repairOnlyFloor':repair_floor});continue
            oq=max(0.0,q-rq);full_floor=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,repair_floor-full_floor)
            if risk>credit+EPS:
                rejects.append({'price':p,'reason':'OVERFLOW_CREDIT_INSUFFICIENT','overflowRisk':risk,'availableCredit':credit});continue
            return {'ok':True,'price':p,'qty':q,'repairQty':rq,'overflowQty':oq,'overflowRisk':risk,
                    'repairOnlyFloor':repair_floor,'fullFloor':full_floor,'debt':debt,'reservedRepairBefore':reserved,
                    'availableDebtBefore':available,'availableExpandCredit':credit,
                    'repairCoverageOfOutstanding':None,'overflowFractionOfPhysicalQty':(oq/q if q>EPS else None)}
        return {'ok':False,'reason':'NO_LEGAL_PASSIVE_CANDIDATE','debt':debt,'reservedRepairBefore':reserved,'availableDebtBefore':available,'availableExpandCredit':credit,'rejects':rejects[:12]}

    def process(self,t):
        start=len(self.r257Events)
        super().process(t)
        for ev in self.r257Events[start:]:
            if ev.get('event')!='R257_RISK_REPAIR_OBLIGATION_PAYMENT':continue
            gen=int(ev.get('generation') or -1);ob=self.riskRepairObligations.get(gen)
            if not ob:continue
            paid=float(ev.get('paid') or 0.0);out=float(ob.get('outstanding') or 0.0);rep=float(ob.get('repaidQty') or 0.0);born=float(ob.get('bornQty') or 0.0)
            if paid>EPS and out>EPS and rep>EPS and born>rep+EPS:
                self.pendingPartial.append({'t':int(t),'generation':gen,'paymentEvent':dict(ev),'bornQty':born,'outstanding':out,'repaidQty':rep})

    def _risk_contract_if_needed(self,t):
        matches=[x for x in self.pendingPartial if int(x['t'])==int(t) and not x.get('captured')]
        for x in matches:
            x['captured']=True;gen=int(x['generation']);ob=self.riskRepairObligations.get(gen)
            current=bool(ob and self.scopeSide is not None and int(self.scopeGeneration)==gen and float(ob.get('outstanding') or 0.0)>EPS)
            repair_side=self._repair_side() if current else None
            live=self._live_dedicated_repair_rows(gen) if current else []
            quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live) if current else 0.0
            cand=self._pure_residual_candidate(repair_side) if current else {'ok':False,'reason':'OBLIGATION_NOT_CURRENT_AFTER_PAYMENT'}
            if cand.get('ok'):
                cand['repairCoverageOfOutstanding']=float(cand['repairQty'])/max(EPS,float(ob.get('outstanding') or 0.0))
            qv=v2.base.quotes(self.book);qs=(qv or {}).get(repair_side,{}) if repair_side else {}
            self.partialSeams.append({'t':int(t),'generation':gen,'paymentEvent':x['paymentEvent'],
                'obligation':({k:ob.get(k) for k in ['bornQty','outstanding','repaidQty','passiveRepaidQty','activeRepaidQty','closeReason']} if ob else None),
                'currentAfterPayment':current,'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'repairSide':repair_side,
                'liveRepairKeys':[str(k) for k,_ in live],'representedRepairQuota':float(quota),'slots':len(self.slot_key),'active':len(self.activeKeys),
                'book':{'bid':float(qs.get('bid')) if qs.get('bid') is not None else None,'ask':float(qs.get('ask')) if qs.get('ask') is not None else None},
                'scopeDebt':float(self._scope_debt_qty()) if current else None,'availableExpandCredit':float(self._available_expand_risk_credit()) if current else None,
                'residualCarrierCandidate':cand})
        return super()._risk_contract_if_needed(t)

    def run_audit(self,winner):return self.run_r264(winner)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='ALL');ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_partial_residual_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort=json.loads(z.read('cohort.json'))['rows'];co={int(x['marketId']):x for x in cohort
            }
            mids=sorted(co) if str(a.market_ids).upper()=='ALL' else [int(x) for x in a.market_ids.split(',') if x.strip()]
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];seams=[]
        for i,m in enumerate(mids,1):
            s=PartialPaymentResidualReachability(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_audit(co[m]['winner'])
            finally:s.close()
            ms=[{'marketId':m,**x} for x in s.partialSeams];seams.extend(ms)
            row={'marketId':m,'partialPaymentSeams':len(ms),'legalResidualCandidates':sum(bool(x['residualCarrierCandidate'].get('ok')) for x in ms),
                 'mixedRepairOverflowCandidates':sum(bool(x['residualCarrierCandidate'].get('ok')) and float(x['residualCarrierCandidate'].get('overflowQty') or 0.0)>EPS for x in ms),
                 'correct':bool(r.get('r264CorrectnessPass'))};rows.append(row)
            print(json.dumps({'progress':i,'of':len(mids),**row},ensure_ascii=False),flush=True)
        legal=[x for x in seams if x['residualCarrierCandidate'].get('ok')];mixed=[x for x in legal if float(x['residualCarrierCandidate'].get('overflowQty') or 0.0)>EPS]
        chosen=[];seen=set()
        for x in sorted(mixed,key=lambda z:(int(z['t']),int(z['marketId']),int(z['generation']))):
            if x['marketId'] in seen:continue
            chosen.append({'marketId':x['marketId'],'t':x['t'],'generation':x['generation'],'candidate':x['residualCarrierCandidate']});seen.add(x['marketId'])
        out={'version':'LANE_G_PARTIAL_PAYMENT_RESIDUAL_CARRIER_REACHABILITY_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'markets':mids,'marketSummary':rows,'seams':seams,'summary':{'markets':len(mids),'partialPaymentSeams':len(seams),'legalResidualCandidates':len(legal),'mixedRepairOverflowCandidates':len(mixed),'marketsWithMixedCandidate':len({x['marketId'] for x in mixed})},
             'outcomeBlindFirstMixedPerMarket':chosen,'gates':{'correctnessPass':all(x['correct'] for x in rows)},
             'selectionBoundary':['current R2.64 consumed behavior unchanged','capture first Manager clock after confirmed authoritative Repair payment that leaves same obligation outstanding','candidate reconstructed from current strict-past live price levels and current authoritative debt/credit only','no winner/terminal/future action in seam selection'],
             'policyBoundary':['observation only','no fixed seconds/windows/rank/age action gate','partial payment remains authoritative','pending gives zero protection/credit','max4 and <=180s inherited','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'gates':out['gates'],'chosen':chosen[:12]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
