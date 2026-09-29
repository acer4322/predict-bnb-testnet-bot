from __future__ import annotations
import argparse, copy, hashlib, json, os, tempfile, zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;base=v3b.base
SPECS={
  1823553:{'t':1788158708092,'favoredSide':'UP','ctrl':{'side':'UP','role':'SATELLITE_EXPAND','price':0.43,'qty':2.3255813953488373},'trt':{'side':'DOWN','role':'SATELLITE_REPAIR','price':0.51,'qty':1.9607843137254901}},
  1823603:{'t':1788159316643,'favoredSide':'DOWN','ctrl':{'side':'DOWN','role':'SATELLITE_EXPAND','price':0.32,'qty':3.125},'trt':{'side':'UP','role':'SATELLITE_REPAIR','price':0.64,'qty':1.5625}},
  1823611:{'t':1788159602698,'favoredSide':'UP','ctrl':{'side':'UP','role':'SATELLITE_EXPAND','price':0.26,'qty':3.846153846153846},'trt':{'side':'DOWN','role':'SATELLITE_REPAIR','price':0.60,'qty':1.6666666666666667}},
}

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(int,float,str,bool)) or x is None:return x
    try:return float(x)
    except Exception:return str(x)

def digest(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

class E1OneShot(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,spec,mutate=False):
        super().__init__(tape);self.spec=spec;self.mutate=bool(mutate);self.prefix=None;self.prefixDigest=None;self.targetObserved=False;self.intervention=None;self.worstFloor=min(0.0,float(self._physical_floor()))
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
        claim=min(debt,live_rem+pend)
        return debt,claim,max(0.0,debt-claim)
    def _open_one_option(self,t,qv,end):
        target_t=int(self.spec['t']);ctrl=self.spec['ctrl'];trt=self.spec['trt']
        if int(t)==target_t and not self.targetObserved:
            self.targetObserved=True;self._snapshot_prefix(t)
            dec=base.MinimalPairRoleSim._role_decision(self,qv);cand=self._pure_pair_candidate(dec[0])
            before_hist=len(self.slot_history);before_n=int(self.n)
            if self.mutate:
                debt,claim,resid=self._coverage(trt['side']);tcand=self._pure_pair_candidate(trt['side']);free=max(0,int(self.max_slots)-len(self.slot_key))
                checks={'decision':dec,'baselineCandidate':cand,'treatmentCandidate':tcand,'debt':debt,'claim':claim,'residual':resid,'freeSlots':free,'pendingActive':copy.deepcopy(self.q_pending_active),'qLadder':copy.deepcopy(self.q_ladder)}
                exact=bool(dec[0]==ctrl['side'] and dec[1]==ctrl['role'] and cand is not None and abs(cand[0]-ctrl['price'])<=1e-12 and abs(cand[1]-ctrl['qty'])<=1e-9 and tcand is not None and abs(tcand[0]-trt['price'])<=1e-12 and abs(tcand[1]-trt['qty'])<=1e-9 and resid+EPS>=trt['qty'] and free>0 and self.q_pending_active is None)
                ok=False
                if exact:
                    ok=base.MinimalPairRoleSim._submit_role(self,int(t),trt['side'],trt['role'],trt['price'],trt['qty'],None,'GPT6_E1_ONE_SHOT_NATIVE_REPAIR_REPLACEMENT_SMOKE3')
                self.intervention={'mode':'TREATMENT','exactPreconditions':exact,'submitOk':bool(ok),'checks':norm(checks),'beforeN':before_n,'newSlotEvents':norm(self.slot_history[before_hist:])}
                return
            out=super()._open_one_option(t,qv,end)
            new_events=self.slot_history[before_hist:];submits=[x for x in new_events if x.get('event')=='ROLE_SLOT_SUBMIT']
            self.intervention={'mode':'CONTROL','decision':dec,'candidate':cand,'beforeN':before_n,'newSlotEvents':norm(new_events),'submitMatchesFrozen':any(str(x.get('side'))==ctrl['side'] and str(x.get('role'))==ctrl['role'] and abs(float(x.get('price') or 0)-ctrl['price'])<=1e-12 and abs(float(x.get('qty') or 0)-ctrl['qty'])<=1e-9 for x in submits)}
            return out
        return super()._open_one_option(t,qv,end)
    def run_e1(self):
        r=super().run_qty('__UNSCORED__');r['e1PrefixDigest']=self.prefixDigest;r['e1Intervention']=self.intervention;r['worstIntramarketFloor']=float(self.worstFloor);return r

def metrics(r,winner,favored):
    opp='DOWN' if favored=='UP' else 'UP';cost=float(r['buyNotional'])
    return {'winnerPnlPostHoc':float(r['upQty' if winner=='UP' else 'downQty'])-cost,'fixedFavoredSide':favored,'fixedFavoredPayoff':float(r['upQty' if favored=='UP' else 'downQty'])-cost,'oppositePayoff':float(r['upQty' if opp=='UP' else 'downQty'])-cost,'terminalFloor':float(r['floor']),'terminalBest':float(r['best']),'worstIntramarketFloor':float(r['worstIntramarketFloor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),'twoSided':bool(r.get('twoSidedMaterialized')),'buyNotional':cost,'roleFills':r.get('roleFills') or {},'roleFillQty':r.get('roleFillQty') or {},'ledgerViolations':r['quantityLedgerSummary'].get('invariantViolations')}

def run_market(tape,winner,spec):
    A=E1OneShot(tape,spec,False)
    try:ra=A.run_e1()
    finally:A.close()
    B=E1OneShot(tape,spec,True)
    try:rb=B.run_e1()
    finally:B.close()
    ma,mb=metrics(ra,winner,spec['favoredSide']),metrics(rb,winner,spec['favoredSide'])
    prefix=bool(ra.get('e1PrefixDigest') and ra.get('e1PrefixDigest')==rb.get('e1PrefixDigest'));ret=(mb['fills']/ma['fills']) if ma['fills'] else None
    checks={'prefixParity':prefix,'controlSubmitMatchesFrozen':bool((ra.get('e1Intervention') or {}).get('submitMatchesFrozen')),'treatmentExactPreconditions':bool((rb.get('e1Intervention') or {}).get('exactPreconditions')),'treatmentSubmitOk':bool((rb.get('e1Intervention') or {}).get('submitOk')),'controlLedgerClean':not bool(ma['ledgerViolations']),'treatmentLedgerClean':not bool(mb['ledgerViolations']),'fillRetention':ret,'fillRetentionGe90':ret is None or ret>=0.90,'twoSidedRetained':(not ma['twoSided']) or mb['twoSided'],'max4Control':int(ra.get('maxSimultaneousSlots') or 0)<=4,'max4Treatment':int(rb.get('maxSimultaneousSlots') or 0)<=4}
    deltas={k:mb[k]-ma[k] for k in ('winnerPnlPostHoc','fixedFavoredPayoff','oppositePayoff','terminalFloor','terminalBest','worstIntramarketFloor','fills','submits','alternations','buyNotional')}
    valid=all([checks['prefixParity'],checks['controlSubmitMatchesFrozen'],checks['treatmentExactPreconditions'],checks['treatmentSubmitOk'],checks['controlLedgerClean'],checks['treatmentLedgerClean'],checks['max4Control'],checks['max4Treatment']])
    return {'control':{'metrics':ma,'prefixDigest':ra.get('e1PrefixDigest'),'intervention':ra.get('e1Intervention')},'treatment':{'metrics':mb,'prefixDigest':rb.get('e1PrefixDigest'),'intervention':rb.get('e1Intervention')},'deltasTreatmentMinusControl':deltas,'checks':checks,'valid':valid}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
    with tempfile.TemporaryDirectory(prefix='e1_smoke3_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in SPECS:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(SPECS,1):
            winner=str(co[mid]['winner']).upper();res=run_market(root/'tapes'/f'{mid}.json.xz',winner,SPECS[mid]);row={'marketId':mid,'winnerPostHocOnly':winner,'spec':SPECS[mid],**res};rows.append(row)
            print(json.dumps({'progress':i,'marketId':mid,'valid':res['valid'],'delta':res['deltasTreatmentMinusControl'],'checks':res['checks']},ensure_ascii=False),flush=True)
    valid_rows=[r for r in rows if r['valid']]
    def ssum(path):return sum(float(r['deltasTreatmentMinusControl'][path]) for r in valid_rows)
    agg={'markets':len(rows),'validMarkets':len(valid_rows),'allCorrectnessPass':len(valid_rows)==len(rows),'allFillRetentionGe90':all(r['checks']['fillRetentionGe90'] for r in valid_rows),'allTwoSidedRetained':all(r['checks']['twoSidedRetained'] for r in valid_rows),'sumDeltaFixedFavoredPayoff':ssum('fixedFavoredPayoff'),'sumDeltaOppositePayoff':ssum('oppositePayoff'),'sumDeltaWinnerPnlPostHoc':ssum('winnerPnlPostHoc'),'sumDeltaTerminalFloor':ssum('terminalFloor'),'sumDeltaWorstIntramarketFloor':ssum('worstIntramarketFloor'),'sumDeltaFills':ssum('fills'),'sumDeltaSubmits':ssum('submits'),'sumDeltaAlternations':ssum('alternations'),'favoredImprovedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']>EPS for r in valid_rows),'favoredHarmedMarkets':sum(r['deltasTreatmentMinusControl']['fixedFavoredPayoff']<-EPS for r in valid_rows),'floorImprovedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']>EPS for r in valid_rows),'floorHarmedMarkets':sum(r['deltasTreatmentMinusControl']['terminalFloor']<-EPS for r in valid_rows)}
    economic_pass=bool(agg['allCorrectnessPass'] and agg['allFillRetentionGe90'] and agg['allTwoSidedRetained'] and agg['sumDeltaFixedFavoredPayoff']>=-1e-8 and agg['sumDeltaTerminalFloor']>=-1e-8 and agg['sumDeltaWinnerPnlPostHoc']>=-1e-8 and agg['sumDeltaWorstIntramarketFloor']>=-1e-8)
    out={'version':'GPT6_E1_V3B_ONE_SHOT_NATIVE_REPAIR_SMOKE3','date':'2026-09-07','researchOnly':True,'markets':list(SPECS),'rows':rows,'aggregate':agg,'decision':'KEEP_FOR_NEXT_FALSIFICATION' if economic_pass else ('INVALID' if not agg['allCorrectnessPass'] else 'FAIL_FOR_THIS_OBJECTIVE'),'boundary':['three chronology-selected markets; first eligible seam per market','one native action replacement per market','same frozen V3B suffix from resulting state','fixed favored side frozen before outcome','winner posthoc only','realistic HFT/no dream fill','exact-FIFO/max4/no new capital','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'decision':out['decision']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
