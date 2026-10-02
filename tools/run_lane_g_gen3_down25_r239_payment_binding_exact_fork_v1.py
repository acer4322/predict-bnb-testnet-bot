from __future__ import annotations
import argparse,json,os,tempfile,zipfile,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,near

T=1788534963270; KEY='DOWN_25'; OWNER_ID=2; EXPECTED=0.666773270050439; EPS=1e-9
BRANCHES=('BASELINE_UNBOUND_R239','BIND_CONFIRMED_NATIVE_REPAIR_TO_OWNER2')

def snap_owner(s):
    o=next((x for x in getattr(s,'obligations',[]) if int(x.get('id') or -1)==OWNER_ID),None)
    return None if o is None else dict(o)

class Gen3Down25BindingFork(R239ConfirmedPaymentBindingFork):
    def __init__(self,tape,mid,branch):
        super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP')
        self.gen3Branch=branch; self.gen3BindTrigger=None; self.bindEvent=None; self.bindErrors=[]
    def process(self,t):
        before=len(getattr(self,'splitEvents',[]) or [])
        owner_before=snap_owner(self) if int(t)==T else None
        super().process(t)
        if int(t)!=T or self.gen3BindTrigger is not None:return
        new=(getattr(self,'splitEvents',[]) or [])[before:]
        ev=next((e for e in new if e.get('event')=='ROLE_FILL_SPLIT' and str(e.get('key'))==KEY and e.get('role')=='ECONOMIC_CORE' and str(e.get('side'))=='DOWN'),None)
        if not ev:return
        ob=next((x for x in getattr(self,'obligations',[]) if int(x.get('id') or -1)==OWNER_ID),None)
        self.gen3BindTrigger={'t':int(t),'split':dict(ev),'ownerBefore':owner_before,'ownerAfterNativeProcess':None if ob is None else dict(ob),'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration)}
        if not owner_before or int(owner_before.get('generation') or -1)!=3 or str(owner_before.get('side'))!='UP':self.bindErrors.append('OWNER2_IDENTITY_MISMATCH')
        if not near(float((owner_before or {}).get('outstanding') or 0.0),EXPECTED,1e-12):self.bindErrors.append('OWNER2_OUTSTANDING_MISMATCH')
        rq=float(ev.get('repairAllocated') or 0.0); ov=float(ev.get('overflowRealized') or 0.0)
        if rq+1e-12<EXPECTED:self.bindErrors.append('REPAIR_INSUFFICIENT')
        if abs(ov)>1e-12:self.bindErrors.append('UNEXPECTED_OVERFLOW')
        if self.gen3Branch=='BIND_CONFIRMED_NATIVE_REPAIR_TO_OWNER2' and not self.bindErrors and ob is not None:
            before_q=float(ob.get('outstanding') or 0.0); paid=min(before_q,rq)
            ob['outstanding']=max(0.0,before_q-paid); ob['repaidQty']=float(ob.get('repaidQty') or 0.0)+paid; ob['passiveRepaidQty']=float(ob.get('passiveRepaidQty') or 0.0)+paid
            self.r239['NATIVE_CORE_REPAIR_PAYMENT_BIND']+=1; self.r239['NATIVE_CORE_REPAIR_PAYMENT_QTY_MILLI']+=int(round(paid*1000))
            x={'t':int(t),'event':'R239_NATIVE_CORE_REPAIR_PAYMENT_BIND','obligationId':OWNER_ID,'key':KEY,'repairQty':rq,'liabilityPaidQty':paid,'repairBeyondOwner':max(0.0,rq-paid),'outstanding':float(ob['outstanding']),'confirmedOverflow':ov,'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x);self.bindEvent=x
            if ob['outstanding']<=EPS and self.activeObligationId==OWNER_ID:self._close_active(t,'REPAID')
    def run_branch(self,winner):
        raw=self.run_fork(winner)
        births=[dict(e) for e in getattr(self,'r239events',[]) if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'}]
        return {'marketId':MID,'branch':self.gen3Branch,'trigger':self.gen3BindTrigger,'bindEvent':self.bindEvent,'bindErrors':self.bindErrors,'owner2Final':snap_owner(self),'birthEvents':births,'terminal':raw.get('terminal'),'upstreamCorrect':raw.get('correct'),'nativeUnauthorizedOverflowQty':raw.get('nativeUnauthorizedOverflowQty'),'nativeRepairQuotaExcessMax':raw.get('nativeRepairQuotaExcessMax'),'combinedAuthorityExcessMax':raw.get('combinedAuthorityExcessMax')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_down25_bind_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}; tape=tmp/f'{MID}.json.xz';tape.write_bytes(z.read(f'tapes/{MID}.json.xz'))
        rows=[]
        for b in BRANCHES:
            s=Gen3Down25BindingFork(tape,MID,b)
            try:r=s.run_branch(co[MID]['winner'])
            finally:s.close()
            rows.append(r);print(json.dumps({'branch':b,'trigger':r['trigger'],'bindEvent':r['bindEvent'],'owner2Final':r['owner2Final'],'terminal':r['terminal'],'errors':r['bindErrors']},ensure_ascii=False),flush=True)
        base,cand=rows; bt,ct=base['terminal'],cand['terminal']; physical={k:ct[k]-bt[k] for k in ['submits','fills','pnl','floor','best']}
        btr,ctr=base['trigger'],cand['trigger']; c2=cand['owner2Final']; b2=base['owner2Final']
        gates={
          'exactTriggerParity':bool(btr and ctr and btr['t']==T and ctr['t']==T and str(btr['split'].get('key'))==KEY and str(ctr['split'].get('key'))==KEY),
          'baselineUnbound':bool(base['bindEvent'] is None and b2 and near(float(b2.get('repaidQty') or 0.0),0.0,1e-12)),
          'candidateOwner2RepaidExact':bool(c2 and near(float(c2.get('repaidQty') or 0.0),EXPECTED,1e-12) and near(float(c2.get('outstanding') or 0.0),0.0,1e-12) and c2.get('closeReason')=='REPAID'),
          'noFalseSuccessorBirth':len(cand['birthEvents'])==len(base['birthEvents'])==2,
          'noPhysicalDelta':all(abs(float(v))<=1e-12 for v in physical.values()),
          'noAuthorityExcess':float(cand.get('combinedAuthorityExcessMax') or 0.0)<=EPS and float(cand.get('nativeUnauthorizedOverflowQty') or 0.0)<=EPS,
          'noRepairQuotaExcess':float(cand.get('nativeRepairQuotaExcessMax') or 0.0)<=EPS,
          'noBindingErrors':not cand['bindErrors'],
          'upstreamCorrect':bool(base['upstreamCorrect'] and cand['upstreamCorrect'])}
        gates['correctnessAllPass']=all(gates.values())
        out={'version':'LANE_G_GEN3_DOWN25_R239_PAYMENT_BINDING_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'physicalDeltaCandidateMinusBaseline':physical,'gates':gates,'verdict':'PASS_GEN3_PAYMENT_BINDING_NO_PHYSICAL_DELTA' if gates['correctnessAllPass'] else 'FAIL_GEN3_PAYMENT_BINDING_EXACT_FORK','boundary':['single consumed market 1946468','single DOWN_25 confirmed receipt','bookkeeping attribution only','no new order/authority/credit','R269 Repair-first unchanged','fresh untouched','no dream fill','no 8781','no time/rank-age gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':out['verdict'],'gates':gates,'physicalDelta':physical},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
