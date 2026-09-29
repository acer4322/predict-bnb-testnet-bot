from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_63_single_pre_repair_reexpand_causal as r263
r257=r263.r257; v2=r263.v2; EPS=1e-9

class ExecutionRepresentedPreRepairReexpandSim(r263.SinglePreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r264=Counter(); self.r264Events=[]

    def _try_r263(self,t,end):
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if gen in self.r263GenerationUsed:return False
        if float(ob.get('repaidQty') or 0.0)>EPS:return False
        live=self._live_dedicated_repair_rows(gen)
        if not live:return False
        quota=0.0; detail=[]
        for key,o in live:
            rem=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
            quota+=rem
            detail.append({'key':key,'price':float(o['price']),'qty':float(o.get('qty') or 0.0),'repairQuotaRemaining':rem})
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        represented=quota+EPS>=outstanding
        ev={'t':int(t),'event':'R264_CURRENT_OBLIGATION_EXECUTION_REPRESENTATION','generation':gen,
            'outstanding':outstanding,'liveRepairQuota':quota,'fullyRepresented':bool(represented),'liveRepairCarriers':detail}
        # Log only the first decisive state per generation to keep telemetry bounded.
        mark=('R264REP',gen)
        if mark not in getattr(self,'_r264Marks',set()):
            if not hasattr(self,'_r264Marks'):self._r264Marks=set()
            self._r264Marks.add(mark);self.r264Events.append(ev);self.slot_history.append(ev)
        if not represented:
            self.r264['BLOCK_CURRENT_OBLIGATION_NOT_FULLY_REPRESENTED']+=1
            return False
        self.r264['ALLOW_CURRENT_OBLIGATION_FULLY_REPRESENTED']+=1
        before=int(self.r263.get('SUBMIT',0))
        ok=super()._try_r263(t,end)
        if ok and int(self.r263.get('SUBMIT',0))>before:
            self.r264['SUBMIT']+=1
            self.r264Events.append({'t':int(t),'event':'R264_EXECUTION_REPRESENTED_REEXPAND_SUBMIT',
                'generation':gen,'outstandingBefore':outstanding,'liveRepairQuotaAtDecision':quota,
                'representationMargin':quota-outstanding})
        return ok

    def run_r264(self,winner):
        r=super().run_r263(winner)
        r.update({'r264Version':'MS4_R2_64_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND_V1',
                  'r264Stats':dict(self.r264),'r264Events':self.r264Events[:3000],
                  'r264Submits':int(self.r264.get('SUBMIT',0)),
                  'r264CorrectnessPass':bool(r.get('r263CorrectnessPass'))})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r264_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:c=sim.run_r264(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'r264Submits':c['r264Submits'],'r263Fills':c['r263Fills'],'r263Qty':c['r263FilledQty'],
               'representedAllows':int(c.get('r264Stats',{}).get('ALLOW_CURRENT_OBLIGATION_FULLY_REPRESENTED',0)),
               'representationBlocks':int(c.get('r264Stats',{}).get('BLOCK_CURRENT_OBLIGATION_NOT_FULLY_REPRESENTED',0)),
               'riskFills':c['riskTrancheFillEvents'],'obligationsBorn':c['riskRepairObligationsBorn'],
               'obligationsRepaid':c['riskRepairObligationsRepaid'],'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
               'correct':bool(c['r264CorrectnessPass']),'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0)),
               'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_64_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'r264SubmitExercised':any(x['r264Submits']>0 for x in cmp),
                      'r264FillExercised':any(x['r263Fills']>0 for x in cmp)},
             'boundary':['exact R257 control','R263 one-child-per-generation retained','additional qualification only: live dedicated passive Repair quota fully represents current pre-existing risk obligation','live Repair does not offset new risk or mint credit','confirmed R264 fill enlarges explicit obligation','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
