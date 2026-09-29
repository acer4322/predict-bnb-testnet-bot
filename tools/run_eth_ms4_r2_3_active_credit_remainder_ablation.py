from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_2_failure_evidence_active_drain.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r22',_STAGED);r22=importlib.util.module_from_spec(sp);sp.loader.exec_module(r22)
else:
    import tools.run_eth_ms4_r2_2_failure_evidence_active_drain as r22
r1=r22.r1;EPS=1e-9

class R23Sim(r22.FailureEvidenceActiveDrainSim):
    def __init__(self,tape,mode,max_slots=4):
        super().__init__(tape,max_slots,['SATELLITE_REPAIR']);self.mode=str(mode).upper();self.r23=Counter();self.quarantinedActiveCredit=0.0;self.r23events=[]
    def process(self,t):
        if self.mode!='CREDIT_QUARANTINE': return super().process(t)
        before_events=len(getattr(self,'scope_credit_events',[])); before_rem={k:float(self.keyRepairQuotaRemaining.get(k,0.0)) for k in list(self.activeKeys)}
        super().process(t)
        new_credit=sum(float(e.get('creditValue') or 0.0) for e in getattr(self,'scope_credit_events',[])[before_events:] if int(e.get('t',-1))==int(t))
        active_credit=0.0
        for k,br in before_rem.items():
            if k not in self.orders: continue
            ar=float(self.keyRepairQuotaRemaining.get(k,0.0));alloc=max(0.0,br-ar)
            if alloc>EPS: active_credit+=alloc*(1.0-float(self.orders[k]['price']))
        q=min(max(0.0,new_credit),max(0.0,active_credit))
        if q>EPS:
            old=float(self.scopeRiskCreditTotal);self.scopeRiskCreditTotal=max(float(self.scopeRiskCreditConsumed),float(self.scopeRiskCreditTotal)-q);actual=max(0.0,old-self.scopeRiskCreditTotal);self.quarantinedActiveCredit+=actual;self.r23['ACTIVE_CREDIT_QUARANTINED']+=1
            ev={'t':int(t),'event':'ACTIVE_REPAIR_CREDIT_QUARANTINED','credit':actual,'rawActiveCredit':active_credit,'newRepairCreditAtTick':new_credit,'riskCreditTotalAfter':float(self.scopeRiskCreditTotal)};self.r23events.append(ev);self.slot_history.append(ev)
    def _try_active_drain(self,t:int):
        if self.mode!='PASSIVE_REMAINDER': return super()._try_active_drain(t)
        while self.pendingFailure:
            ev=self.pendingFailure[0];gen=int(ev['generation']);side=str(ev['side']);epoch=(gen,int(ev['repairProgressClock']))
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side(): self.pendingFailure.popleft();self.drainStats['STALE_EVIDENCE_DROP']+=1;continue
            if epoch in self.usedEpochs: self.pendingFailure.popleft();self.drainStats['EPOCH_ALREADY_DRAINED']+=1;continue
            if self._has_live_active(): self.drainStats['ACTIVE_ALREADY_LIVE_WAIT']+=1;return False
            qv=r1.v2.base.quotes(self.book)
            if not qv or qv.get(side,{}).get('ask') is None:self.drainStats['NO_ACTIVE_ASK_WAIT']+=1;return False
            ap=float(qv[side]['ask']);aq=1.0/ap if ap>EPS else math.inf
            if not math.isfinite(aq) or aq<=EPS or aq>12.0+EPS:self.drainStats['BAD_ACTIVE_MIN_QTY']+=1;self.pendingFailure.popleft();continue
            debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
            if avail+EPS<aq:self.drainStats['RESIDUAL_DEBT_BELOW_ACTIVE_MIN']+=1;return False
            live_passive=any(float(self.keyRepairQuotaRemaining.get(k,0.0))>EPS for _,k,o,role in self._live_role_rows(side=side) if role in r22.REPAIR and self.key_scope_gen.get(k)==self.scopeGeneration)
            passive_q=0.0
            if not live_passive:
                cand=self._candidate_from_levels_v8(side,'SATELLITE_REPAIR',False)
                if cand is None:self.r23['NO_PASSIVE_REMAINDER_CANDIDATE']+=1;return False
                passive_q=float(cand[1])
                if avail+EPS<aq+passive_q:
                    self.r23['PASSIVE_REMAINDER_HEADROOM_BLOCK']+=1;return False
            before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,aq))
            if after<=before+EPS:self.drainStats['ACTIVE_NOT_FLOOR_IMPROVING']+=1;self.pendingFailure.popleft();continue
            if self._submit_active(t,side,'SATELLITE_REPAIR',aq,0.0,{'rank':None,'depth':None}):
                self.usedEpochs.add(epoch);self.pendingFailure.popleft();self.drainStats['ACTIVE_DRAIN_SUBMIT']+=1;self.r23['ACTIVE_WITH_LIVE_PASSIVE' if live_passive else 'ACTIVE_WITH_RESERVED_REMAINDER_HEADROOM']+=1
                x={'t':int(t),'event':'FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT','sourceKey':ev['sourceKey'],'sourceRole':ev['sourceRole'],'generation':gen,'repairProgressClock':epoch[1],'side':side,'activePrice':ap,'qty':aq,'debt':debt,'reservedBefore':reserved,'floorBefore':before,'candidateFloor':after,'livePassiveRemainder':live_passive,'passiveHeadroomQty':passive_q}
                self.drainEvents.append(x);self.slot_history.append(x);return True
            self.drainStats['ACTIVE_SUBMIT_BLOCKED']+=1;return False
        return False
    def run_r23(self,w):
        r=super().run_r22(w);r['r23Mode']=self.mode;r['r23Stats']=dict(self.r23);r['quarantinedActiveCredit']=float(self.quarantinedActiveCredit);r['r23Events']=self.r23events[:500];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--mode',choices=['CREDIT_QUARANTINE','PASSIVE_REMAINDER'],required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r23_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r22.FailureEvidenceActiveDrainSim(tape,4,['SATELLITE_REPAIR'])
            try:r0=ctl.run_r22(cr['winner'])
            finally:ctl.close()
            sim=R23Sim(tape,a.mode,4)
            try:r=sim.run_r23(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R22_SATELLITE_ONLY_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':f'MS4_R23_{a.mode}','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'mode':a.mode,'ctlF':r0['fillEvents'],'candF':r['fillEvents'],'ctlP':r0['pnlDiagnosticOnly'],'candP':r['pnlDiagnosticOnly'],'ctlFloor':r0['floor'],'candFloor':r['floor'],'activeSubs':r['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'activeQty':r['ms4R2ActiveRepairFillQty'],'r23':r['r23Stats'],'qCredit':r['quarantinedActiveCredit'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='R22_SATELLITE_ONLY_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_R23_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeSubmits':n[m]['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'quarantinedActiveCredit':n[m].get('quarantinedActiveCredit',0.0)})
        out={'version':'MS4_R2_3_ACTIVE_CREDIT_REMAINDER_ABLATION_V1','researchOnly':True,'runtimeAuthority':False,'mode':a.mode,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapse50pctPass':all(n[m]['fillEvents']>=.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['control is R2.2 SATELLITE-only failure-evidence Active drain','CREDIT_QUARANTINE removes only Active-generated Repair credit from generic Expand risk budget; passive Repair credit unchanged','PASSIVE_REMAINDER preserves an existing live passive Repair reservation or enough unreserved debt for one legal passive tranche before Active','no new responsibility/risk/quantity authority','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'mode':a.mode,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
