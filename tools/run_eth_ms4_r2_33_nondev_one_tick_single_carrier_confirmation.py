from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2;EPS=1e-9
TARGETS=[(1946036,'DOWN_10'),(1946640,'UP_25')]

class SingleCarrierPersistenceSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,target_key,max_slots=4):
        super().__init__(tape,1,max_slots);self.targetKey=str(target_key);self.activated=False;self.keepClocks=0;self.events=[]
    def _live_same_gen_opp_core(self,side):
        opp='DOWN' if str(side)=='UP' else 'UP';rows=[]
        for sid,key,o,role in self._live_role_rows(role='ECONOMIC_CORE'):
            if int(self.key_scope_gen.get(key,-1))==int(self.scopeGeneration) and str(o.get('side'))==opp:rows.append((sid,key,o,role))
        return rows[-1] if rows else None
    def _reanchor_stale(self,t:int):
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'):continue
            role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=v2.kprice(o['price']);levels=[v2.kprice(x) for x in self._live_price_levels(side)]
            if role=='ECONOMIC_CORE':
                if p in levels and self._pair_ok(side,p):self.corePreservedClocks+=1;continue
                self._request_cancel(t,sid,'CORE_INVALIDATED');continue
            if p not in levels:
                if role=='SATELLITE_EXPAND' and key==self.targetKey and int(self.key_scope_gen.get(key,-1))==int(self.scopeGeneration):
                    nearest=min((abs(float(p)-float(x)) for x in levels),default=None);core=self._live_same_gen_opp_core(side)
                    pair=(float(p)+float(core[2]['price'])) if core else None
                    can_keep=core is not None and pair is not None and pair<=1.0+EPS
                    if not self.activated:
                        can_keep=can_keep and nearest is not None and abs(float(nearest)-0.01)<=1e-9
                        if can_keep:
                            self.activated=True
                            self.events.append({'t':int(t),'event':'SINGLE_CARRIER_PERSISTENCE_ACTIVATE','key':key,'side':side,'expandPrice':float(p),'corePrice':float(core[2]['price']),'pairSum':float(pair),'nearestLevelDistance':float(nearest)})
                    if self.activated and can_keep:
                        self.keepClocks+=1
                        if len(self.events)<500:self.events.append({'t':int(t),'event':'SINGLE_CARRIER_PERSISTENCE_KEEP','key':key,'pairSum':float(pair)})
                        continue
                if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'):self.reanchors+=1
    def run_single(self,w):
        r=super().run_cap(w);o=self.orders.get(self.targetKey)
        try:s=dict(self.snap(o)) if o else {}
        except Exception:s={}
        r['r231']={'targetKey':self.targetKey,'activated':bool(self.activated),'keepClocks':int(self.keepClocks),'events':self.events,'targetFinalStatus':s.get('status'),'targetFinalCum':float(o.get('cum') or 0.0) if o else 0.0}
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='ms4_r233_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};controls={};rows=[];cmp=[]
        for mid in sorted(set(m for m,_ in TARGETS)):
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:controls[mid]=ctl.run_cap(cr['winner'])
            finally:ctl.close()
        for mid,key in TARGETS:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=controls[mid];sim=SingleCarrierPersistenceSim(tape,key,4)
            try:c=sim.run_single(cr['winner'])
            finally:sim.close()
            row={'marketId':mid,'targetKey':key,'winnerPostHocOnly':cr['winner'],'control':{'submits':b['submits'],'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best'],'roleSubmits':b.get('roleSubmits'),'roleFills':b.get('roleFills'),'scopeBirths':b.get('scopeBirths'),'scopeFlips':b.get('scopeFlips')},'candidate':{'submits':c['submits'],'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'roleSubmits':c.get('roleSubmits'),'roleFills':c.get('roleFills'),'scopeBirths':c.get('scopeBirths'),'scopeFlips':c.get('scopeFlips'),'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty'),'repairQuotaExcessMax':c.get('repairQuotaExcessMax'),'r231':c['r231']}}
            row['delta']={'submits':c['submits']-b['submits'],'fills':c['fillEvents']-b['fillEvents'],'pnl':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floor':c['floor']-b['floor'],'best':c['best']-b['best']}
            rows.append(row);cmp.append({'marketId':mid,'targetKey':key,'activated':c['r231']['activated'],**row['delta']})
            print(json.dumps(cmp[-1],ensure_ascii=False),flush=True)
        correct=all(float(r['candidate']['unauthorizedOverflowQty'] or 0)<=EPS and float(r['candidate']['repairQuotaExcessMax'] or 0)<=EPS for r in rows)
        exercised=all(bool(r['candidate']['r231']['activated']) for r in rows)
        out={'version':'MS4_R2_33_NONDEV_ONE_TICK_SINGLE_CARRIER_CONFIRMATION_V1','researchOnly':True,'runtimeAuthority':False,'targets':[{'marketId':m,'key':k} for m,k in TARGETS],'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'allTargetsExercised':exercised},'boundary':['one target carrier intervention per replay','CAP1 otherwise frozen','no new risk capacity','ordinary credit accounting unchanged','winner post-hoc only','no Target/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
