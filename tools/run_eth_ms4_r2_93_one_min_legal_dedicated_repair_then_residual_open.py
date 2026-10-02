from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9

class OneMinLegalDedicatedRepairThenResidualOpenSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r293=Counter();self.r293Events=[];self.r293DedicatedFilledGeneration=set()
    def _try_risk_repair_carrier(self,t):
        ob=self._obligation_current()
        if ob is not None:
            gen=int(ob['generation'])
            if gen in self.r293DedicatedFilledGeneration:
                self.r293['BLOCK_AFTER_FIRST_DEDICATED_FILL']+=1
                return False
        return super()._try_risk_repair_carrier(t)
    def process(self,t):
        s0=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[s0:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0);rq=float(ev.get('repairAllocated') or 0.0)
            if key not in self.riskRepairCarrierKeys or inc<=EPS or rq<=EPS:continue
            gen=int(ev.get('generationAtSubmit') or -1)
            if gen in self.r293DedicatedFilledGeneration:continue
            self.r293DedicatedFilledGeneration.add(gen);self.r293['FIRST_DEDICATED_REPAIR_FILL']+=1
            ob=self.riskRepairObligations.get(gen)
            self.r293Events.append({'t':int(t),'event':'R293_FIRST_DEDICATED_MIN_LEGAL_REPAIR_FILL','generation':gen,'key':key,'price':float(ev.get('price') or 0.0),'fillQty':inc,'repairAllocated':rq,'residualObligationAfter':float(ob.get('outstanding') or 0.0) if ob else None,'physicalFloor':float(self._physical_floor())})
    def run_r293(self,winner):
        r=super().run_r264(winner)
        correct=(bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices',0))<=self.max_slots)
        r.update({'r293Version':'MS4_R2_93_ONE_MIN_LEGAL_DEDICATED_REPAIR_THEN_RESIDUAL_OPEN_V1','r293Stats':dict(self.r293),'r293Events':self.r293Events[:3000],'r293DedicatedFilledGenerations':sorted(self.r293DedicatedFilledGeneration),'r293CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r293_'))
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
            s=OneMinLegalDedicatedRepairThenResidualOpenSim(tape,1,4)
            try:r=s.run_r293(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R293_ONE_MIN_LEGAL_DEDICATED_REPAIR_THEN_RESIDUAL_OPEN','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'firstDedicatedFills':int(r.get('r293Stats',{}).get('FIRST_DEDICATED_REPAIR_FILL',0)),'residualDedicatedBlocks':int(r.get('r293Stats',{}).get('BLOCK_AFTER_FIRST_DEDICATED_FILL',0)),'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'passiveRiskRepaidQty':float(r.get('riskRepairPassiveRepaidQty',0.0)),'activeRiskRepaidQty':float(r.get('riskRepairActiveRepaidQty',0.0)),'riskDebtOutstanding':float(r.get('riskDebtOutstanding',0.0)),'correct':bool(r.get('r293CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_93_ONE_MIN_LEGAL_DEDICATED_REPAIR_THEN_RESIDUAL_OPEN_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(x['correct'] for x in cmp),'firstDedicatedFillExercised':any(x['firstDedicatedFills']>0 for x in cmp),'residualOpenExercised':any(x['residualDedicatedBlocks']>0 for x in cmp),'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp)},'boundary':['exact R264 risk/Expand authority','dedicated R257 passive Repair may retry until first confirmed dedicated Repair fill','after first dedicated fill residual obligation stays explicit but no further dedicated carrier is born in that generation','ordinary R2.47 Repair and all inherited Active remain frozen','no Floor/pair/time/count threshold','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
