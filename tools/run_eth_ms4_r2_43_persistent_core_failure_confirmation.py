from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2
EPS=1e-9

class PersistentCoreFailureConfirmationSim(r28.FanoutRoleCapacitySim):
    """R2.43 research-only persistent Core execution-failure confirmation.

    CAP1's SATELLITE_REPAIR failure evidence is untouched. ECONOMIC_CORE is not
    globally added to sourceRoles. Instead, genuine terminal zero-fill Core
    attempts are counted per (scope generation, confirmed Repair progress clock),
    and only the second independent failure in that same epoch is promoted into
    the existing pendingFailure Active-drain path.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.coreFailureSeen=set()
        self.coreFailureCount=Counter()
        self.corePromotedEpochs=set()
        self.r243=Counter();self.r243Events=[]

    def _refresh_slots(self,t:int):
        # Observe Core terminal failures before CAP1 releases their slots.
        for sid,key in list(self.slot_key.items()):
            if key in self.coreFailureSeen:continue
            role=self.key_role.get(key)
            if role!='ECONOMIC_CORE':continue
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status not in v2.TERMINAL_STATUSES:continue
            self.coreFailureSeen.add(key)
            if cum>EPS or bool(o.get('cancelRequested')):
                self.r243['CORE_TERMINAL_NOT_GENUINE_FAILURE']+=1;continue
            gen=int(self.key_scope_gen.get(key,-1));epoch=(gen,int(self.scopeRepairProgressClocks))
            self.coreFailureCount[epoch]+=1;n=int(self.coreFailureCount[epoch])
            obs={'t':int(t),'event':'R243_CORE_GENUINE_ZERO_FILL_OBSERVED','sourceKey':key,'sourceRole':role,
                 'generation':gen,'repairProgressClock':epoch[1],'side':str(o['side']),'sourcePrice':float(o['price']),
                 'terminalStatus':status,'confirmationCount':n}
            self.r243Events.append(obs);self.slot_history.append(obs);self.r243['CORE_GENUINE_ZERO_FILL_OBSERVED']+=1
            if n>=2 and epoch not in self.corePromotedEpochs:
                ev={'t':int(t),'event':'PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL','sourceKey':key,'sourceRole':'ECONOMIC_CORE',
                    'generation':gen,'repairProgressClock':epoch[1],'side':str(o['side']),'sourcePrice':float(o['price']),
                    'terminalStatus':status,'r243ConfirmationCount':n}
                self.pendingFailure.append(ev);self.drainEvents.append(ev);self.slot_history.append(ev)
                self.corePromotedEpochs.add(epoch);self.r243['CORE_FAILURE_EPOCH_PROMOTED']+=1
                pe={'t':int(t),'event':'R243_CORE_FAILURE_EPOCH_PROMOTED','sourceKey':key,'generation':gen,
                    'repairProgressClock':epoch[1],'side':str(o['side']),'sourcePrice':float(o['price']),'confirmationCount':n}
                self.r243Events.append(pe);self.slot_history.append(pe)
        # Frozen CAP1 handling for SATELLITE_REPAIR evidence + Active drain.
        super()._refresh_slots(t)

    def run_r243(self,winner):
        r=super().run_cap(winner)
        r['r243Stats']=dict(self.r243);r['r243Events']=self.r243Events[:1600]
        r['r243CoreFailureCountByEpoch']={f'{g}:{p}':int(n) for (g,p),n in self.coreFailureCount.items()}
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r243_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            bsim=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=bsim.run_cap(cr['winner'])
            finally:bsim.close()
            csim=PersistentCoreFailureConfirmationSim(tape,1,4)
            try:c=csim.run_r243(cr['winner'])
            finally:csim.close()
            rows.extend([{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                         {'marketId':mid,'cell':'MS4_R243_PERSISTENT_CORE_FAILURE_CONFIRMATION','winnerPostHocOnly':cr['winner'],**c}])
            st=c.get('failureEvidenceActiveDrainStats',{});rs=c.get('r243Stats',{})
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'coreZeroFillObserved':int(rs.get('CORE_GENUINE_ZERO_FILL_OBSERVED',0)),'coreEpochsPromoted':int(rs.get('CORE_FAILURE_EPOCH_PROMOTED',0)),
               'activeDrainSubmits':int(st.get('ACTIVE_DRAIN_SUBMIT',0)),'residualBelowActiveMin':int(st.get('RESIDUAL_DEBT_BELOW_ACTIVE_MIN',0)),
               'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        cand={r['marketId']:r for r in rows if r['cell']=='MS4_R243_PERSISTENT_CORE_FAILURE_CONFIRMATION'}
        out={'version':'MS4_R2_43_PERSISTENT_CORE_FAILURE_CONFIRMATION_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(float(cand[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cand[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
                      'persistentCoreEvidenceExercised':any(x['coreEpochsPromoted']>0 for x in cmp)},
             'boundary':['CAP1 SATELLITE_REPAIR evidence unchanged','ECONOMIC_CORE requires two independent genuine terminal zero-fill attempts in same generation/Repair-progress epoch before promotion','existing Active drain semantics frozen after promotion','no time/price/PnL threshold','no Target/winner/future runtime input','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
