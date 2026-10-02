from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9

class PostRepaidThesisReturnSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.r295=Counter();self.r295Events=[];self._r295FavorableContext=False
        super().__init__(tape,fanout_limit,max_slots)

    def _try_favorable_replenishment(self,t,end):
        self._r295FavorableContext=True
        try:
            return super()._try_favorable_replenishment(t,end)
        finally:
            self._r295FavorableContext=False

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if role=='SATELLITE_EXPAND' and not self._r295FavorableContext and self.intentThesisSide in ('UP','DOWN') and str(side)!=str(self.intentThesisSide):
            ob=self.riskRepairObligations.get(int(self.scopeGeneration))
            if ob is not None and str(ob.get('closeReason'))=='REPAID':
                self.r295['BLOCK_POST_REPAID_OFF_THESIS_ORDINARY_EXPAND']+=1
                ev={'t':int(t),'event':'R295_POST_REPAID_OFF_THESIS_EXPAND_BLOCK','side':str(side),'thesisSide':self.intentThesisSide,'price':float(p),'qty':float(q),'scopeSide':self.scopeSide,'generation':int(self.scopeGeneration),'obligationClosedAt':ob.get('closedAt'),'obligationBornQty':float(ob.get('bornQty') or 0.0),'obligationRepaidQty':float(ob.get('repaidQty') or 0.0)}
                self.r295Events.append(ev);self.slot_history.append(ev)
                return False
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
            ev={'t':int(t),'event':'R295_INTENT_THESIS_BIRTH','side':self.intentThesisSide,'sourceKey':self.intentThesisSourceKey,'source':'FIRST_SUCCESSFUL_PROBE_CORE_SUBMIT'}
            self.r295Events.append(ev);self.slot_history.append(ev);self.r295['THESIS_BIRTH']+=1
        return ok

    def run_r295(self,winner):
        r=super().run_r264(winner)
        correct=(bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices',0))<=self.max_slots)
        r.update({'r295Version':'MS4_R2_95_POST_REPAID_THESIS_RETURN_V1','r295Stats':dict(self.r295),'r295Events':self.r295Events[:5000],'r295IntentThesisSide':self.intentThesisSide,'r295IntentThesisBornAt':self.intentThesisBornAt,'r295IntentThesisSourceKey':self.intentThesisSourceKey,'r295CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r295_'))
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
            s=PostRepaidThesisReturnSim(tape,1,4)
            try:r=s.run_r295(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R295_POST_REPAID_THESIS_RETURN','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r295IntentThesisSide'),'thesisWinnerAligned':r.get('r295IntentThesisSide')==w,'postRepaidBlocks':int(r.get('r295Stats',{}).get('BLOCK_POST_REPAID_OFF_THESIS_ORDINARY_EXPAND',0)),'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'riskFills':int(r.get('riskTrancheFillEvents',0)),'r263Fills':int(r.get('r263Fills',0)),'replenishmentFills':int(r.get('r247FavorableReplenishmentFills',0)),'correct':bool(r.get('r295CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        noeff=[x for x in cmp if x['marketId']==1946784]
        out={'version':'MS4_R2_95_POST_REPAID_THESIS_RETURN_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(x['correct'] for x in cmp),'postRepaidBlockExercised':any(x['postRepaidBlocks']>0 for x in cmp),'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp),'noEffect1946784':all(abs(x[k])<=1e-9 for x in noeff for k in ['pnlDelta','bestDelta','floorDelta','gapDelta','fillDelta','submitDelta']) if noeff else None},'boundary':['first successful PROBE_CORE submit births persistent intent thesis','PRE_OBLIGATION/RISK_SEED/OBLIGATION_LIVE exposure remains allowed','only ordinary off-thesis SATELLITE_EXPAND after same-generation explicit obligation closeReason=REPAID is blocked','R2.47 favorable replenishment explicitly exempt','Repair/Active/SCOPE_BIRTH frozen','no winner/Target/future runtime input','no new numeric threshold','max4','<=180s unchanged','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
