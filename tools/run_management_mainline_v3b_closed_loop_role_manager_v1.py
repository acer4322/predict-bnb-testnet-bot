from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile
from pathlib import Path
from collections import Counter
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS;base=v3b.base
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND_ROLES={'PROBE_CORE','SATELLITE_EXPAND'}
POLICIES=('NATIVE','ALWAYS_REPAIR','ALWAYS_REEXPAND','ALTERNATE_ROLE')

class ClosedLoopManager(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,policy):
        super().__init__(tape);self.policy=str(policy);self.mgmt=Counter();self.mgmt_events=[];self.last_managed_class=None
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
        ini=sum(float(x.get('initialQty') or 0.0) for x in lots);paid=sum(float(x.get('paidQty') or 0.0) for x in lots);rem=sum(float(x.get('remainingQty') or 0.0) for x in lots)
        return ini,paid,rem
    def _repair_role(self,side): return 'ECONOMIC_CORE' if self._core_for_side(side) is None else 'SATELLITE_REPAIR'
    def _forced_arm(self,t,qv,side,role):
        if role not in REPAIR_ROLES or self.q_ladder is not None or self.q_pending_active is not None:return None
        lot=self._oldest_for_repair_side(side);agg=float(self._aggregate_for_repair_side(side))
        if lot is None or agg<=EPS:return None
        return {'t':int(t),'side':side,'role':role,'responsibilityId':int(lot['id']),'oldestRemainingQty':float(lot['remainingQty']),'aggregateOutstandingQty':agg,'bornAt':int(lot['bornAt']),'expandSide':lot['side'],'expandPrice':float(lot['price']),'qv':qv}
    def _force_one(self,t,qv,kind):
        if self.q_pending_active is not None:return False,None,None,None
        if len(self.slot_key)>=self.max_slots:return False,None,None,None
        state,_,weak=self._state()
        if state!='TWO_SIDED' or weak not in {'UP','DOWN'}:return False,None,None,None
        expand='DOWN' if weak=='UP' else 'UP';side=weak if kind=='REPAIR' else expand;role=self._repair_role(side) if kind=='REPAIR' else 'SATELLITE_EXPAND'
        self.q_arm=self._forced_arm(t,qv,side,role) if kind=='REPAIR' else None
        try:
            cand=self._candidate_from_levels(side,True,False)
            if cand is None:return False,side,role,None
            p,q,proj=cand;before=int(self.submits)
            ok=bool(self._submit_role(int(t),side,role,float(p),float(q),proj,'MANAGEMENT_MAINLINE_CLOSED_LOOP_ROLE_MANAGER_V1'))
            return bool(ok and int(self.submits)>before),side,role,{'price':float(p),'qty':float(q)}
        finally:self.q_arm=None
    def _eligible(self,t,qv,end):
        if int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS:return None
        if self.q_pending_active is not None:return None
        state,_,weak=self._state()
        if state!='TWO_SIDED' or weak not in {'UP','DOWN'}:return None
        expand='DOWN' if weak=='UP' else 'UP';ini,paid,rem=self._progress_on_debt_side(expand)
        if paid<=EPS or rem<=EPS:return None
        rc=self._pure_pair_candidate(weak);ec=self._pure_pair_candidate(expand);free=max(0,int(self.max_slots)-len(self.slot_key))
        if rc is None or ec is None or free<=0:return None
        return {'weakSide':weak,'expandSide':expand,'repairProgressFrac':paid/ini if ini>EPS else 0.0,'remainingDebtQty':rem,'repairCandidate':rc,'expandCandidate':ec,'freeSlots':free}
    def _open_one_option(self,t,qv,end):
        if self.policy=='NATIVE':return super()._open_one_option(t,qv,end)
        e=self._eligible(t,qv,end)
        if e is None:return super()._open_one_option(t,qv,end)
        self.mgmt['ELIGIBLE']+=1
        if self.policy=='ALWAYS_REPAIR':choice='REPAIR'
        elif self.policy=='ALWAYS_REEXPAND':choice='REEXPAND'
        else:
            choice='REEXPAND' if self.last_managed_class=='REPAIR' else 'REPAIR'
        ok,side,role,cand=self._force_one(t,qv,choice)
        if ok:
            self.mgmt['FORCED_'+choice]+=1;self.last_managed_class=choice
            self.mgmt_events.append({'t':int(t),'choice':choice,'side':side,'role':role,'repairProgressFrac':float(e['repairProgressFrac']),'remainingDebtQty':float(e['remainingDebtQty']),'candidate':cand})
            return None
        self.mgmt['FORCE_FAILED_FALLBACK_NATIVE']+=1
        return super()._open_one_option(t,qv,end)
    def run_managed(self):
        r=super().run_qty('__UNSCORED__');r['managementCounters']=dict(self.mgmt);r['managementEvents']=self.mgmt_events;return r

def metrics(r,winner):
    up=float(r['upQty']);dn=float(r['downQty']);c=float(r['buyNotional']);w=str(winner).upper();pnl=(up if w=='UP' else dn)-c
    return {'pnl':pnl,'floor':float(r['floor']),'best':float(r['best']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),'upQty':up,'downQty':dn,'buyNotional':c,'maxSlots':int(r.get('maxSimultaneousSlots') or 0),'ledgerViolations':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},'managedActiveSubmits':int((r.get('quantityLadderCounters') or {}).get('managedActiveSubmits',0)),'managedRepairQty':float(r.get('quantityManagedRepairQtyDiagnostic') or 0.0)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 rows=[]
 with tempfile.TemporaryDirectory(prefix='mgmt_closed_loop_v1_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:z.extract(f'tapes/{m}.json.xz',root)
  for i,m in enumerate(mids,1):
   tape=root/'tapes'/f'{m}.json.xz';winner=co[m]['winner'];pr={}
   for pol in POLICIES:
    s=ClosedLoopManager(tape,pol)
    try:r=s.run_managed()
    finally:s.close()
    pr[pol]={'metrics':metrics(r,winner),'managementCounters':r.get('managementCounters') or {},'managementEvents':r.get('managementEvents') or []}
   native=pr['NATIVE']['metrics'];checks={'allLedgerClean':all(not pr[p]['metrics']['ledgerViolations'] for p in POLICIES),'allMax4':all(pr[p]['metrics']['maxSlots']<=4 for p in POLICIES)}
   row={'marketId':m,'winnerPostHocOnly':winner,'policies':pr,'checks':checks};rows.append(row)
   print(json.dumps({'progress':i,'of':len(mids),'marketId':m,'checks':checks,'pnl':{p:round(pr[p]['metrics']['pnl'],6) for p in POLICIES},'eligible':{p:pr[p]['managementCounters'].get('ELIGIBLE',0) for p in POLICIES}},ensure_ascii=False),flush=True)
 out={'version':'MANAGEMENT_MAINLINE_V3B_CLOSED_LOOP_ROLE_MANAGER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'allSafetyPass':all(all(r['checks'].values()) for r in rows),'boundary':['current V3B exact-HFT/exact FIFO','manager acts only at current legal partial-progress role-switch seam','pending Active states untouched/native','no new slot/risk/credit/responsibility authority','forced action uses current V3B candidate and submit semantics','all non-eligible receipts remain native V3B','diagnostic policies only, not promotion candidates','winner post-hoc scoring only','max4/no dream fill/no NEW24-B/no 8781']}
 op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allSafetyPass':out['allSafetyPass'],'markets':len(rows)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
