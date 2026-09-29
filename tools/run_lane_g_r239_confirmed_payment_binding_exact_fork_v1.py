from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_owned_successor_mixed_venue_min_exact_fork_v1 import OwnedSuccessorMixedVenueMinFork,EPS,near,MID

T=1788534947089
KEY='UP_20'
EXPECTED_REPAIR=1.5071397734278218
EXPECTED_OLD=1.5071397734278216
EXPECTED_OVERFLOW=0.666773270050439
BRANCHES=('INHERITED_R239_NO_NATIVE_CORE_PAYMENT_BINDING','R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP')

def ob_copy(sim,oid):
    x=next((z for z in getattr(sim,'obligations',[]) if int(z.get('id') or -1)==int(oid)),None)
    return None if x is None else dict(x)

class R239ConfirmedPaymentBindingFork(OwnedSuccessorMixedVenueMinFork):
    def __init__(self,tape,mid,payment_branch):
        # Both branches intentionally keep the exact same upstream mixed-carrier world.
        super().__init__(tape,mid,'R269_MIXED_VENUE_MIN_PASSIVE')
        self.paymentBranch=payment_branch
        self.paymentTrigger=None
        self.paymentEvent=None
        self.paymentErrors=[]

    def _matching_native_core_receipt(self,t):
        if int(t)!=T:return None
        rows=[e for e in (getattr(self,'splitEvents',[]) or [])
              if int(e.get('t') or -1)==T and str(e.get('key'))==KEY
              and e.get('event')=='ROLE_FILL_SPLIT' and e.get('role')=='ECONOMIC_CORE'
              and str(e.get('side'))=='UP' and float(e.get('fillInc') or 0.0)>EPS]
        return rows[-1] if rows else None

    def _close_active(self,t,reason):
        ob=self._active_obligation()
        ev=self._matching_native_core_receipt(t)
        exact=bool(ev and ob and int(ob.get('id') or -1)==1 and str(ob.get('side'))=='DOWN'
                   and int(ob.get('generation') or -1)==2 and str(reason)=='SCOPE_LEFT_OVERFLOW_SIDE')
        if exact and self.paymentTrigger is None:
            self.paymentTrigger={
                't':int(t),'key':KEY,'reasonRequested':str(reason),
                'ownerBefore':dict(ob),'splitEvent':dict(ev),
                'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration),
                'submits':int(self.submits),'fills':int(getattr(self,'fillEvents',0)),
                'scopeRiskCreditTotal':float(getattr(self,'scopeRiskCreditTotal',0.0)),
                'scopeRiskCreditConsumed':float(getattr(self,'scopeRiskCreditConsumed',0.0)),
            }
            rq=float(ev.get('repairAllocated') or 0.0)
            ov=float(ev.get('overflowRealized') or 0.0)
            if not near(float(ob.get('outstanding') or 0.0),EXPECTED_OLD,1e-12):self.paymentErrors.append('OWNER_OUTSTANDING_MISMATCH')
            if not near(rq,EXPECTED_REPAIR,1e-12):self.paymentErrors.append('REPAIR_ALLOC_MISMATCH')
            if not near(ov,EXPECTED_OVERFLOW,1e-12):self.paymentErrors.append('OVERFLOW_MISMATCH')
            if self.paymentBranch=='R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP' and not self.paymentErrors:
                before=float(ob.get('outstanding') or 0.0)
                paid=min(before,rq)
                ob['outstanding']=max(0.0,before-paid)
                ob['repaidQty']=float(ob.get('repaidQty') or 0.0)+paid
                # This is confirmed passive native ECONOMIC_CORE Repair; account provenance without minting credit.
                ob['passiveRepaidQty']=float(ob.get('passiveRepaidQty') or 0.0)+paid
                self.r239['NATIVE_CORE_REPAIR_PAYMENT_BIND']+=1
                self.r239['NATIVE_CORE_REPAIR_PAYMENT_QTY_MILLI']+=int(round(paid*1000))
                x={'t':int(t),'event':'R239_NATIVE_CORE_REPAIR_PAYMENT_BIND','obligationId':1,'key':KEY,
                   'repairQty':rq,'liabilityPaidQty':paid,'outstanding':float(ob['outstanding']),
                   'confirmedOverflow':ov,'price':float(ev.get('price') or 0.0)}
                self.r239events.append(x);self.slot_history.append(x);self.paymentEvent=x
                if ob['outstanding']<=EPS:
                    return super()._close_active(t,'REPAID')
        return super()._close_active(t,reason)

    def run_payment(self,winner):
        raw=self.run_fork(winner)
        o1=ob_copy(self,1)
        births=[e for e in (getattr(self,'r239events',[]) or []) if e.get('event')=='R239_OVERFLOW_OBLIGATION_BORN']
        o2=next((x for x in getattr(self,'obligations',[]) if int(x.get('id') or -1)==2),None)
        return {
            'marketId':MID,'branch':self.paymentBranch,'paymentTrigger':self.paymentTrigger,
            'paymentEvent':self.paymentEvent,'paymentErrors':self.paymentErrors,'owner1Final':o1,
            'owner2Final':None if o2 is None else dict(o2),'birthEvents':births,
            'upstreamMixed':{'branchAttempted':raw.get('branchAttempted'),'mixedKey':raw.get('mixedKey'),
                             'submitDelta':raw.get('submitDelta'),'correct':raw.get('correct'),'errors':raw.get('errors')},
            'terminal':raw.get('terminal'),'nativeUnauthorizedOverflowQty':raw.get('nativeUnauthorizedOverflowQty'),
            'nativeRepairQuotaExcessMax':raw.get('nativeRepairQuotaExcessMax'),
            'combinedAuthorityExcessMax':raw.get('combinedAuthorityExcessMax'),
            'scopeRiskCreditTotal':float(getattr(self,'scopeRiskCreditTotal',0.0)),
            'scopeRiskCreditConsumed':float(getattr(self,'scopeRiskCreditConsumed',0.0)),
        }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_r239_payment_bind_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            (tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
        rows=[]
        for b in BRANCHES:
            s=R239ConfirmedPaymentBindingFork(tmp/f'{MID}.json.xz',MID,b)
            try:r=s.run_payment(co[MID]['winner'])
            finally:s.close()
            rows.append(r)
            print(json.dumps({'branch':b,'trigger':r['paymentTrigger'],'paymentEvent':r['paymentEvent'],
                              'owner1Final':r['owner1Final'],'owner2Final':r['owner2Final'],
                              'terminal':r['terminal'],'paymentErrors':r['paymentErrors']},ensure_ascii=False),flush=True)
        base,cand=rows
        bt,ct=base['terminal'],cand['terminal']
        physical={k:(ct[k]-bt[k]) for k in ['submits','fills','pnl','floor','best']}
        triggerParity=bool(base['paymentTrigger'] and cand['paymentTrigger'] and
                           base['paymentTrigger']['t']==cand['paymentTrigger']['t'] and
                           base['paymentTrigger']['key']==cand['paymentTrigger']['key'] and
                           near(float(base['paymentTrigger']['splitEvent'].get('repairAllocated') or 0.0),EXPECTED_REPAIR,1e-12) and
                           near(float(cand['paymentTrigger']['splitEvent'].get('repairAllocated') or 0.0),EXPECTED_REPAIR,1e-12))
        b1,c1=base['owner1Final'],cand['owner1Final'];c2=cand['owner2Final'];b2=base['owner2Final']
        gates={
            'exactTriggerParity':triggerParity,
            'baselineBindingAbsent':base['paymentEvent'] is None and near(float((b1 or {}).get('repaidQty') or 0.0),0.0,1e-12),
            'candidateOwner1RepaidExact':bool(c1 and near(float(c1.get('repaidQty') or 0.0),EXPECTED_OLD,1e-12) and near(float(c1.get('outstanding') or 0.0),0.0,1e-12) and c1.get('closeReason')=='REPAID'),
            'candidateOwner2ConfirmedExcessOnly':bool(c2 and int(c2.get('generation') or -1)==3 and str(c2.get('side'))=='UP' and near(float(c2.get('originOverflowQty') or 0.0),EXPECTED_OVERFLOW,1e-12)),
            'successorParity':bool(b2 and c2 and near(float(b2.get('originOverflowQty') or 0.0),float(c2.get('originOverflowQty') or 0.0),1e-12)),
            'noPhysicalDelta':all(abs(float(v))<=1e-12 for v in physical.values()),
            'noAuthorityExcess':float(cand.get('combinedAuthorityExcessMax') or 0.0)<=EPS and float(cand.get('nativeUnauthorizedOverflowQty') or 0.0)<=EPS,
            'noRepairQuotaExcess':float(cand.get('nativeRepairQuotaExcessMax') or 0.0)<=EPS,
            'noPaymentErrors':not cand['paymentErrors'],
            'upstreamCorrectnessPass':bool(base['upstreamMixed']['correct'] and cand['upstreamMixed']['correct']),
        }
        gates['correctnessAllPass']=all(gates.values())
        out={'version':'LANE_G_R239_CONFIRMED_PAYMENT_BINDING_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,
             'rows':rows,'physicalDeltaCandidateMinusBaseline':physical,'gates':gates,
             'verdict':'PASS_PAYMENT_BINDING_NO_PHYSICAL_DELTA' if gates['correctnessAllPass'] else 'FAIL_PAYMENT_BINDING_EXACT_FORK',
             'boundary':['single consumed market 1946468','single confirmed UP_20 structural receipt','same upstream mixed-carrier realistic-HFT world in both branches','bookkeeping attribution only','no new order/fill/authority/credit/capacity','R269 Repair-first unchanged','fresh untouched','no dream fill','no 8781','no fixed-time/rank-age Manager gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'physicalDelta':physical,'verdict':out['verdict']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
