from __future__ import annotations
import argparse,json,os,tempfile,zipfile,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,near

T=1788534963270
KEY='DOWN_25'
OWNER_ID=2
OWNER_DEBT=0.666773270050439
EPS=1e-9

class Gen3Down25IdentityAudit(R239ConfirmedPaymentBindingFork):
    def __init__(self,tape,mid):
        super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP')
        self.preReceipt=None; self.postReceipt=None
    def _owner(self):
        return next((o for o in getattr(self,'obligations',[]) if int(o.get('id') or -1)==OWNER_ID),None)
    def _unmatched(self):
        out={}
        for side in ('UP','DOWN'):
            vals=getattr(self,'un',{}).get(side,[]) if hasattr(self,'un') else []
            out[side]=float(sum(float(x[0]) for x in vals))
        return out
    def process(self,t):
        if int(t)==T and self.preReceipt is None:
            ob=self._owner()
            self.preReceipt={'t':int(t),'scopeSide':getattr(self,'scopeSide',None),'scopeGeneration':int(getattr(self,'scopeGeneration',-1)),
              'owner2':None if ob is None else dict(ob),'unmatchedQty':self._unmatched(),
              'inv':{k:float(v) for k,v in getattr(self,'inv',{}).items()},'cost':float(getattr(self,'cost',0.0)),
              'splitCountBefore':len(getattr(self,'splitEvents',[]) or [])}
        super().process(t)
        if int(t)==T and self.postReceipt is None:
            ob=self._owner(); new=(getattr(self,'splitEvents',[]) or [])[int((self.preReceipt or {}).get('splitCountBefore') or 0):]
            hit=next((dict(e) for e in new if e.get('event')=='ROLE_FILL_SPLIT' and str(e.get('key'))==KEY),None)
            self.postReceipt={'t':int(t),'scopeSide':getattr(self,'scopeSide',None),'scopeGeneration':int(getattr(self,'scopeGeneration',-1)),
              'owner2':None if ob is None else dict(ob),'unmatchedQty':self._unmatched(),'split':hit,
              'inv':{k:float(v) for k,v in getattr(self,'inv',{}).items()},'cost':float(getattr(self,'cost',0.0))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_down25_identity_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}; tape=tmp/f'{MID}.json.xz';tape.write_bytes(z.read(f'tapes/{MID}.json.xz'))
        s=Gen3Down25IdentityAudit(tape,MID)
        try: raw=s.run_fork(co[MID]['winner']); pre=s.preReceipt;post=s.postReceipt
        finally:s.close()
        sp=(post or {}).get('split') or {}; obpre=(pre or {}).get('owner2') or {}; obpost=(post or {}).get('owner2') or {}
        repair=float(sp.get('repairAllocated') or 0.0); fill=float(sp.get('fillInc') or 0.0); overflow=float(sp.get('overflowRealized') or 0.0)
        owner_before=float(obpre.get('outstanding') or 0.0); owner_after=float(obpost.get('outstanding') or 0.0)
        pre_native_up=float(((pre or {}).get('unmatchedQty') or {}).get('UP') or 0.0)
        gates={
          'exactReceiptFound':bool(sp and int(sp.get('t') or -1)==T and str(sp.get('key'))==KEY),
          'sameGenerationRepair':bool(sp and int(sp.get('generationAtSubmit') or -1)==3 and str(sp.get('side'))=='DOWN' and str(sp.get('role'))=='ECONOMIC_CORE'),
          'owner2LiveExact':bool(obpre and int(obpre.get('generation') or -1)==3 and str(obpre.get('side'))=='UP' and near(owner_before,OWNER_DEBT,1e-12)),
          'repairContainsOwnerCapacity':repair+1e-12>=owner_before,
          'nativeScopeDebtContainsOwner':pre_native_up+1e-12>=owner_before,
          'fillConservation':abs(fill-repair-overflow)<=1e-12,
          'ownerCurrentlyUnbound':near(owner_after,owner_before,1e-12) and near(float(obpost.get('repaidQty') or 0.0),0.0,1e-12),
          'noOverflowAtReceipt':abs(overflow)<=1e-12,
          'upstreamCorrect':bool(raw.get('correct')),
        }
        gates['identityAndConservationPass']=all(gates.values())
        paid_if_bound=min(owner_before,repair)
        out={'version':'LANE_G_GEN3_DOWN25_OWNER_PAYMENT_IDENTITY_AUDIT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,
          'receipt':{'t':T,'key':KEY},'preReceipt':pre,'postReceipt':post,
          'derived':{'ownerOutstandingBefore':owner_before,'nativeUpUnmatchedBefore':pre_native_up,'repairAllocated':repair,'fillInc':fill,'overflow':overflow,
                     'ownerPayableFromConfirmedRepair':paid_if_bound,'repairBeyondOwner':max(0.0,repair-paid_if_bound)},
          'gates':gates,'classification':'PASS_SAME_SCOPE_CONFIRMED_REPAIR_CONTAINS_OWNER2_PAYMENT_BUT_R239_UNBOUND' if gates['identityAndConservationPass'] else 'IDENTITY_NOT_PROVEN',
          'boundary':['single consumed 1946468 receipt','read-only telemetry','no order/authority/credit mutation','R269 split unchanged','fresh untouched','no dream fill','no 8781','no time/rank-age gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'classification':out['classification'],'derived':out['derived'],'gates':gates},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
