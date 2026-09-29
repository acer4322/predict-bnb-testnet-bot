from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math,joblib
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

class EarlyResidualActiveSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,model_bundle,max_slots=4):
        super().__init__(tape,1,max_slots);self.execModel=model_bundle;self.r215=Counter();self.r215events=[]
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        # Preserve CAP1 passive behavior first.
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if not ok:return False
        if role!='SATELLITE_REPAIR' or not isinstance(split,dict):return True
        if float(split.get('overflowQty',0.0))>EPS or float(split.get('repairQty',0.0))+EPS<float(q):return True
        try:score,diag=self._score(side,role,p,q,split)
        except Exception:
            self.r215['SCORE_EXCEPTION']+=1;return True
        self.r215['SCORED_PASSIVE_REPAIR']+=1
        if score>=0.5:
            self.r215['KEEP_PASSIVE_GOOD_FILLABILITY']+=1;return True
        self.r215['LOW_FILLABILITY_SIGNAL']+=1
        if self._has_live_active():self.r215['ACTIVE_ALREADY_LIVE']+=1;return True
        qv=v2.base.quotes(self.book)
        if not qv or qv.get(side,{}).get('ask') is None:self.r215['NO_ACTIVE_ASK']+=1;return True
        ap=float(qv[side]['ask']);aq=1.0/ap if ap>EPS else math.inf
        if not math.isfinite(aq) or aq<=EPS or aq>12.0+EPS:self.r215['BAD_ACTIVE_MIN_QTY']+=1;return True
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));residual=max(0.0,debt-reserved)
        if residual+EPS<aq:
            self.r215['NO_UNRESERVED_RESIDUAL_FOR_EARLY_ACTIVE']+=1;return True
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,aq))
        if after<=before+EPS:self.r215['EARLY_ACTIVE_NOT_FLOOR_IMPROVING']+=1;return True
        if self._submit_active(t,side,'SATELLITE_REPAIR',aq,score,diag):
            self.r215['EARLY_ACTIVE_SUBMIT']+=1
            ev={'t':int(t),'event':'EARLY_RESIDUAL_ACTIVE_SUBMIT','generation':int(self.scopeGeneration),'side':side,'passivePrice':float(p),'activePrice':ap,'passiveFillability':float(score),'activeQty':aq,'debt':debt,'reservedPassiveBeforeActive':reserved,'residualBeforeActive':residual,'pairSumPassive':diag.get('pairSum')};self.r215events.append(ev);self.slot_history.append(ev)
        else:self.r215['EARLY_ACTIVE_SUBMIT_BLOCKED']+=1
        return True
    def run_r215(self,w):
        r=super().run_cap(w);r['r215Stats']=dict(self.r215);r['r215Events']=self.r215events[:1200];return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model)
 tmp=Path(tempfile.mkdtemp(prefix='ms4_r215_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
   try:r0=ctl.run_cap(cr['winner'])
   finally:ctl.close()
   sim=EarlyResidualActiveSim(tape,model,4)
   try:r=sim.run_r215(cr['winner'])
   finally:sim.close()
   rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R215_EARLY_RESIDUAL_ACTIVE','winnerPostHocOnly':cr['winner'],**r}]
   print(json.dumps({'marketId':mid,'ctl':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'active':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'cand':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'activeDrain':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'early':r['r215Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R215_EARLY_RESIDUAL_ACTIVE'};cmp=[]
  for m in mids:
   b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'earlyActiveSubmits':c['r215Stats'].get('EARLY_ACTIVE_SUBMIT',0),'terminalActiveDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)})
  correct=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids);anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
  out={'version':'MS4_R2_15_EARLY_RESIDUAL_ACTIVE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 passive option is always submitted first and remains live','existing strict-past fillability classifier only identifies LOW_FILLABILITY passive Repair','early Active may use only truly unreserved residual authoritative Repair debt after all passive reservations','same venue-min Active quantity and candidate-alone Floor improvement as R2.2','no Passive replacement/cancel caused by this feature','no new debt/overflow/Expand credit','0.5 is frozen classifier decision boundary, not Target/PnL tuned safety threshold','realistic HFT','no dream fill','no 8781']}
  op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
