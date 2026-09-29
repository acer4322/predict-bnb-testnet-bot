from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;base=v3b.base

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-10)
    return a==b

def core(r):
    ks=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','quantityLedgerSummary')
    return {k:r.get(k) for k in ks}

class Scanner(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):super().__init__(tape);self.events=[]
    def clean_history(self,t):
        by={}
        for a in self.fill_accounting:
            if int(a.get('t') or 0)>=int(t) or float(a.get('confirmedQty') or 0)<=EPS:continue
            by.setdefault(int(a['t']),[]).append(a)
        clean=[]
        for tt in sorted(by):
            rs=by[tt];sides={str(x.get('side')) for x in rs}
            if len(sides)!=1 or any(float(x.get('matchedRepairQty') or 0)>EPS for x in rs):continue
            q=sum(float(x.get('overflowQty') or 0) for x in rs)
            if q<=EPS:continue
            clean.append({'t':tt,'side':next(iter(sides)),'riskQty':q})
        if not clean:return None
        last=clean[-1];recent=clean[-3:];run=1
        for x in reversed(clean[:-1]):
            if x['side']!=last['side']:break
            run+=1
        ru=sum(x['riskQty'] for x in recent if x['side']=='UP');rd=sum(x['riskQty'] for x in recent if x['side']=='DOWN')
        return {'priorCleanSide':last['side'],'priorCleanAgeMs':int(t)-last['t'],'priorCleanRunLength':run,
                'recentCleanCount':len(recent),'recentCleanUpRatio':sum(x['side']=='UP' for x in recent)/len(recent),
                'recentRiskWeightedSide':'UP' if ru>rd+EPS else ('DOWN' if rd>ru+EPS else 'TIE'),
                'recentRiskUpQty':ru,'recentRiskDownQty':rd,'last3Clean':recent}
    def role_for_forced_side(self,side):
        state,held,weak=self._state()
        if state=='EMPTY':return 'PROBE_CORE'
        if state=='ONE_SIDED':
            missing=weak
            if side==missing and self._core_for_side(missing) is None:return 'ECONOMIC_CORE'
            return 'SATELLITE_EXPAND' if side==held else 'SATELLITE_REPAIR'
        if weak is not None and side==weak and self._core_for_side(weak) is None:return 'ECONOMIC_CORE'
        return 'SATELLITE_REPAIR' if side==weak else 'SATELLITE_EXPAND'
    def pure_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used or not self._pair_ok(side,p):continue
            return {'side':side,'price':float(p),'qty':float(1.0/p)}
        return None
    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)>base.v2.NO_NEW_EXPOSURE_MS and len(self.slot_key)<self.max_slots:
            market_side,market_role,_,_=base.MinimalPairRoleSim._role_decision(self,qv);h=self.clean_history(t)
            if h and market_role=='SATELLITE_EXPAND' and h['priorCleanSide']!=market_side:
                hc=self.pure_candidate(h['priorCleanSide']);mc=self.pure_candidate(market_side)
                if hc is not None and mc is not None:
                    hr=self.role_for_forced_side(h['priorCleanSide'])
                    self.events.append({'t':int(t),'marketProposalSide':market_side,'marketRole':market_role,
                        'history':h,'historyForcedSide':h['priorCleanSide'],'historyForcedRole':hr,
                        'marketCandidate':mc,'historyCandidateDiagnostic':hc,
                        'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'cost':float(self.cost),
                        'branchPayoffs':{'UP':float(self.inv['UP']-self.cost),'DOWN':float(self.inv['DOWN']-self.cost)},
                        'aggregateDebt':{'repairUP':float(self._aggregate_for_repair_side('UP')),'repairDOWN':float(self._aggregate_for_repair_side('DOWN'))},
                        'liveRoles':[{'slotId':int(sid),'side':str(o.get('side')),'role':role,'price':float(o.get('price') or 0),'cum':float(o.get('cum') or 0),'cancelRequested':bool(o.get('cancelRequested'))} for sid,key,o,role in self._live_role_rows()],
                        'qLadder':None if self.q_ladder is None else {'side':self.q_ladder.get('side'),'role':self.q_ladder.get('role'),'route':self.q_ladder.get('route'),'responsibilityId':self.q_ladder.get('responsibilityId')},
                        'qPendingActive':self.q_pending_active is not None,'freeSlots':int(self.max_slots-len(self.slot_key)),
                        'legalActionIntent':['MARKET_DIRECTION_BASELINE','HISTORY_DIRECTION_OVERRIDE','HOLD']})
        return super()._open_one_option(t,qv,end)
    def run_scan(self):r=super().run_qty('__UNSCORED__');r['events']=self.events;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='hist_dir_v2_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:ra=A.run_qty('__UNSCORED__')
            finally:A.close()
            B=Scanner(tape)
            try:rb=B.run_scan()
            finally:B.close()
            ca,cb=core(ra),core(rb);par={k:eq(ca[k],cb[k]) for k in ca};ev=rb['events']
            rows.append({'marketId':mid,'allBehaviorParity':all(par.values()),'parity':par,'ledgerViolations':(rb.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},'eligibleCount':len(ev),'events':ev})
            print(json.dumps({'progress':i,'marketId':mid,'parity':all(par.values()),'eligibleCount':len(ev)},ensure_ascii=False),flush=True)
    flat=[]
    for r in rows:
        for e in r['events']:flat.append({'marketId':r['marketId'],**e})
    first=[];seen=set()
    for e in flat:
        if e['marketId'] not in seen:seen.add(e['marketId']);first.append(e)
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_VS_MARKET_DIRECTION_STATE_SCAN_V2','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,
         'markets':mids,'allBehaviorParity':all(r['allBehaviorParity'] for r in rows),'rows':rows,'eligibleStateCount':len(flat),'eligibleMarketCount':len(seen),'firstEligiblePerMarket':first,'smokeCandidates':first[:3],
         'selectionContract':['strict-past current-V3B exact-FIFO state','prior clean risk clock = single-side confirmed fill clock with zero matchedRepairQty and positive overflowQty','baseline receipt must be native SATELLITE_EXPAND and priorCleanSide must oppose baseline marketProposalSide','both market side and history side must have Pair-legal distinct-price candidate and at least one free slot','history branch only overrides direction at the target receipt; role/qty/route remain current V3B semantics','HOLD skips only target open decision; suffix returns to current V3B','no winner/PnL/Target future input','behavior-inert scanner parity required','realistic HFT/exact FIFO/max4/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'eligibleStateCount':len(flat),'eligibleMarketCount':len(seen),'smokeCandidates':first[:3]},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
