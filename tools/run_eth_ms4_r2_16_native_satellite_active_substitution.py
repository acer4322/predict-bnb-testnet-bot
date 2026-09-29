from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,joblib
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
 sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
 import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1;v2=r1.v2;EPS=1e-9

class NativeSatelliteActiveSubstitutionSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,model_bundle,max_slots=4):
        super().__init__(tape,1,max_slots);self.execModel=model_bundle;self.inFanout=False;self.r216=Counter();self.r216events=[]
    def _parallel_repair_fill(self,t:int,side:str):
        self.inFanout=True
        try:return super()._parallel_repair_fill(t,side)
        finally:self.inFanout=False
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if (not self.inFanout) and role=='SATELLITE_REPAIR' and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q):
            try:score,diag=self._score(side,role,p,q,split)
            except Exception:
                self.r216['SCORE_EXCEPTION']+=1;return super()._submit_role_v8(t,side,role,p,q,proj,split)
            self.r216['NATIVE_SATELLITE_SCORED']+=1
            if score<0.5 and not self._has_live_active():
                self.r216['LOW_FILLABILITY_NATIVE_SIGNAL']+=1
                if self._submit_active(t,side,role,q,score,diag):
                    self.r216['NATIVE_TO_ACTIVE_SUBMIT']+=1
                    ev={'t':int(t),'event':'NATIVE_SATELLITE_TO_ACTIVE_SUBSTITUTION','generation':int(self.scopeGeneration),'side':side,'passivePrice':float(p),'activePrice':float(self.orders[next(reversed(self.orders))]['price']) if self.orders else None,'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'pairSumPassive':diag.get('pairSum')};self.r216events.append(ev);self.slot_history.append(ev);return True
                self.r216['ACTIVE_SUBSTITUTION_BLOCKED_FALLBACK_PASSIVE']+=1
            else:
                if score>=0.5:self.r216['KEEP_NATIVE_PASSIVE_GOOD_FILLABILITY']+=1
                else:self.r216['KEEP_NATIVE_PASSIVE_ACTIVE_ALREADY_LIVE']+=1
        return super()._submit_role_v8(t,side,role,p,q,proj,split)
    def run_r216(self,w):
        r=super().run_cap(w);r['r216Stats']=dict(self.r216);r['r216Events']=self.r216events[:1200];return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model)
 tmp=Path(tempfile.mkdtemp(prefix='ms4_r216_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
   try:r0=ctl.run_cap(cr['winner'])
   finally:ctl.close()
   sim=NativeSatelliteActiveSubstitutionSim(tape,model,4)
   try:r=sim.run_r216(cr['winner'])
   finally:sim.close()
   rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R216_NATIVE_SATELLITE_ACTIVE_SUBSTITUTION','winnerPostHocOnly':cr['winner'],**r}]
   print(json.dumps({'marketId':mid,'ctl':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'activeDrain':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'cand':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'activeDrain':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'r216':r['r216Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R216_NATIVE_SATELLITE_ACTIVE_SUBSTITUTION'};cmp=[]
  for m in mids:
   b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'nativeActiveSubmits':c['r216Stats'].get('NATIVE_TO_ACTIVE_SUBMIT',0),'terminalActiveDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)})
  correct=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids);anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
  out={'version':'MS4_R2_16_NATIVE_SATELLITE_ACTIVE_SUBSTITUTION_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 fanout remains Passive and fanoutLimit=1','Economic Core/Expand unchanged','only native pure SATELLITE_REPAIR candidate may substitute its already-authorized tranche to Active when frozen strict-past fillability classifier says LOW_FILLABILITY','same candidate Repair quantity; zero Overflow/new debt/new risk authority','if Active substitution cannot submit, original Passive candidate is submitted','one live Active per scope remains','0.5 is frozen classifier boundary, not Target/PnL-tuned safety threshold','realistic HFT','no dream fill','no 8781']}
  op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
