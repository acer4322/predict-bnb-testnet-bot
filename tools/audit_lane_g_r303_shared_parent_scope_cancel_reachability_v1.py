from __future__ import annotations
import argparse, json, os, shutil, tempfile, zipfile, sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r3_03_contingent_composite_shared_parent_hft as r303
EPS=1e-9

class SharedParentScopeCancelAudit(r303.ContingentCompositeSharedParentHFT):
    """Behavior-inert audit of inherited scope-contract cancels against unresolved shared parents."""
    def __init__(self,tape,*a,**kw):
        self.scopeCancelAudit=[]
        self.scopeCancelStats=Counter()
        super().__init__(tape,*a,**kw)

    def _parent_state_for_key(self,key):
        pid=self.r303KeyParent.get(str(key)) if hasattr(self,'r303KeyParent') else None
        if pid is None and getattr(self,'r303Parent',None) and str(key) in getattr(self,'r303SharedKeys',set()):
            pid=int(self.r303Parent['pid'])
        if pid is None:return None,None
        try:return int(pid),self.r303Ledger.describe_parent(int(pid))
        except Exception:return int(pid),None

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid))
        if str(reason)=='REPAIR_BUNDLE_SCOPE_CONTRACT' and key is not None:
            pid,ps=self._parent_state_for_key(key)
            if pid is not None:
                rem=float((ps or {}).get('remainingDebt') or 0.0)
                status='UNRESOLVED_SHARED_PARENT' if rem>EPS else 'COMPLETE_SHARED_PARENT'
                self.scopeCancelStats[status]+=1
                self.scopeCancelAudit.append({
                    't':int(t),'key':str(key),'slotId':int(sid),'reason':str(reason),'parentId':pid,
                    'parentRemainingDebt':rem,'parentRepairPaid':float((ps or {}).get('repairPaid') or 0.0),
                    'parentInitialDebt':float((ps or {}).get('initialDebt') or 0.0),
                    'sharedKeys':sorted(getattr(self,'r303SharedKeys',set())),
                    'scopeGeneration':int(getattr(self,'scopeGeneration',0)),'scopeSide':getattr(self,'scopeSide',None),
                    'r303OverflowQty':float(getattr(self,'r303OverflowQty',0.0)),
                    'r303OverflowRisk':float(getattr(self,'r303OverflowRisk',0.0)),
                    'classification':status
                })
        return super()._request_cancel(t,sid,reason)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_scope_cancel_audit_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]; candidates=[]
        for m in mids:
            sim=SharedParentScopeCancelAudit(tmp/f'{m}.json.xz',1,4)
            try:r=sim.run_r303(co[m]['winner'])
            finally:sim.close()
            unresolved=[x for x in sim.scopeCancelAudit if x['classification']=='UNRESOLVED_SHARED_PARENT']
            row={'marketId':m,'r303Submits':int(r.get('r303Submits',0)),'sharedParentScopeCancelCount':len(sim.scopeCancelAudit),
                 'unresolvedSharedParentCancelCount':len(unresolved),'events':sim.scopeCancelAudit,
                 'correct':bool(r.get('r303CorrectnessPass')) and float(r.get('r303SplitMismatch',0.0))<=EPS and float(r.get('r303AuthorityOverrun',0.0))<=EPS}
            rows.append(row)
            for x in unresolved:candidates.append({'marketId':m,**x})
            print(json.dumps({'marketId':m,'r303Submits':row['r303Submits'],'scopeCancels':row['sharedParentScopeCancelCount'],'unresolvedCancels':row['unresolvedSharedParentCancelCount'],'correct':row['correct']},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R303_SHARED_PARENT_SCOPE_CANCEL_REACHABILITY_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'behaviorMutation':False,'markets':mids,'rows':rows,'candidates':candidates,
             'summary':{'marketCount':len(mids),'r303Markets':sum(1 for x in rows if x['r303Submits']>0),'candidateMarkets':len(set(x['marketId'] for x in candidates)),'candidateEvents':len(candidates)},
             'gates':{'correctnessPass':all(x['correct'] for x in rows)},
             'selectionBoundary':['outcome-blind instrumentation of inherited REPAIR_BUNDLE_SCOPE_CONTRACT requests','candidate requires live R303 shared carrier with authoritative parent remainingDebt > 0','no terminal/winner/action outcome used for candidate definition','winner read only for simulator terminal bookkeeping'],
             'policyBoundary':['behavior unchanged','no fixed seconds/windows/rank/age rule','same R303 one-unit authority','same max4 and <=180s boundary','pending gives zero payment/protection/credit','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary'],'gates':out['gates'],'candidateKeys':[(x['marketId'],x['t'],x['key'],x['parentRemainingDebt']) for x in candidates]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
