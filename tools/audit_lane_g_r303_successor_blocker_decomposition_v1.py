from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_r303_successor_multi_action_reachability_v1 as reach
EPS=reach.EPS

class SuccessorBlockerAudit(reach.SuccessorReachabilityAudit):
    def __init__(self,tape,mid):
        super().__init__(tape,mid)
        self.r263PreCalls=[]
        self.repairCarrierAttempts=[]
        self.successorLifecycle=[]
        self._lastLifecycleSig=None

    def _succ_ob(self):
        ob=self._obligation_current()
        if not ob:return None
        return ob if int(ob.get('generation') or -1) in self.successorGenerations else None

    def _snapshot_successor(self,t,tag):
        ob=self._succ_ob()
        if not ob:return
        gen=int(ob['generation']); live=self._live_dedicated_repair_rows(gen)
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        rows=[]
        for k,o in live:
            try:s=self.snap(o); st=str(s.get('status') or '').upper(); cum=float(s.get('cumExecQty') or o.get('cum') or 0.0); leaves=s.get('leavesQty')
            except Exception:st='';cum=float(o.get('cum') or 0.0);leaves=None
            rows.append({'key':str(k),'side':str(o.get('side')),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'cum':cum,'leaves':float(leaves) if leaves is not None else None,'status':st,'repairQuotaRemaining':float(self.keyRepairQuotaRemaining.get(k,0.0))})
        x={'t':int(t),'tag':tag,'generation':gen,'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
           'bornQty':float(ob.get('bornQty') or 0.0),'outstanding':float(ob.get('outstanding') or 0.0),'repaidQty':float(ob.get('repaidQty') or 0.0),
           'passiveRepaidQty':float(ob.get('passiveRepaidQty') or 0.0),'activeRepaidQty':float(ob.get('activeRepaidQty') or 0.0),
           'carrierSubmits':int(ob.get('carrierSubmits') or 0),'carrierFills':int(ob.get('carrierFills') or 0),'zeroFillTerminals':int(ob.get('zeroFillTerminals') or 0),
           'closeReason':ob.get('closeReason'),'liveRepairCount':len(live),'representedQuota':float(quota),'repairRows':rows,
           'slots':len(self.slot_key),'active':len(self.activeKeys),'scopeDebt':float(self._scope_debt_qty()),'thesisSide':self.intentThesisSide,'repairSide':self._repair_side()}
        sig=(x['generation'],round(x['outstanding'],10),round(x['repaidQty'],10),x['carrierSubmits'],x['carrierFills'],x['zeroFillTerminals'],x['closeReason'],tuple((r['key'],r['status'],round(r['cum'],10),round(r['repairQuotaRemaining'],10)) for r in rows),x['slots'],x['active'])
        if sig!=self._lastLifecycleSig:
            self.successorLifecycle.append(x);self._lastLifecycleSig=sig

    def _try_risk_repair_carrier(self,t):
        ob=self._succ_ob(); pre=None
        if ob:
            gen=int(ob['generation']); live=self._live_dedicated_repair_rows(gen)
            pre={'t':int(t),'generation':gen,'outstanding':float(ob.get('outstanding') or 0.0),'repaidQty':float(ob.get('repaidQty') or 0.0),
                 'liveRepairCount':len(live),'representedQuota':sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live),
                 'slots':len(self.slot_key),'active':len(self.activeKeys),'staleScope':bool(self._has_stale_scope_reservation()),'repairSide':self._repair_side()}
        n0=int(self.n);s0=int(self.submits);ok=super()._try_risk_repair_carrier(t)
        if pre is not None:
            ob2=self._succ_ob();gen=pre['generation'];live2=self._live_dedicated_repair_rows(gen)
            self.repairCarrierAttempts.append({**pre,'returned':bool(ok),'submitDelta':int(self.submits)-s0,'nDelta':int(self.n)-n0,
                'postOutstanding':float(ob2.get('outstanding') or 0.0) if ob2 else None,
                'postLiveRepairCount':len(live2),'postRepresentedQuota':sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live2),
                'postRepairKeys':[str(k) for k,_ in live2]})
        return ok

    def _try_r263(self,t,end):
        ob=self._succ_ob()
        if ob:
            pre=self._r303_pre(t,end)
            gen=int(ob['generation']);live=self._live_dedicated_repair_rows(gen)
            quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
            self.r263PreCalls.append({'t':int(t),'generation':gen,'reason':pre.get('reason'),'r303Ok':bool(pre.get('ok')),
              'outstanding':float(ob.get('outstanding') or 0.0),'repaidQty':float(ob.get('repaidQty') or 0.0),'liveRepairCount':len(live),'representedQuota':float(quota),
              'representationMargin':float(quota)-float(ob.get('outstanding') or 0.0),'slots':len(self.slot_key),'active':len(self.activeKeys),
              'scopeDebt':float(self._scope_debt_qty()),'thesisSide':self.intentThesisSide,'repairSide':self._repair_side(),
              'staleScope':bool(self._has_stale_scope_reservation()),'sameGenExpandLive':bool(self._samegen_expand_live(gen,str(ob.get('scopeSideAtBirth') or self.scopeSide)))})
        return super()._try_r263(t,end)

    def process(self,t):
        super().process(t);self._snapshot_successor(t,'POST_PROCESS')

    def _open_one_option(self,t,qv,end):
        self._snapshot_successor(t,'PRE_OPEN')
        r=super()._open_one_option(t,qv,end)
        self._snapshot_successor(t,'POST_OPEN')
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_succ_block_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=SuccessorBlockerAudit(tmp/f'{m}.json.xz',m)
            try:r=s.run_audit(co[m]['winner'])
            finally:s.close()
            hist=Counter(str(x.get('reason')) for x in s.r263PreCalls)
            at=Counter('SUBMITTED' if x['returned'] else ('LIVE_ALREADY' if x['liveRepairCount'] else ('STALE' if x['staleScope'] else ('CAPACITY' if x['slots']+x['active']>=4 else 'NO_SUBMIT'))) for x in s.repairCarrierAttempts)
            row={'marketId':m,'successorBirths':s.successorBirths,'successorGenerations':sorted(s.successorGenerations),
                 'r263PreCallCount':len(s.r263PreCalls),'r263ReasonHistogram':dict(hist),'r263PreCalls':s.r263PreCalls[:500],
                 'repairCarrierAttemptCount':len(s.repairCarrierAttempts),'repairCarrierAttemptHistogram':dict(at),'repairCarrierAttempts':s.repairCarrierAttempts[:500],
                 'successorLifecycle':s.successorLifecycle[:500],
                 'riskRepairObligations':[x for x in getattr(s,'riskRepairObligations',{}).values() if int(x.get('generation') or -1) in s.successorGenerations],
                 'r257Events':[x for x in getattr(s,'r257Events',[]) if int(x.get('generation') or -1) in s.successorGenerations],
                 'r264Events':[x for x in getattr(s,'r264Events',[]) if int(x.get('generation') or -1) in s.successorGenerations],
                 'r263Events':[x for x in getattr(s,'r263Events',[]) if int(x.get('generation') or -1) in s.successorGenerations],
                 'correct':bool(r.get('residualCorrect')),'terminalSecondary':r.get('terminal')}
            rows.append(row)
            print(json.dumps({'marketId':m,'r263ReasonHistogram':row['r263ReasonHistogram'],'repairCarrierAttemptHistogram':row['repairCarrierAttemptHistogram'],'obligations':row['riskRepairObligations'],'correct':row['correct']},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R303_SUCCESSOR_BLOCKER_DECOMPOSITION_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'rows':rows,
             'gates':{'correctnessPass':all(r['correct'] for r in rows)},
             'boundary':['V1C KEEP_SHARED_PARENT_RESIDUAL lineage only','diagnostic wrappers only; inherited behavior unchanged','all blocker states strict-past at natural manager calls','no fixed seconds/windows/rank/age action gate','winner/terminal excluded from blocker labels','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
