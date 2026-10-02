from __future__ import annotations
import argparse,json,math,tempfile,zipfile
from pathlib import Path
from collections import Counter
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS;base=v3b.base
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND_ROLES={'PROBE_CORE','SATELLITE_EXPAND'}

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-10)
    return a==b

def core(r):
    ks=['submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','quantityLedgerSummary','quantityPaymentRows']
    return {k:r.get(k) for k in ks}

class RoleSwitchScan(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape);self.rs=[];self.counts=Counter()
    def _pure_pair_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used:continue
            if not self._pair_ok(side,p):continue
            q=1.0/float(p)
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            return {'side':side,'price':float(p),'qty':float(q)}
        return None
    def _progress_on_debt_side(self,debt_side):
        lots=[x for x in self.resp_all if str(x.get('side'))==str(debt_side)]
        ini=sum(float(x.get('initialQty') or 0.0) for x in lots)
        paid=sum(float(x.get('paidQty') or 0.0) for x in lots)
        rem=sum(float(x.get('remainingQty') or 0.0) for x in lots)
        return ini,paid,rem
    def _open_one_option(self,t,qv,end):
        pre=None
        if int(end)-int(t)>base.v2.NO_NEW_EXPOSURE_MS:
            state,_,weak=self._state()
            if state=='TWO_SIDED' and weak in {'UP','DOWN'}:
                expand='DOWN' if weak=='UP' else 'UP'
                ini,paid,rem=self._progress_on_debt_side(expand)
                if paid>EPS and rem>EPS:
                    rc=self._pure_pair_candidate(weak);ec=self._pure_pair_candidate(expand)
                    free=max(0,int(self.max_slots)-len(self.slot_key))
                    self.counts['PARTIAL_PROGRESS_CLOCKS']+=1
                    if rc is not None:self.counts['REPAIR_CANDIDATE']+=1
                    if ec is not None:self.counts['EXPAND_CANDIDATE']+=1
                    if rc is not None and ec is not None and free>0:
                        self.counts['BOTH_CANDIDATES_FREE_SLOT']+=1
                        pre={'t':int(t),'weakSide':weak,'expandSide':expand,'repairCandidate':rc,'expandCandidate':ec,
                             'freeSlots':free,'initialDebtQty':ini,'paidDebtQty':paid,'remainingDebtQty':rem,
                             'repairProgressFrac':paid/ini if ini>EPS else 0.0,
                             'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'cost':float(self.cost),
                             'floor':float(self._physical_floor()),'best':float(max(self.inv.values())-self.cost),
                             'liveSlots':len(self.slot_key),'qLadderLive':bool(self.q_ladder is not None),'qPendingActive':bool(self.q_pending_active is not None),
                             'liveRepairSlots':sum(1 for _,_,_,r in self._live_role_rows() if r in REPAIR_ROLES),
                             'liveExpandSlots':sum(1 for _,_,_,r in self._live_role_rows() if r in EXPAND_ROLES),
                             'book':{'imbalance':float(qv.get('imb') or 0.0),'spread':float(qv.get('spread') or 0.0),
                                     'weakBid':float(qv[weak]['bid']),'weakAsk':float(qv[weak]['ask']),
                                     'expandBid':float(qv[expand]['bid']),'expandAsk':float(qv[expand]['ask'])}}
        before_n=int(self.n);before_sub=int(self.submits)
        out=super()._open_one_option(t,qv,end)
        if pre is not None and int(self.submits)>before_sub:
            # May be one Active or one Passive native action. Restrict this corpus to naturally materialized PASSIVE role actions.
            candidates=[]
            for n in range(before_n,int(self.n)):
                for side in ('UP','DOWN'):
                    k=f'{side}_{n}'
                    if k in self.orders:candidates.append(k)
            for key in candidates:
                role=str(self.key_role.get(key,'UNASSIGNED'));o=self.orders[key]
                is_active=key in getattr(self,'activeKeys',set())
                if is_active or role not in REPAIR_ROLES|EXPAND_ROLES:continue
                nativeClass='REPAIR' if role in REPAIR_ROLES else 'EXPAND'
                alt=pre['expandCandidate'] if nativeClass=='REPAIR' else pre['repairCandidate']
                row={**pre,'nativeKey':key,'nativeSide':str(o['side']),'nativeRole':role,'nativeClass':nativeClass,
                     'nativePrice':float(o['price']),'nativeQty':float(o['qty']),'alternativeClass':'EXPAND' if nativeClass=='REPAIR' else 'REPAIR','alternative':alt}
                self.rs.append(row);self.counts['ELIGIBLE_MATERIALIZED_'+nativeClass]+=1
        return out
    def run_scan(self):
        r=super().run_qty('__UNSCORED__');r['roleSwitchScan']=self.rs;r['roleSwitchCounts']=dict(self.counts);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_rs_scan_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:z.extract(f'tapes/{m}.json.xz',root)
        for i,m in enumerate(mids,1):
            tape=root/'tapes'/f'{m}.json.xz'
            A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:ra=A.run_qty('__UNSCORED__')
            finally:A.close()
            B=RoleSwitchScan(tape)
            try:rb=B.run_scan()
            finally:B.close()
            parity={k:eq(core(ra)[k],core(rb)[k]) for k in core(ra)}
            row={'marketId':m,'allBehaviorParity':all(parity.values()),'parity':parity,'ledgerViolations':rb.get('quantityLedgerSummary',{}).get('invariantViolations'),
                 'counts':rb['roleSwitchCounts'],'events':rb['roleSwitchScan']};rows.append(row)
            print(json.dumps({'progress':i,'of':len(mids),'marketId':m,'parity':row['allBehaviorParity'],'counts':row['counts'],'eligible':len(row['events'])},ensure_ascii=False),flush=True)
    events=[]
    for r in rows:
        for e in r['events']:events.append({'marketId':r['marketId'],**e})
    events.sort(key=lambda x:(x['marketId'],x['t'],x['nativeKey']))
    by=Counter(e['nativeClass'] for e in events)
    out={'version':'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_STATE_SCAN_V1_20260907','researchOnly':True,'runtimeAuthority':False,
         'markets':mids,'allBehaviorParity':all(r['allBehaviorParity'] for r in rows),'ledgerClean':all(not r['ledgerViolations'] for r in rows),
         'eligibleStates':len(events),'eligibleMarkets':len(set(e['marketId'] for e in events)),'byNativeClass':dict(by),'rows':rows,'events':events,
         'boundary':['current V3B behavior unchanged','TWO_SIDED only','requires confirmed Repair payment on still-open responsibility','both weak-side Repair and opposite-side re-Expand have distinct live Pair-legal candidates before native action','at least one free max4 slot','native branch must naturally materialize a PASSIVE role action','alternative is shadow only','no threshold fitting','no winner/Target/future input','no NEW24-B/no dream fill/no 8781']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'ledgerClean':out['ledgerClean'],'eligibleStates':out['eligibleStates'],'eligibleMarkets':out['eligibleMarkets'],'byNativeClass':out['byNativeClass'],'first':events[:3]},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
