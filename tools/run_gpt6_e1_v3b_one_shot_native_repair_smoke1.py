from __future__ import annotations
import argparse,copy,hashlib,json,math,os,tempfile,zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;base=v3b.base
TARGET_T=1788158708092
TARGET_MARKET=1823553
CTRL={'side':'UP','role':'SATELLITE_EXPAND','price':0.43,'qty':2.3255813953488373}
TRT={'side':'DOWN','role':'SATELLITE_REPAIR','price':0.51,'qty':1.9607843137254901}

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(int,float,str,bool)) or x is None:return x
    try:return float(x)
    except Exception:return str(x)

def digest(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

class E1OneShot(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,mutate=False):
        super().__init__(tape);self.mutate=bool(mutate);self.prefix=None;self.prefixDigest=None;self.targetObserved=False;self.intervention=None;self.worstFloor=min(0.0,float(self._physical_floor()))

    def process(self,t):
        super().process(t);self.worstFloor=min(self.worstFloor,float(self._physical_floor()))

    def _snapshot_prefix(self,t):
        live_orders={}
        for key,o in self.orders.items():
            if key in set(self.slot_key.values()) or base.v2.base.live(str(o.get('status') or '')):
                live_orders[key]={k:o.get(k) for k in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        snap={'t':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),'liveOrders':live_orders,'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),'placeHist':list(self.placeHist),'fillSequence':copy.deepcopy(self.fill_side_sequence)}
        self.prefix=norm(snap);self.prefixDigest=digest(self.prefix)

    def _pure_pair_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used:continue
            if not self._pair_ok(side,p):continue
            return float(p),float(1.0/p)
        return None

    def _coverage(self,side):
        debt=float(self._aggregate_for_repair_side(side));live_rem=0.0;live_keys=set()
        for _,key,o,role in self._live_role_rows(side=side):
            if role not in v3b.REPAIR_ROLES:continue
            live_keys.add(key);live_rem+=max(0.0,float(o.get('qty') or 0)-float(o.get('cum') or 0))
        pend=0.0
        if self.q_pending_active is not None and str(self.q_pending_active.get('side'))==side and str(self.q_pending_active.get('sourceKey') or '') not in live_keys:
            pend=max(0.0,float(self.q_pending_active.get('sourceRemainingQty') or 0))
        return debt,min(debt,live_rem+pend),max(0.0,debt-min(debt,live_rem+pend))

    def _open_one_option(self,t,qv,end):
        if int(t)==TARGET_T and not self.targetObserved:
            self.targetObserved=True;self._snapshot_prefix(t)
            dec=base.MinimalPairRoleSim._role_decision(self,qv);cand=self._pure_pair_candidate(dec[0])
            before_hist=len(self.slot_history);before_n=int(self.n)
            if self.mutate:
                debt,claim,resid=self._coverage(TRT['side']);tcand=self._pure_pair_candidate(TRT['side']);free=max(0,int(self.max_slots)-len(self.slot_key))
                checks={'decision':dec,'baselineCandidate':cand,'treatmentCandidate':tcand,'debt':debt,'claim':claim,'residual':resid,'freeSlots':free,'pendingActive':copy.deepcopy(self.q_pending_active),'qLadder':copy.deepcopy(self.q_ladder)}
                exact=bool(dec[0]==CTRL['side'] and dec[1]==CTRL['role'] and cand is not None and abs(cand[0]-CTRL['price'])<=1e-12 and abs(cand[1]-CTRL['qty'])<=1e-9 and tcand is not None and abs(tcand[0]-TRT['price'])<=1e-12 and abs(tcand[1]-TRT['qty'])<=1e-9 and resid+EPS>=TRT['qty'] and free>0 and self.q_pending_active is None)
                ok=False
                if exact:
                    ok=base.MinimalPairRoleSim._submit_role(self,int(t),TRT['side'],TRT['role'],TRT['price'],TRT['qty'],None,'GPT6_E1_ONE_SHOT_NATIVE_REPAIR_REPLACEMENT')
                new_events=self.slot_history[before_hist:]
                self.intervention={'mode':'TREATMENT','exactPreconditions':exact,'submitOk':bool(ok),'checks':norm(checks),'beforeN':before_n,'newSlotEvents':norm(new_events)}
                return
            else:
                out=super()._open_one_option(t,qv,end);new_events=self.slot_history[before_hist:]
                submits=[x for x in new_events if x.get('event')=='ROLE_SLOT_SUBMIT']
                self.intervention={'mode':'CONTROL','decision':dec,'candidate':cand,'beforeN':before_n,'newSlotEvents':norm(new_events),'submitMatchesFrozen':any(str(x.get('side'))==CTRL['side'] and str(x.get('role'))==CTRL['role'] and abs(float(x.get('price') or 0)-CTRL['price'])<=1e-12 and abs(float(x.get('qty') or 0)-CTRL['qty'])<=1e-9 for x in submits)}
                return out
        return super()._open_one_option(t,qv,end)

    def run_e1(self):
        r=super().run_qty('__UNSCORED__');r['e1OneShot']=True;r['e1Mutate']=self.mutate;r['e1PrefixDigest']=self.prefixDigest;r['e1Prefix']=self.prefix;r['e1Intervention']=self.intervention;r['worstIntramarketFloor']=float(self.worstFloor);return r

def metrics(r,winner):
    pnl=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
    return {'winnerPnlPostHoc':pnl,'fixedFavoredSide':'UP','fixedFavoredPayoff':float(r['upQty'])-float(r['buyNotional']),'oppositePayoff':float(r['downQty'])-float(r['buyNotional']),'terminalFloor':float(r['floor']),'terminalBest':float(r['best']),'worstIntramarketFloor':float(r['worstIntramarketFloor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),'twoSided':bool(r.get('twoSidedMaterialized')),'buyNotional':float(r['buyNotional']),'roleFills':r.get('roleFills') or {},'roleFillQty':r.get('roleFillQty') or {},'ledgerViolations':r['quantityLedgerSummary'].get('invariantViolations')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='e1_one_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{TARGET_MARKET}.json.xz',root)
        tape=root/'tapes'/f'{TARGET_MARKET}.json.xz';winner=str(co[TARGET_MARKET]['winner']).upper()
        A=E1OneShot(tape,False)
        try:ra=A.run_e1()
        finally:A.close()
        B=E1OneShot(tape,True)
        try:rb=B.run_e1()
        finally:B.close()
    ma,mb=metrics(ra,winner),metrics(rb,winner)
    prefixParity=bool(ra.get('e1PrefixDigest') and ra.get('e1PrefixDigest')==rb.get('e1PrefixDigest'))
    fillRetention=(mb['fills']/ma['fills']) if ma['fills'] else None
    checks={'prefixParity':prefixParity,'controlSubmitMatchesFrozen':bool((ra.get('e1Intervention') or {}).get('submitMatchesFrozen')),'treatmentExactPreconditions':bool((rb.get('e1Intervention') or {}).get('exactPreconditions')),'treatmentSubmitOk':bool((rb.get('e1Intervention') or {}).get('submitOk')),'controlLedgerClean':not bool(ma['ledgerViolations']),'treatmentLedgerClean':not bool(mb['ledgerViolations']),'fillRetention':fillRetention,'fillRetentionGe90':fillRetention is None or fillRetention>=0.90,'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],'max4Control':int(ra.get('maxSimultaneousSlots') or 0)<=4,'max4Treatment':int(rb.get('maxSimultaneousSlots') or 0)<=4}
    deltas={k:mb[k]-ma[k] for k in ('winnerPnlPostHoc','fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','worstIntramarketFloor','fills','submits','alternations','buyNotional')}
    out={'version':'GPT6_E1_V3B_ONE_SHOT_NATIVE_REPAIR_SMOKE1','date':'2026-09-06','researchOnly':True,'marketId':TARGET_MARKET,'winnerPostHocOnly':winner,'control':{'metrics':ma,'prefixDigest':ra.get('e1PrefixDigest'),'intervention':ra.get('e1Intervention')},'treatment':{'metrics':mb,'prefixDigest':rb.get('e1PrefixDigest'),'intervention':rb.get('e1Intervention')},'deltasTreatmentMinusControl':deltas,'checks':checks,'decision':'INVALID' if not all([checks['prefixParity'],checks['controlSubmitMatchesFrozen'],checks['treatmentExactPreconditions'],checks['treatmentSubmitOk'],checks['controlLedgerClean'],checks['treatmentLedgerClean'],checks['max4Control'],checks['max4Treatment']]) else ('FAIL_ACTIVITY' if not checks['fillRetentionGe90'] or not checks['twoSidedRetained'] else 'VALID_SMOKE_RESULT'),'boundary':['one receipt only','native Expand replaced by native ordinary Pair-Core Repair','same frozen V3B suffix on resulting state','fixed favored side UP frozen before outcome','winner posthoc only','no Target runtime input','realistic HFT','no dream fill','max4/no new capital','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
