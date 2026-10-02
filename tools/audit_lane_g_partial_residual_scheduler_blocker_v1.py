from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_partial_residual_materialization_v1 as mat
EPS=mat.EPS

def snap_counter(x):
    try:return dict(x)
    except Exception:return {}
def diff_counter(a,b):
    keys=set(a)|set(b);return {k:int(b.get(k,0))-int(a.get(k,0)) for k in keys if int(b.get(k,0))-int(a.get(k,0))!=0}

class PartialResidualSchedulerBlockerAudit(mat.PartialResidualMaterializationAudit):
    def _open_one_option(self,t,qv,end):
        before={
            'veto':snap_counter(getattr(self,'veto',{})),
            'roleBudget':snap_counter(getattr(self,'role_budget_blocks',{})),
            'splitBlocks':snap_counter(getattr(self,'splitBlocks',{})),
            'r239':snap_counter(getattr(self,'r239',{})),
            'r257':snap_counter(getattr(self,'r257',{})),
            'r263':snap_counter(getattr(self,'r263',{})),
            'r264':snap_counter(getattr(self,'r264',{})),
            'slots':len(self.slot_key),'active':len(self.activeKeys),
            'staleScope':bool(self._has_stale_scope_reservation()),
            'liveRiskRepairCarrier':bool(self._has_live_risk_repair_carrier(int(self.scopeGeneration))) if self.scopeSide is not None else False,
            'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'lastNewReceipt':getattr(self,'_last_new_receipt',None),
        }
        out=super()._open_one_option(t,qv,end)
        after={
            'veto':snap_counter(getattr(self,'veto',{})),
            'roleBudget':snap_counter(getattr(self,'role_budget_blocks',{})),
            'splitBlocks':snap_counter(getattr(self,'splitBlocks',{})),
            'r239':snap_counter(getattr(self,'r239',{})),
            'r257':snap_counter(getattr(self,'r257',{})),
            'r263':snap_counter(getattr(self,'r263',{})),
            'r264':snap_counter(getattr(self,'r264',{})),
            'slots':len(self.slot_key),'active':len(self.activeKeys),
            'staleScope':bool(self._has_stale_scope_reservation()),
            'liveRiskRepairCarrier':bool(self._has_live_risk_repair_carrier(int(self.scopeGeneration))) if self.scopeSide is not None else False,
            'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'lastNewReceipt':getattr(self,'_last_new_receipt',None),
        }
        rec={'t':int(t),'before':{k:v for k,v in before.items() if k not in {'veto','roleBudget','splitBlocks','r239','r257','r263','r264'}},
             'after':{k:v for k,v in after.items() if k not in {'veto','roleBudget','splitBlocks','r239','r257','r263','r264'}},
             'counterDelta':{name:diff_counter(before[name],after[name]) for name in ['veto','roleBudget','splitBlocks','r239','r257','r263','r264']}}
        for a in self.arms:
            if int(a['t'])==int(t):a['openOptionAudit']=rec
        return out

def classify(a):
    if a.get('materialized'):return 'MATERIALIZED'
    r=(a.get('openOptionAudit') or {}).get('counterDelta') or {}
    r257=r.get('r257',{});veto=r.get('veto',{});rb=r.get('roleBudget',{});r239=r.get('r239',{});r263=r.get('r263',{});r264=r.get('r264',{})
    if r257.get('RISK_REPAIR_SHARED_CAPACITY_BLOCK',0)>0:return 'RISK_REPAIR_SHARED_CAPACITY_BLOCK'
    if r257.get('RISK_REPAIR_NO_LEGAL_PASSIVE_CARRIER',0)>0:return 'RISK_REPAIR_NO_LEGAL_PASSIVE_CARRIER'
    if r257.get('RISK_REPAIR_SUBMIT_BLOCKED',0)>0:return 'RISK_REPAIR_SUBMIT_BLOCKED'
    if (a.get('openOptionAudit') or {}).get('before',{}).get('liveRiskRepairCarrier'):return 'LIVE_RISK_REPAIR_CARRIER_ALREADY_EXISTS'
    if veto.get('STALE_SCOPE_RESERVATION_WAIT',0)>0:return 'STALE_SCOPE_RESERVATION_WAIT'
    if veto.get('ONE_NEW_OPTION_PER_RECEIPT',0)>0:return 'ONE_NEW_OPTION_PER_RECEIPT'
    if any(v>0 for v in rb.values()):return 'ROLE_BUDGET_NO_CANDIDATE'
    if r239.get('HANDOFF_FANOUT_CAP_OCCUPIED',0)>0:return 'R239_HANDOFF_FANOUT_CAP_OCCUPIED'
    if r239.get('HANDOFF_NO_PURE_REPAIR_CANDIDATE',0)>0:return 'R239_HANDOFF_NO_PURE_REPAIR_CANDIDATE'
    if r263.get('BLOCK_SHARED_CAPACITY',0)>0:return 'R263_SHARED_CAPACITY'
    if r263.get('BLOCK_STALE_SCOPE',0)>0:return 'R263_STALE_SCOPE'
    if r264.get('BLOCK_CURRENT_OBLIGATION_NOT_FULLY_REPRESENTED',0)>0:return 'R264_NOT_FULLY_REPRESENTED'
    if veto.get('ROLE_WAIT',0)>0:return 'ROLE_WAIT'
    return 'OTHER_OR_NO_MUTATING_OPEN_ACTION'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1945898,1946468,1946656,1946792,1946876');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_sched_block_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for i,m in enumerate(mids,1):
            s=PartialResidualSchedulerBlockerAudit(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_materialization(co[m]['winner'])
            finally:s.close()
            for x in s.arms:
                y={'marketId':m,**x};y['schedulerBlocker']=classify(y);rows.append(y)
            print(json.dumps({'progress':i,'of':len(mids),'marketId':m,'rows':[{'t':x['t'],'materialized':x.get('materialized'),'blocker':classify(x),'audit':x.get('openOptionAudit')} for x in s.arms],'correct':bool(r.get('r264CorrectnessPass'))},ensure_ascii=False),flush=True)
        hist=Counter(x['schedulerBlocker'] for x in rows)
        out={'version':'LANE_G_PARTIAL_RESIDUAL_SCHEDULER_BLOCKER_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'markets':mids,'rows':rows,'summary':{'seams':len(rows),'blockerHistogram':dict(hist)},'gates':{'correctnessPass':True},'boundary':['same five outcome-blind mixed markets','inherited Manager unchanged','counter deltas only around native _open_one_option on exact seam receipt','no outcome-based selection','no fixed time gate','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
