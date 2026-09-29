from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9; BUDGET_FLOOR=-1.0

class DownsideWeightedDedicatedRepairDemotionSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r292=Counter();self.r292Events=[]
    def _budget_satisfied(self):return float(self._physical_floor())>=BUDGET_FLOOR-EPS
    def _try_risk_repair_carrier(self,t):
        # Only the extra R2.57 dedicated lane is demoted. Ordinary R2.47 Repair and all Active remain frozen.
        if self.scopeSide is not None and self._budget_satisfied():
            self.r292['BLOCK_DEDICATED_REPAIR_BUDGET_SATISFIED']+=1;return False
        return super()._try_risk_repair_carrier(t)
    def _cancel_live_dedicated(self,t):
        if self.scopeSide is None or not self._budget_satisfied():return 0
        n=0
        for sid,key,o,role in list(self._live_role_rows()):
            if key not in self.riskRepairCarrierKeys:continue
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            if bool(o.get('cancelRequested')):continue
            if self._request_cancel(int(t),int(sid),'R292_DEDICATED_REPAIR_DEMOTED_WITHIN_BUDGET'):
                n+=1;self.r292['CANCEL_DEDICATED_REPAIR_BUDGET_SATISFIED']+=1
                self.r292Events.append({'t':int(t),'event':'R292_CANCEL_DEDICATED_REPAIR_BUDGET_SATISFIED','key':key,'role':role,'generation':int(self.scopeGeneration),'floor':float(self._physical_floor())})
        return n
    def process(self,t):
        before=float(self._physical_floor())
        super().process(t)
        after=float(self._physical_floor())
        if before<BUDGET_FLOOR-EPS and after>=BUDGET_FLOOR-EPS:
            self.r292['BUDGET_RECOVERY_CROSSING']+=1
            self.r292Events.append({'t':int(t),'event':'R292_BUDGET_RECOVERY_CROSSING','floorBefore':before,'floorAfter':after,'scopeSide':self.scopeSide,'generation':int(self.scopeGeneration)})
        self._cancel_live_dedicated(t)
    def run_r292(self,winner):
        r=super().run_r264(winner)
        correct=(bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices',0))<=self.max_slots)
        r.update({'r292Version':'MS4_R2_92_DOWNSIDE_WEIGHTED_DEDICATED_REPAIR_DEMOTION_V1','r292Stats':dict(self.r292),'r292Events':self.r292Events[:3000],'r292CorrectnessPass':bool(correct),'r292BudgetFloor':BUDGET_FLOOR})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r292_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            s=DownsideWeightedDedicatedRepairDemotionSim(tape,1,4)
            try:r=s.run_r292(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R292_DOWNSIDE_WEIGHTED_DEDICATED_REPAIR_DEMOTION','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'budgetCrossings':int(r.get('r292Stats',{}).get('BUDGET_RECOVERY_CROSSING',0)),'dedicatedBlocks':int(r.get('r292Stats',{}).get('BLOCK_DEDICATED_REPAIR_BUDGET_SATISFIED',0)),'dedicatedCancels':int(r.get('r292Stats',{}).get('CANCEL_DEDICATED_REPAIR_BUDGET_SATISFIED',0)),'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'passiveRiskRepaidQty':float(r.get('riskRepairPassiveRepaidQty',0.0)),'activeRiskRepaidQty':float(r.get('riskRepairActiveRepaidQty',0.0)),'correct':bool(r.get('r292CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_92_DOWNSIDE_WEIGHTED_DEDICATED_REPAIR_DEMOTION_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(x['correct'] for x in cmp),'mechanismExercised':any(x['dedicatedBlocks']>0 or x['dedicatedCancels']>0 for x in cmp),'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp)},'boundary':['exact R264 risk/Expand admission','only R257 dedicated passive risk-repair service is demoted inside one-risk-unit downside budget','ordinary R2.47 ECONOMIC_CORE/SATELLITE_REPAIR frozen','inherited Active completely frozen and remains available','if downside deepens dedicated service immediately returns','no pair gate','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
