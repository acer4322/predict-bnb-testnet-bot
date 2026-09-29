from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
 sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
 import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r22=r28.r26.r22;r1=r28.r1;v2=r1.v2;EPS=1e-9

class ReanchorActiveHandoffSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.reanchorHandoffKeys=set();self.reanchorHandoffSeen=set();self.r214=Counter();self.r214events=[]
    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid));role=self.key_role.get(key) if key else None
        ok=super()._request_cancel(t,sid,reason)
        if ok and reason=='SATELLITE_FRONTIER_REANCHOR' and key and role=='SATELLITE_REPAIR' and self.key_scope_gen.get(key)==self.scopeGeneration:
            self.reanchorHandoffKeys.add(str(key));self.r214['REANCHOR_HANDOFF_INTENT']+=1
            ev={'t':int(t),'event':'REANCHOR_ACTIVE_HANDOFF_INTENT','key':str(key),'generation':int(self.scopeGeneration),'side':str(self.orders[key]['side']),'price':float(self.orders[key]['price'])};self.r214events.append(ev);self.slot_history.append(ev)
        return ok
    def _refresh_slots(self,t:int):
        # Before parent refresh drops terminal own-cancel evidence, capture reanchor handoff terminals.
        for sid,key in list(self.slot_key.items()):
            sk=str(key)
            if sk not in self.reanchorHandoffKeys or sk in self.reanchorHandoffSeen:continue
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status in v2.TERMINAL_STATUSES:
                self.reanchorHandoffSeen.add(sk)
                if cum<=EPS:
                    gen=int(self.key_scope_gen.get(key,-1));epoch=(gen,int(self.scopeRepairProgressClocks));ev={'t':int(t),'event':'PASSIVE_REPAIR_REANCHOR_ZERO_FILL_TERMINAL','sourceKey':sk,'sourceRole':'SATELLITE_REPAIR','generation':gen,'repairProgressClock':int(self.scopeRepairProgressClocks),'side':str(o['side']),'sourcePrice':float(o['price']),'terminalStatus':status}
                    self.pendingFailure.append(ev);self.drainEvents.append(ev);self.slot_history.append(ev);self.r214['REANCHOR_ZERO_FILL_EVIDENCE']+=1
                else:self.r214['REANCHOR_LATE_FILL_NO_ACTIVE']+=1
        super()._refresh_slots(t)
    def run_r214(self,w):
        r=super().run_cap(w);r['r214Stats']=dict(self.r214);r['r214Events']=self.r214events[:1000];return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 tmp=Path(tempfile.mkdtemp(prefix='ms4_r214_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
   try:r0=ctl.run_cap(cr['winner'])
   finally:ctl.close()
   sim=ReanchorActiveHandoffSim(tape,4)
   try:r=sim.run_r214(cr['winner'])
   finally:sim.close()
   rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R214_REANCHOR_ACTIVE_HANDOFF','winnerPostHocOnly':cr['winner'],**r}]
   print(json.dumps({'marketId':mid,'ctl':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'active':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'cand':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'r214':r['r214Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R214_REANCHOR_ACTIVE_HANDOFF'};cmp=[]
  for m in mids:
   b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'activeDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'r214Stats':c['r214Stats']})
  correct=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids);anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
  out={'version':'MS4_R2_14_REANCHOR_ACTIVE_HANDOFF_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 max4/fanoutLimit1 frozen','existing SATELLITE_FRONTIER_REANCHOR cancel is reused as execution-deterioration evidence','only terminal-confirmed zero-fill reanchor carrier may create early Active handoff evidence','cancel-pending reservation retained; late fill cancels handoff','same R2.2 one Active per scope/progress epoch','no fixed seconds/ticks/pair threshold','no new debt/overflow/risk authority','realistic HFT','no dream fill','no 8781']}
  op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
