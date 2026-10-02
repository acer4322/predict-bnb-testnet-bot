from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
EPS=1e-9

class HandoffRepairCreditQuarantineSim(r239.OverflowResponsibilityHandoffSim):
    """R2.40 research-only ablation.

    Physical R2.39 handoff fills remain authoritative. The only change is that
    Repair credit minted by a handoff key is removed from spendable
    scopeRiskCreditTotal after the fill clock, so repayment cannot immediately
    recycle into new Expand/overflow authority.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r240Events=[]
        self.quarantinedHandoffRepairCredit=0.0

    def process(self,t):
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration)
        before_n=len(self.splitEvents)
        super().process(t)
        new_events=self.splitEvents[before_n:]
        qcredit=0.0
        details=[]
        for ev in new_events:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.handoffKeys:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            price=float(ev.get('price') or 0.0)
            qcredit += rq*(1.0-price)
            details.append({'key':key,'repairQty':rq,'price':price,'credit':rq*(1.0-price)})
        if qcredit>EPS and self.scopeSide==old_scope and int(self.scopeGeneration)==old_gen:
            before=float(self.scopeRiskCreditTotal)
            removed=min(before,qcredit)
            self.scopeRiskCreditTotal=max(0.0,before-removed)
            self.quarantinedHandoffRepairCredit+=removed
            ev={'t':int(t),'event':'R240_HANDOFF_REPAIR_CREDIT_QUARANTINED','scopeSide':self.scopeSide,
                'generation':int(self.scopeGeneration),'creditObserved':qcredit,'creditRemoved':removed,
                'riskCreditTotalBefore':before,'riskCreditTotalAfter':float(self.scopeRiskCreditTotal),
                'details':details}
            self.r240Events.append(ev);self.slot_history.append(ev)

    def run_r240(self,winner):
        r=super().run_r239(winner)
        r['r240Events']=self.r240Events[:1000]
        r['r240QuarantinedHandoffRepairCredit']=float(self.quarantinedHandoffRepairCredit)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r240_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            base=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=base.run_cap(cr['winner'])
            finally:base.close()
            s39=r239.OverflowResponsibilityHandoffSim(tape,1,4)
            try:a39=s39.run_r239(cr['winner'])
            finally:s39.close()
            s40=HandoffRepairCreditQuarantineSim(tape,1,4)
            try:a40=s40.run_r240(cr['winner'])
            finally:s40.close()
            rows.extend([
                {'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                {'marketId':mid,'cell':'MS4_R239_HANDOFF','winnerPostHocOnly':cr['winner'],**a39},
                {'marketId':mid,'cell':'MS4_R240_HANDOFF_CREDIT_QUARANTINE','winnerPostHocOnly':cr['winner'],**a40},
            ])
            d={'marketId':mid,
               'r239VsCap1Pnl':a39['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],
               'r240VsCap1Pnl':a40['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],
               'r240VsR239Pnl':a40['pnlDiagnosticOnly']-a39['pnlDiagnosticOnly'],
               'r239VsCap1Floor':a39['floor']-b['floor'],
               'r240VsCap1Floor':a40['floor']-b['floor'],
               'r240VsR239Floor':a40['floor']-a39['floor'],
               'r239Fills':a39['fillEvents'],'r240Fills':a40['fillEvents'],
               'r239HandoffFills':a39['r239HandoffFills'],'r240HandoffFills':a40['r239HandoffFills'],
               'quarantinedCredit':a40['r240QuarantinedHandoffRepairCredit'],
               'unauthorizedOverflowQty':a40.get('unauthorizedOverflowQty',0.0),
               'repairQuotaExcessMax':a40.get('repairQuotaExcessMax',0.0)}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        cand={r['marketId']:r for r in rows if r['cell']=='MS4_R240_HANDOFF_CREDIT_QUARANTINE'}
        correctness=all(float(cand[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cand[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids)
        out={'version':'MS4_R2_40_HANDOFF_REPAIR_CREDIT_QUARANTINE_V1','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':correctness,'quarantineExercised':any(float(cand[m].get('r240QuarantinedHandoffRepairCredit',0.0))>EPS for m in mids)},
             'boundary':['R2.39 physical handoff semantics frozen','only handoff-generated Repair credit is quarantined from spendable scopeRiskCreditTotal','no new gate/slot/credit','winner post-hoc only','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
