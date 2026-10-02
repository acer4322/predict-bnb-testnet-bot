from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2
EPS=1e-9

class SingleCoreLivenessActiveCausalSim(r28.FanoutRoleCapacitySim):
    """R2.44 research-only single Core-derived Active intervention.

    Frozen CAP1 ignores Core terminal zero-fill evidence. Here we manually feed
    genuine ECONOMIC_CORE terminal zero-fill evidence into the existing Active
    drain only until ONE Core-derived Active submit actually materializes. After
    that, all further Core evidence is suppressed; SATELLITE_REPAIR evidence is
    unchanged CAP1. This estimates one intervention plus normal downstream CAP1.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.coreSeen=set();self.coreActiveMaterialized=False
        self.r244=Counter();self.r244Events=[];self.coreIntervention=None

    def _has_pending_core(self):
        return any(bool(x.get('r244CoreEvidence')) for x in self.pendingFailure)

    def _refresh_slots(self,t:int):
        if not self.coreActiveMaterialized and not self._has_pending_core():
            for sid,key in list(self.slot_key.items()):
                if key in self.coreSeen:continue
                if self.key_role.get(key)!='ECONOMIC_CORE':continue
                o=self.orders.get(key)
                if not o:continue
                try:s=self.snap(o)
                except Exception:s={}
                status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                if status not in v2.TERMINAL_STATUSES:continue
                self.coreSeen.add(key)
                if cum>EPS or bool(o.get('cancelRequested')):
                    self.r244['CORE_TERMINAL_NOT_GENUINE']+=1;continue
                gen=int(self.key_scope_gen.get(key,-1));epoch=(gen,int(self.scopeRepairProgressClocks))
                ev={'t':int(t),'event':'PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL','sourceKey':key,'sourceRole':'ECONOMIC_CORE',
                    'generation':gen,'repairProgressClock':epoch[1],'side':str(o['side']),'sourcePrice':float(o['price']),
                    'terminalStatus':status,'r244CoreEvidence':True}
                self.pendingFailure.append(ev);self.drainEvents.append(ev);self.slot_history.append(ev)
                self.r244['CORE_EVIDENCE_INJECTED']+=1
                x={'t':int(t),'event':'R244_CORE_EVIDENCE_INJECTED','sourceKey':key,'generation':gen,
                   'repairProgressClock':epoch[1],'side':str(o['side']),'sourcePrice':float(o['price'])}
                self.r244Events.append(x);self.slot_history.append(x)
                break
        super()._refresh_slots(t)

    def _strict_past_snapshot(self,t,head):
        qv=r28.r1.v2.base.quotes(self.book)
        upmid=None;imb=None
        if qv:
            try:upmid=(float(qv['UP']['bid'])+float(qv['UP']['ask']))/2.0
            except Exception:upmid=None
            imb=float(qv.get('imb') or 0.0)
        return {'decisionT':int(t),'sourceKey':head.get('sourceKey'),'sourcePrice':head.get('sourcePrice'),
                'sourceRole':head.get('sourceRole'),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
                'repairProgressClock':int(self.scopeRepairProgressClocks),'scopeDebtQty':float(self._scope_debt_qty()),
                'physicalFloor':float(self._physical_floor()),'physicalBest':float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost)),
                'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'cost':float(self.cost),
                'riskCreditTotal':float(self.scopeRiskCreditTotal),'riskCreditConsumed':float(self.scopeRiskCreditConsumed),
                'riskCreditReserved':float(self._reserved_current_expand_risk()),'bookImbalance':imb,'upMid':upmid,
                'roleFillsSoFar':dict(self.role_fills),'roleFillQtySoFar':{k:float(v) for k,v in self.role_fill_qty.items()},
                'liveRoles':[{'key':k,'role':role,'side':o['side'],'price':float(o['price']),'remaining':float(self._remaining(k))}
                             for _,k,o,role in self._live_role_rows()]}

    def _try_active_drain(self,t:int):
        head=self.pendingFailure[0] if self.pendingFailure else None
        was_core=bool(head and head.get('r244CoreEvidence')) and not self.coreActiveMaterialized
        pre=self._strict_past_snapshot(t,head) if was_core else None
        before_sub=int(self.activeStats.get('SUBMIT',0));before_events=len(self.drainEvents)
        result=super()._try_active_drain(t)
        after_sub=int(self.activeStats.get('SUBMIT',0))
        if was_core and after_sub>before_sub:
            self.coreActiveMaterialized=True;self.r244['CORE_ACTIVE_MATERIALIZED']+=1
            active=None
            for e in reversed(self.drainEvents[before_events:]):
                if e.get('event')=='FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT':active=e;break
            rec={**(pre or {}),'event':'R244_CORE_ACTIVE_MATERIALIZED'}
            if active:
                rec.update({'activeSubmitT':int(active['t']),'activePrice':float(active['activePrice']),'activeQty':float(active['qty']),
                            'activeDebt':float(active.get('debt') or 0.0),'activeReservedBefore':float(active.get('reservedBefore') or 0.0),
                            'candidateFloor':float(active.get('candidateFloor') or 0.0)})
            self.coreIntervention=rec;self.r244Events.append(rec);self.slot_history.append(rec)
        return result

    def run_r244(self,winner):
        r=super().run_cap(winner);r['r244Stats']=dict(self.r244);r['r244Events']=self.r244Events[:1200]
        r['r244CoreIntervention']=self.coreIntervention;r['r244CoreActiveMaterialized']=bool(self.coreActiveMaterialized)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r244_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            bsim=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=bsim.run_cap(cr['winner'])
            finally:bsim.close()
            csim=SingleCoreLivenessActiveCausalSim(tape,1,4)
            try:c=csim.run_r244(cr['winner'])
            finally:csim.close()
            rows.extend([{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                         {'marketId':mid,'cell':'MS4_R244_SINGLE_CORE_ACTIVE','winnerPostHocOnly':cr['winner'],**c}])
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'coreEvidenceInjected':int(c.get('r244Stats',{}).get('CORE_EVIDENCE_INJECTED',0)),
               'coreActiveMaterialized':bool(c.get('r244CoreActiveMaterialized')),
               'coreActiveIntervention':c.get('r244CoreIntervention'),
               'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            cmp.append(d);print(json.dumps({k:v for k,v in d.items() if k!='coreActiveIntervention'},ensure_ascii=False),flush=True)
        cand={r['marketId']:r for r in rows if r['cell']=='MS4_R244_SINGLE_CORE_ACTIVE'}
        out={'version':'MS4_R2_44_SINGLE_CORE_LIVENESS_ACTIVE_CAUSAL_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(float(cand[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cand[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
                      'atMostOneCoreActivePerMarket':all(int(cand[m].get('r244Stats',{}).get('CORE_ACTIVE_MATERIALIZED',0))<=1 for m in mids),
                      'coreInterventionExercised':any(bool(cand[m].get('r244CoreActiveMaterialized')) for m in mids)},
             'boundary':['CAP1 frozen except one materialized Core-derived Active intervention per market','SATELLITE_REPAIR evidence unchanged','after first Core Active, later Core evidence suppressed','existing Active pricing/sizing/pure-Repair semantics frozen','no Target/winner/future runtime input','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'summary':[{k:v for k,v in x.items() if k!='coreActiveIntervention'} for x in cmp]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
