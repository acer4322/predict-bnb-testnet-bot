from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9

class SelfFundedRepairOverflow(r1.QueueAwareRepairRoutingSim):
    """Diagnostic: Repair-first composite may self-fund its unavoidable Overflow when the
    full physical order remains non-worsening versus current realized Floor. External realized
    credit is still required if the full composite would worsen Floor. All debt/reservation identity
    rules and overflow accounting remain exact.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.selfFund=Counter()
    def _repair_split(self,side,p,q):
        debt=self._scope_debt_qty(); reserved=self._reserved_repair_quota(side); available=max(0.0,debt-reserved); rq=min(float(q),available)
        if rq<=EPS:
            self.splitBlocks['NO_UNRESERVED_REPAIR_DEBT']+=1;return None
        before=self._physical_floor(); repair_floor=self._candidate_alone_floor(side,p,rq)
        if repair_floor<=before+EPS:
            self.splitBlocks['REPAIR_PORTION_NOT_FLOOR_IMPROVING']+=1;return None
        oq=max(0.0,float(q)-rq); full_floor=self._candidate_alone_floor(side,p,q); overflow_risk=max(0.0,repair_floor-full_floor)
        credit=self._available_expand_risk_credit()
        if overflow_risk>credit+EPS:
            if full_floor>=before-EPS:
                self.selfFund['SELF_FUNDED_COMPOSITE_ALLOW']+=1
            else:
                self.splitBlocks['OVERFLOW_MONETARY_CREDIT_INSUFFICIENT']+=1;return None
        sp={'repairQty':rq,'overflowQty':oq,'overflowRisk':overflow_risk,'repairOnlyFloor':repair_floor,'fullFloor':full_floor,'debt':debt,'reservedRepairBefore':reserved,'availableDebtBefore':available}
        # Preserve V8.2 dynamic-quota correctness: overflow-bearing Repair cannot coexist with another live Repair reservation.
        if oq>EPS and reserved>EPS:
            self.splitBlocks['OVERFLOW_REPAIR_WAITS_OTHER_REPAIR_TERMINAL']+=1;return None
        return sp
    def run_d4(self,winner):
        r=super().run_v88(winner);r['selfFund']=dict(self.selfFund);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d4_self_fund_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            c=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=c.run_v88(cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            s=SelfFundedRepairOverflow(tape,4)
            try:r=s.run_d4(cr['winner'])
            finally:s.close()
            rows.append({'marketId':mid,'cell':'MS4_D4_SELF_FUNDED_REPAIR_OVERFLOW','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d4Sub':r['submits'],'r1Fill':r0['fillEvents'],'d4Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d4Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d4Floor':r['floor'],'selfFund':r['selfFund'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D4_SELF_FUNDED_REPAIR_OVERFLOW'}
        cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor']} for m in mids]
        out={'version':'MS4_D4_SELF_FUNDED_REPAIR_OVERFLOW_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['Repair-first allocation unchanged','aggregate Repair reservation <= authoritative debt','overflow identity/accounting exact','overflow may self-fund only when full composite Floor >= pre-submit realized Floor','otherwise existing realized monetary credit still required','overflow-bearing Repair exclusivity retained','no new hard safety','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
