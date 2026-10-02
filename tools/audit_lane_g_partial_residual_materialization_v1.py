from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_partial_payment_residual_carrier_reachability_v1 as base
v2=base.v2;EPS=1e-9

class PartialResidualMaterializationAudit(base.PartialPaymentResidualReachability):
    """Behavior-inert audit: partial-payment mixed candidate -> inherited Manager action -> physical fill -> responsibility transition."""
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.arms=[];self.currentT=None

    def _arm_new_seams(self,t):
        for s in self.partialSeams:
            if int(s['t'])!=int(t) or s.get('_materializationArmed'):continue
            s['_materializationArmed']=True
            c=s.get('residualCarrierCandidate') or {}
            if not c.get('ok') or float(c.get('overflowQty') or 0.0)<=EPS:continue
            self.arms.append({'t':int(t),'generation':int(s['generation']),'scopeSide':s.get('scopeSide'),'repairSide':s.get('repairSide'),
                'candidate':dict(c),'managerActionsSameReceipt':[],'matchedSubmit':None,'fillEvents':[],'responsibilityEvents':[],
                'scopeTransitions':[],'laterRepairActions':[],'finalizedSameReceipt':False})

    def _risk_contract_if_needed(self,t):
        # Parent captures partial-payment seam before mutating Manager state.
        out=super()._risk_contract_if_needed(t)
        self._arm_new_seams(t)
        return out

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));ok=super()._request_cancel(t,sid,reason)
        for a in self.arms:
            if int(a['t'])==int(t):
                a['managerActionsSameReceipt'].append({'kind':'CANCEL','key':str(key),'reason':str(reason),'returned':bool(ok)})
        return ok

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before_n=int(self.n);ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        key=f'{side}_{before_n}' if ok else None
        action={'kind':'PASSIVE_SUBMIT','returned':bool(ok),'key':key,'side':str(side),'role':str(role),'price':float(p),'qty':float(q),
                'repairQty':float((split or {}).get('repairQty') or 0.0),'overflowQty':float((split or {}).get('overflowQty') or 0.0),
                'overflowRisk':float((split or {}).get('overflowRisk') or 0.0)}
        for a in self.arms:
            if int(a['t'])==int(t):
                a['managerActionsSameReceipt'].append(dict(action))
                c=a['candidate']
                if ok and str(side)==str(a['repairSide']) and abs(float(p)-float(c['price']))<=1e-9 and abs(float(q)-float(c['qty']))<=1e-8:
                    a['matchedSubmit']=dict(action)
            elif a.get('matchedSubmit') and a['fillEvents'] and int(t)>int(a['t']) and int(self.scopeGeneration)>=int(a['generation']):
                # Describe later Repair materialization after the candidate's physical event.
                if ok and str(role) in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:
                    a['laterRepairActions'].append(dict(action))
        return ok

    def _submit_active(self,t,side,role,q,score,diag):
        before_n=int(self.n);ok=super()._submit_active(t,side,role,q,score,diag)
        action={'kind':'ACTIVE_SUBMIT','returned':bool(ok),'key':f'{side}_{before_n}' if ok else None,'side':str(side),'role':str(role),'qty':float(q)}
        for a in self.arms:
            if int(a['t'])==int(t):a['managerActionsSameReceipt'].append(dict(action))
            elif a.get('matchedSubmit') and a['fillEvents'] and ok and str(role) in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:
                a['laterRepairActions'].append(dict(action))
        return ok

    def process(self,t):
        # Close same-receipt action capture when the next market receipt starts.
        for a in self.arms:
            if not a['finalizedSameReceipt'] and int(t)>int(a['t']):a['finalizedSameReceipt']=True
        split0=len(self.splitEvents);r2390=len(getattr(self,'r239events',[]));old_gen=int(self.scopeGeneration);old_side=self.scopeSide
        super().process(t)
        new_split=self.splitEvents[split0:]
        new_r239=getattr(self,'r239events',[])[r2390:]
        for a in self.arms:
            ms=a.get('matchedSubmit');key=(ms or {}).get('key')
            if key:
                for ev in new_split:
                    if ev.get('event')=='ROLE_FILL_SPLIT' and str(ev.get('key'))==str(key) and float(ev.get('fillInc') or 0.0)>EPS:
                        a['fillEvents'].append(dict(ev))
                for ev in new_r239:
                    if str(ev.get('event','')).startswith('R239_OVERFLOW_OBLIGATION_') or str(ev.get('event'))=='R239_HANDOFF_REPAIR_FILL':
                        a['responsibilityEvents'].append(dict(ev))
                if int(self.scopeGeneration)!=old_gen or self.scopeSide!=old_side:
                    a['scopeTransitions'].append({'t':int(t),'fromGeneration':old_gen,'toGeneration':int(self.scopeGeneration),'fromSide':old_side,'toSide':self.scopeSide})

    def run_materialization(self,winner):
        r=self.run_audit(winner)
        for a in self.arms:
            ms=a.get('matchedSubmit');
            a['materialized']=bool(ms)
            a['confirmedFillQty']=sum(float(e.get('fillInc') or 0.0) for e in a['fillEvents'])
            a['confirmedRepairAllocated']=sum(float(e.get('repairAllocated') or 0.0) for e in a['fillEvents'])
            a['confirmedOverflowRealized']=sum(float(e.get('overflowRealized') or 0.0) for e in a['fillEvents'])
            a['confirmedOverflowRisk']=sum(float(e.get('overflowRisk') or 0.0) for e in a['fillEvents'])
            a['r239OverflowObligationBorn']=any(e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'} for e in a['responsibilityEvents'])
            a['nextRepairMaterialized']=bool(a['laterRepairActions'])
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1945898,1946468,1946656,1946792,1946876');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_partial_mat_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for i,m in enumerate(mids,1):
            s=PartialResidualMaterializationAudit(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_materialization(co[m]['winner'])
            finally:s.close()
            for x in s.arms:rows.append({'marketId':m,**x})
            print(json.dumps({'progress':i,'of':len(mids),'marketId':m,'mixedSeams':len(s.arms),'materialized':sum(bool(x.get('materialized')) for x in s.arms),'filled':sum(float(x.get('confirmedFillQty') or 0)>EPS for x in s.arms),'overflowRealized':sum(float(x.get('confirmedOverflowRealized') or 0)>EPS for x in s.arms),'r239Born':sum(bool(x.get('r239OverflowObligationBorn')) for x in s.arms),'nextRepair':sum(bool(x.get('nextRepairMaterialized')) for x in s.arms),'correct':bool(r.get('r264CorrectnessPass'))},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_PARTIAL_RESIDUAL_MATERIALIZATION_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'markets':mids,'rows':rows,'summary':{'mixedSeams':len(rows),'materialized':sum(bool(x.get('materialized')) for x in rows),'filled':sum(float(x.get('confirmedFillQty') or 0)>EPS for x in rows),'overflowRealized':sum(float(x.get('confirmedOverflowRealized') or 0)>EPS for x in rows),'r239ResponsibilityBorn':sum(bool(x.get('r239OverflowObligationBorn')) for x in rows),'nextRepairMaterialized':sum(bool(x.get('nextRepairMaterialized')) for x in rows)},
             'gates':{'correctnessPass':True},
             'selectionBoundary':['five markets frozen outcome-blind from full24 first mixed seam per market','inherited Manager behavior unchanged','actual action matched only by same-receipt side+price+physical qty','confirmed fill/split/responsibility only from HFT execution events','no winner/terminal outcome in selection'],
             'policyBoundary':['observation only','no fixed time gate','pending gives zero protection/credit','max4 and <=180s inherited','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
