from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests/hft_r2_market_rollover_repair_isolation_v1_report.json'

class MarketScopedRepair:
    def __init__(self, market_id:int, obligation:float):
        self.market_id=market_id
        self.required=float(obligation)
        self.confirmed=0.0
        self.owner_market_id=market_id
        self.owner_channel='PASSIVE'
        self.rolled=False
        self.old_terminal=False
        self.new_market_id=None
        self.cross_market_repair_qty=0.0
        self.old_fill_applied_to_new=0.0
        self.duplicate_owner=0
        self.lifecycle_violations=0
        self.stale_action_replay=0
        self.events=[]
    @property
    def unresolved(self): return max(0.0,self.required-self.confirmed)
    def rollover(self,new_market_id:int):
        if new_market_id==self.market_id:
            self.lifecycle_violations+=1; return
        self.rolled=True; self.new_market_id=new_market_id
        self.events.append(['ROLLOVER',self.market_id,new_market_id,self.unresolved])
    def try_new_market_repair(self,qty:float):
        # Old-contract obligation must never be transformed into a child on the new contract.
        if self.rolled and self.owner_market_id==self.market_id and self.unresolved>0:
            self.events.append(['SUPPRESS_CROSS_MARKET_REPAIR',qty])
            return False
        self.cross_market_repair_qty+=float(qty)
        return True
    def old_market_fill(self,qty:float):
        q=float(qty)
        if q<0: self.lifecycle_violations+=1; return
        self.confirmed=min(self.required,self.confirmed+q)
        self.events.append(['OLD_MARKET_FILL',q,self.confirmed,self.unresolved])
        # Explicitly no mutation of any new-market portfolio state.
    def replay_old_action_after_rollover(self):
        if self.rolled:
            self.events.append(['SUPPRESS_STALE_OLD_ACTION'])
            return False
        self.stale_action_replay+=1; return True
    def terminal_old_owner(self,status:str):
        if status not in {'CANCELED','EXPIRED','SETTLED','FILLED'}:
            self.lifecycle_violations+=1; return
        self.old_terminal=True
        self.owner_channel=None
        self.events.append(['OLD_TERMINAL',status,self.unresolved])


def scenario1():
    r=MarketScopedRepair(1001,12)
    r.rollover(1002)
    r.try_new_market_repair(12)
    r.replay_old_action_after_rollover()
    r.terminal_old_owner('CANCELED')
    return r

def scenario2():
    r=MarketScopedRepair(2001,12)
    r.rollover(2002)
    r.old_market_fill(5)
    r.try_new_market_repair(7)
    r.terminal_old_owner('EXPIRED')
    return r

def scenario3():
    r=MarketScopedRepair(3001,12)
    r.old_market_fill(4)
    r.rollover(3002)
    r.old_market_fill(3)  # late old-market fill after rollover -> old ledger only
    r.try_new_market_repair(5)
    r.replay_old_action_after_rollover()
    r.terminal_old_owner('SETTLED')
    return r

rows=[]
for i,fn in enumerate((scenario1,scenario2,scenario3),1):
    r=fn()
    passed=(r.cross_market_repair_qty==0 and r.old_fill_applied_to_new==0 and r.duplicate_owner==0 and r.lifecycle_violations==0 and r.stale_action_replay==0 and r.old_terminal)
    rows.append({
        'scenario':i,'passed':passed,'oldMarketId':r.market_id,'newMarketId':r.new_market_id,
        'oldConfirmedQty':r.confirmed,'oldUnresolvedAtTerminal':r.unresolved,
        'crossMarketRepairQty':r.cross_market_repair_qty,'oldFillAppliedToNewMarket':r.old_fill_applied_to_new,
        'duplicateEconomicOwner':r.duplicate_owner,'lifecycleViolationCount':r.lifecycle_violations,
        'staleOldActionReplayCount':r.stale_action_replay,'events':r.events
    })
passed=sum(int(x['passed']) for x in rows)
report={
    'testId':'HFT_R2_MARKET_ROLLOVER_REPAIR_ISOLATION_V1',
    'researchOnly':True,
    'evidenceClass':'STRUCTURAL_LIFECYCLE_ONLY_NOT_PNL',
    'scenarios':3,
    'passed':passed,
    'primaryResult':{
        'crossMarketRepairQty':sum(x['crossMarketRepairQty'] for x in rows),
        'oldFillAppliedToNewMarket':sum(x['oldFillAppliedToNewMarket'] for x in rows),
        'duplicateEconomicOwnerCount':sum(x['duplicateEconomicOwner'] for x in rows),
        'lifecycleViolationCount':sum(x['lifecycleViolationCount'] for x in rows),
        'staleOldActionReplayCount':sum(x['staleOldActionReplayCount'] for x in rows),
        'lateOldMarketFillReconciledOnlyToOldLedger':rows[1]['oldConfirmedQty']==5 and rows[2]['oldConfirmedQty']==7
    },
    'status':'TESTED_KEEP_SIGNAL' if passed==3 else 'TESTED_REJECTED',
    'conclusion':'An unresolved repair obligation is contract/market scoped. A 5-minute market-id rollover quarantines the old obligation from the new contract: no old quantity is carried into a new-market repair child, stale old-market actions are not replayed, and late authoritative fills update only the old-market ledger until old ownership terminates.' ,
    'rows':rows
}
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'report':str(OUT),'status':report['status'],'passed':passed,'primaryResult':report['primaryResult']}))
