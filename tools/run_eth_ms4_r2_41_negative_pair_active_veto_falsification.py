from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,math
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r22=r28.r22
v2=r28.v2
EPS=1e-9

class NegativePairActiveVetoSim(r28.FanoutRoleCapacitySim):
    """Research-only R2.41 hard-veto falsification.

    Only failure-evidence Active Repair is changed.  When the current venue-min
    Active quantity would immediately lock a negative FIFO pair edge against the
    current unmatched scope lots, that failure-evidence epoch is consumed without
    submitting Active.  Everything else remains frozen CAP1.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r241=Counter();self.r241Events=[]

    def _expected_fifo_pair(self,repair_side,price,qty):
        if self.scopeSide is None:return None
        opp_side=self.scopeSide
        rem=float(qty);edge=0.0;paired=0.0;segments=[]
        for oq,op in list(self.un[opp_side]):
            if rem<=EPS:break
            m=min(rem,float(oq));ps=float(op)+float(price);e=(1.0-ps)*m
            edge+=e;paired+=m;rem-=m
            segments.append({'qty':m,'scopePrice':float(op),'activePrice':float(price),'pairSum':ps,'edge':e})
        return {'pairedQty':paired,'unpairedQty':max(0.0,rem),'edge':edge,
                'weightedPairSum':(1.0-edge/paired) if paired>EPS else None,'segments':segments}

    def _try_active_drain(self,t:int):
        # Only intercept a valid, ready-to-submit failure-evidence Active candidate.
        while self.pendingFailure:
            ev=self.pendingFailure[0];gen=int(ev['generation']);side=str(ev['side']);epoch=(gen,int(ev['repairProgressClock']))
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            if epoch in self.usedEpochs or self._has_live_active():
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            qv=r28.r1.v2.base.quotes(self.book)
            if not qv or qv.get(side,{}).get('ask') is None:
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            ap=float(qv[side]['ask']);q=1.0/ap if ap>EPS else math.inf
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
            if avail+EPS<q:
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,q))
            if after<=before+EPS:
                return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
            econ=self._expected_fifo_pair(side,ap,q)
            if econ and float(econ['pairedQty'])+EPS>=q and float(econ['edge'])< -EPS:
                self.pendingFailure.popleft();self.usedEpochs.add(epoch)
                self.drainStats['ACTIVE_NEGATIVE_PAIR_VETO']+=1;self.r241['ACTIVE_NEGATIVE_PAIR_VETO']+=1
                x={'t':int(t),'event':'R241_ACTIVE_NEGATIVE_PAIR_VETO','sourceKey':ev['sourceKey'],'sourceRole':ev['sourceRole'],
                   'generation':gen,'repairProgressClock':epoch[1],'side':side,'sourcePrice':float(ev.get('sourcePrice') or 0.0),
                   'activePrice':ap,'qty':q,'debt':debt,'reservedBefore':reserved,'floorBefore':before,'candidateFloor':after,
                   'expectedPairEdge':float(econ['edge']),'expectedWeightedPairSum':econ['weightedPairSum'],'segments':econ['segments']}
                self.r241Events.append(x);self.drainEvents.append(x);self.slot_history.append(x)
                return False
            return r22.FailureEvidenceActiveDrainSim._try_active_drain(self,t)
        return False

    def run_r241(self,winner):
        r=super().run_cap(winner);r['r241Stats']=dict(self.r241);r['r241Events']=self.r241Events[:1200];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r241_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            bsim=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=bsim.run_cap(cr['winner'])
            finally:bsim.close()
            csim=NegativePairActiveVetoSim(tape,1,4)
            try:c=csim.run_r241(cr['winner'])
            finally:csim.close()
            rows.extend([{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                         {'marketId':mid,'cell':'MS4_R241_NEGATIVE_PAIR_ACTIVE_VETO','winnerPostHocOnly':cr['winner'],**c}])
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],
               'bestDelta':c['best']-b['best'],'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'activeVetoes':int(c.get('r241Stats',{}).get('ACTIVE_NEGATIVE_PAIR_VETO',0)),
               'controlActiveFills':b.get('ms4R2ActiveRepairFillQty',0.0),'candidateActiveFills':c.get('ms4R2ActiveRepairFillQty',0.0),
               'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        cand={r['marketId']:r for r in rows if r['cell']=='MS4_R241_NEGATIVE_PAIR_ACTIVE_VETO'}
        correctness=all(float(cand[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cand[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids)
        out={'version':'MS4_R2_41_NEGATIVE_PAIR_ACTIVE_VETO_FALSIFICATION_V1','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':correctness,'vetoMechanismExercised':any(x['activeVetoes']>0 for x in cmp)},
             'boundary':['CAP1 frozen except failure-evidence Active Repair negative-FIFO-pair veto','veto consumes only that confirmed failure-evidence epoch','passive/normal CAP1 remains available','no fitted price/premium threshold','no Target/winner/future runtime input','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
