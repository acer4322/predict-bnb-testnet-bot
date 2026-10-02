from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter,deque
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_1_fillability_active_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r21',_STAGED);r21=importlib.util.module_from_spec(sp);sp.loader.exec_module(r21)
else:
    import tools.run_eth_ms4_r2_1_fillability_active_repair as r21
r1=r21.r1;EPS=1e-9;REPAIR={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class FailureEvidenceActiveDrainSim(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,max_slots=4,source_roles=None):
        super().__init__(tape,{'model':None},max_slots)
        self.sourceRoles=set(source_roles or REPAIR)
        self.failedSeen=set();self.pendingFailure=deque();self.usedEpochs=set();self.drainStats=Counter();self.drainEvents=[]
    # Freeze MS4-R1 passive behavior. No fillability-driven automatic switch.
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def _refresh_slots(self,t:int):
        for sid,key in list(self.slot_key.items()):
            if key in self.failedSeen: continue
            role=self.key_role.get(key)
            if role not in self.sourceRoles: continue
            o=self.orders.get(key)
            if not o: continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status in r1.v2.TERMINAL_STATUSES:
                self.failedSeen.add(key)
                # User-directed/reanchor cancellations are not execution-failure evidence.
                if cum<=EPS and not bool(o.get('cancelRequested')):
                    gen=int(self.key_scope_gen.get(key,-1));epoch=(gen,int(self.scopeRepairProgressClocks))
                    ev={'t':int(t),'event':'PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL','sourceKey':key,'sourceRole':role,'generation':gen,'repairProgressClock':int(self.scopeRepairProgressClocks),'side':str(o['side']),'sourcePrice':float(o['price']),'terminalStatus':status}
                    self.pendingFailure.append(ev);self.drainEvents.append(ev);self.slot_history.append(ev);self.drainStats['GENUINE_ZERO_FILL_EVIDENCE']+=1
                elif cum<=EPS:
                    self.drainStats['OWN_CANCEL_ZERO_FILL_IGNORED']+=1
        super()._refresh_slots(t)
        self._try_active_drain(t)
    def _try_active_drain(self,t:int):
        while self.pendingFailure:
            ev=self.pendingFailure[0];gen=int(ev['generation']);side=str(ev['side']);epoch=(gen,int(ev['repairProgressClock']))
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
                self.pendingFailure.popleft();self.drainStats['STALE_EVIDENCE_DROP']+=1;continue
            if epoch in self.usedEpochs:
                self.pendingFailure.popleft();self.drainStats['EPOCH_ALREADY_DRAINED']+=1;continue
            if self._has_live_active(): self.drainStats['ACTIVE_ALREADY_LIVE_WAIT']+=1;return False
            qv=r1.v2.base.quotes(self.book)
            if not qv or qv.get(side,{}).get('ask') is None:self.drainStats['NO_ACTIVE_ASK_WAIT']+=1;return False
            ap=float(qv[side]['ask']);q=1.0/ap if ap>EPS else math.inf
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:self.drainStats['BAD_ACTIVE_MIN_QTY']+=1;self.pendingFailure.popleft();continue
            debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
            if avail+EPS<q:self.drainStats['RESIDUAL_DEBT_BELOW_ACTIVE_MIN']+=1;return False
            before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,q))
            if after<=before+EPS:self.drainStats['ACTIVE_NOT_FLOOR_IMPROVING']+=1;self.pendingFailure.popleft();continue
            if self._submit_active(t,side,'SATELLITE_REPAIR',q,0.0,{'rank':None,'depth':None}):
                self.usedEpochs.add(epoch);self.pendingFailure.popleft();self.drainStats['ACTIVE_DRAIN_SUBMIT']+=1
                x={'t':int(t),'event':'FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT','sourceKey':ev['sourceKey'],'sourceRole':ev['sourceRole'],'generation':gen,'repairProgressClock':epoch[1],'side':side,'activePrice':ap,'qty':q,'debt':debt,'reservedBefore':reserved,'floorBefore':before,'candidateFloor':after}
                self.drainEvents.append(x);self.slot_history.append(x);return True
            self.drainStats['ACTIVE_SUBMIT_BLOCKED']+=1;return False
        return False
    def run_r22(self,winner):
        r=super().run_r21(winner);r['failureEvidenceActiveDrainStats']=dict(self.drainStats);r['failureEvidenceActiveDrainEvents']=self.drainEvents[:800];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);ap.add_argument('--source-roles',default='ECONOMIC_CORE,SATELLITE_REPAIR');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r22_failactive_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            sim=FailureEvidenceActiveDrainSim(tape,4,[x for x in a.source_roles.split(',') if x])
            try:r=sim.run_r22(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R2_2_FAILURE_EVIDENCE_ACTIVE_DRAIN','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'r1Sub':r0['submits'],'r22Sub':r['submits'],'r1Fill':r0['fillEvents'],'r22Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'r22Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'r22Floor':r['floor'],'activeQty':r['ms4R2ActiveRepairFillQty'],'drain':r['failureEvidenceActiveDrainStats'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R2_2_FAILURE_EVIDENCE_ACTIVE_DRAIN'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeFillQty':n[m]['ms4R2ActiveRepairFillQty'],'activeDrainSubmits':n[m]['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0)})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);live=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_R2_2_FAILURE_EVIDENCE_ACTIVE_DRAIN_SMOKE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':live},'boundary':['frozen MS4-R1 passive behavior','only genuine terminal zero-fill Passive Repair with cancelRequested=false creates Active evidence','one Active drain per (scope generation, confirmed Repair progress clock)','Active is pure Repair venue-min at current ask','no Overflow/new debt/new risk authority','passive Core can continue on residual debt','Active does not consume passive price slot','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
