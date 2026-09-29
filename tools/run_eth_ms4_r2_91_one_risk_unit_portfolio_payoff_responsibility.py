from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9; BUDGET_FLOOR=-1.0; REPAIR_ROLES=r264.r257.REPAIR_ROLES

class OneRiskUnitPortfolioPayoffResponsibilitySim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r291=Counter();self.r291Events=[]
    def _budget_satisfied(self):
        return float(self._physical_floor())>=BUDGET_FLOOR-EPS
    def _repair_split(self,side,p,q):
        # This only changes Repair responsibility completion. Risk/Expand admission remains parent R2.64.
        if self.scopeSide is not None and self._budget_satisfied():
            self.r291['BLOCK_NEW_REPAIR_BUDGET_SATISFIED']+=1
            return None
        return super()._repair_split(side,p,q)
    def _try_active_drain(self,t:int):
        # Active remains available whenever the portfolio downside responsibility is unpaid.
        if self.scopeSide is not None and self._budget_satisfied():
            self.r291['BLOCK_NEW_ACTIVE_BUDGET_SATISFIED']+=1
            return False
        return super()._try_active_drain(t)
    def _cancel_excess_live_passive_repair(self,t:int):
        if self.scopeSide is None or not self._budget_satisfied():return 0
        n=0
        for sid,key,o,role in list(self._live_role_rows()):
            if role not in REPAIR_ROLES:continue
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            if bool(o.get('cancelRequested')):continue
            if self._request_cancel(int(t),int(sid),'R291_PAYOFF_RESPONSIBILITY_SATISFIED'):
                n+=1;self.r291['CANCEL_LIVE_REPAIR_BUDGET_SATISFIED']+=1
                self.r291Events.append({'t':int(t),'event':'R291_CANCEL_LIVE_REPAIR_BUDGET_SATISFIED','key':key,'role':role,'generation':int(self.scopeGeneration),'floor':float(self._physical_floor())})
        return n
    def process(self,t):
        before=float(self._physical_floor());split0=len(self.splitEvents)
        super().process(t)
        after=float(self._physical_floor())
        newrep=sum(float(x.get('repairAllocated') or 0.0) for x in self.splitEvents[split0:] if x.get('event')=='ROLE_FILL_SPLIT')
        if before<BUDGET_FLOOR-EPS and after>=BUDGET_FLOOR-EPS:
            self.r291['BUDGET_RECOVERY_CROSSING']+=1
            self.r291Events.append({'t':int(t),'event':'R291_PORTFOLIO_BUDGET_RECOVERED','floorBefore':before,'floorAfter':after,'repairAllocatedThisClock':newrep,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide})
        if self.scopeSide is not None and after>=BUDGET_FLOOR-EPS:
            self._cancel_excess_live_passive_repair(t)
    def run_r291(self,winner):
        r=super().run_r264(winner)
        correct=(bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices',0))<=self.max_slots)
        r.update({'r291Version':'MS4_R2_91_ONE_RISK_UNIT_PORTFOLIO_PAYOFF_RESPONSIBILITY_V1','r291Stats':dict(self.r291),'r291Events':self.r291Events[:3000],'r291CorrectnessPass':bool(correct),'r291BudgetFloor':BUDGET_FLOOR})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r291_'))
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
            s=OneRiskUnitPortfolioPayoffResponsibilitySim(tape,1,4)
            try:r=s.run_r291(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R291_ONE_RISK_UNIT_PORTFOLIO_PAYOFF_RESPONSIBILITY','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'budgetCrossings':int(r.get('r291Stats',{}).get('BUDGET_RECOVERY_CROSSING',0)),'repairBlocks':int(r.get('r291Stats',{}).get('BLOCK_NEW_REPAIR_BUDGET_SATISFIED',0)),'activeBlocks':int(r.get('r291Stats',{}).get('BLOCK_NEW_ACTIVE_BUDGET_SATISFIED',0)),'repairCancels':int(r.get('r291Stats',{}).get('CANCEL_LIVE_REPAIR_BUDGET_SATISFIED',0)),'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'passiveRiskRepaidQty':float(r.get('riskRepairPassiveRepaidQty',0.0)),'activeRiskRepaidQty':float(r.get('riskRepairActiveRepaidQty',0.0)),'correct':bool(r.get('r291CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_91_ONE_RISK_UNIT_PORTFOLIO_PAYOFF_RESPONSIBILITY_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(x['correct'] for x in cmp),'mechanismExercised':any(x['repairBlocks']>0 or x['repairCancels']>0 or x['activeBlocks']>0 for x in cmp),'activeStillExercisedWhereNeeded':any(x['activeFillQty']>EPS for x in cmp)},'boundary':['exact R264 risk/Expand admission','one quote-unit portfolio downside budget is fixed causal probe only; no sweep','new Repair/Active only blocked while physical Floor already within budget','if later risk worsens Floor below budget inherited Passive/Active service resumes','live Passive Repair cancel requested after budget recovery; late fills remain accounting truth','Active retained as bounded backstop while downside unpaid','no pair gate','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
