from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239

EPS=1e-9
REPLAY_MIDS=(1946640,1946899)
ANCHOR_MID=1946468
REQUIRED={
  1946640:[(1788535906741,'UP_30',1.5454545454545452)],
  1946899:[(1788539440439,'DOWN_16',0.17517335913562326),(1788539462867,'UP_21',0.8554003268829271)],
}
FROZEN_ANCHOR={
  'marketId':1946468,'t':1788534963270,'key':'DOWN_25','expectedOwnerPayment':0.666773270050439,
  'sourceResult':'LANE_G_GEN3_DOWN25_R239_PAYMENT_BINDING_EXACT_FORK_V1_RESULT_20260907.json',
  'verdict':'PASS_GEN3_PAYMENT_BINDING_NO_PHYSICAL_DELTA','physicalDelta':{'submits':0,'fills':0,'pnl':0,'floor':0,'best':0}
}

def near(a,b,tol=1e-9):return abs(float(a)-float(b))<=tol

def terminal_core(x):
    return {'submits':int(x.get('submits') or 0),'fills':int(x.get('fillEvents') or 0),
            'pnl':float(x.get('pnlDiagnosticOnly') or 0.0),'floor':float(x.get('floor') or 0.0),
            'best':float(x.get('best') or 0.0),'maxSlots':int(x.get('maxSimultaneousSlots') or 0)}

class StructuralNativeRepairPaymentBindingSim(r239.OverflowResponsibilityHandoffSim):
    """R239 with bookkeeping-only attribution of confirmed native ECONOMIC_CORE Repair to the live owner.
    Dedicated R239 handoff accounting remains first. Native payment is applied before scope-flip closure and
    before confirmed overflow registration. No physical execution primitive is changed.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.nativeBindEvents=[]
    def process(self,t):
        before_n=len(self.splitEvents)
        r239.r28.FanoutRoleCapacitySim.process(self,t)
        new_events=self.splitEvents[before_n:]
        # Existing dedicated handoff Repair accounting, unchanged.
        for ev in new_events:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.handoffKeys:continue
            oid=int(self.handoffKeys[key]);ob=next((x for x in self.obligations if int(x['id'])==oid),None)
            if not ob:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            paid=min(float(ob['outstanding']),rq);ob['outstanding']=max(0.0,float(ob['outstanding'])-paid);ob['repaidQty']+=paid
            self.r239['HANDOFF_REPAIR_FILL']+=1;self.r239['HANDOFF_REPAIR_FILL_QTY_MILLI']+=int(round(paid*1000))
            x={'t':int(t),'event':'R239_HANDOFF_REPAIR_FILL','obligationId':oid,'key':key,'repairQty':rq,'liabilityPaidQty':paid,
               'outstanding':float(ob['outstanding']),'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x)
            if ob['outstanding']<=EPS and self.activeObligationId==oid:self._close_active(t,'REPAID')
        # Structural native Repair payment attribution.
        for ev in new_events:
            if ev.get('event')!='ROLE_FILL_SPLIT' or ev.get('role')!='ECONOMIC_CORE':continue
            key=str(ev.get('key'))
            if key in self.handoffKeys:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            ob=self._active_obligation()
            if not ob or float(ob.get('outstanding') or 0.0)<=EPS:continue
            repair_side=str(ev.get('side')); expected='DOWN' if str(ob.get('side'))=='UP' else 'UP'
            if repair_side!=expected:continue
            evgen=int(ev.get('generationAtSubmit') or ev.get('generation') or -1)
            if evgen!=int(ob.get('generation') or -2):continue
            before=float(ob.get('outstanding') or 0.0);paid=min(before,rq)
            if paid<=EPS:continue
            ob['outstanding']=max(0.0,before-paid);ob['repaidQty']=float(ob.get('repaidQty') or 0.0)+paid
            ob['passiveRepaidQty']=float(ob.get('passiveRepaidQty') or 0.0)+paid
            self.r239['NATIVE_CORE_REPAIR_PAYMENT_BIND']+=1;self.r239['NATIVE_CORE_REPAIR_PAYMENT_QTY_MILLI']+=int(round(paid*1000))
            x={'t':int(t),'event':'R239_NATIVE_CORE_REPAIR_PAYMENT_BIND','obligationId':int(ob['id']),'key':key,
               'ownerSide':str(ob.get('side')),'generation':int(ob.get('generation')),'repairQty':rq,
               'liabilityPaidQty':paid,'repairBeyondOwner':max(0.0,rq-paid),'outstanding':float(ob['outstanding']),
               'confirmedOverflow':float(ev.get('overflowRealized') or 0.0),'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x);self.nativeBindEvents.append(x)
            if ob['outstanding']<=EPS and self.activeObligationId==int(ob['id']):self._close_active(t,'REPAID')
        # Existing scope transition closure and overflow ownership registration.
        ob=self._active_obligation()
        if ob and (self.scopeSide!=ob['side'] or int(self.scopeGeneration)!=int(ob['generation'])):self._close_active(t,'SCOPE_LEFT_OVERFLOW_SIDE')
        for ev in new_events:
            if ev.get('event')=='ROLE_FILL_SPLIT' and ev.get('role')=='ECONOMIC_CORE' and float(ev.get('overflowRealized') or 0.0)>EPS:self._register_overflow(t,ev)

def run_one(tape,winner,mid,candidate):
    sim=StructuralNativeRepairPaymentBindingSim(tape,1,4) if candidate else r239.OverflowResponsibilityHandoffSim(tape,1,4)
    try:
        raw=sim.run_r239(winner)
        return {'marketId':mid,'branch':'STRUCTURAL_NATIVE_REPAIR_PAYMENT_BINDING' if candidate else 'CURRENT_R239',
                'terminal':terminal_core(raw),'obligations':[dict(x) for x in sim.obligations],
                'r239Events':[dict(x) for x in sim.r239events],'bindEvents':[dict(x) for x in getattr(sim,'nativeBindEvents',[])],
                'unauthorizedOverflowQty':float(raw.get('unauthorizedOverflowQty') or 0.0),
                'repairQuotaExcessMax':float(raw.get('repairQuotaExcessMax') or 0.0)}
    finally:sim.close()

def receipt_matches(row,mid):
    out=[]
    for t,key,expected in REQUIRED[mid]:
        hits=[e for e in row['bindEvents'] if int(e.get('t') or -1)==t and str(e.get('key'))==key]
        paid=sum(float(e.get('liabilityPaidQty') or 0.0) for e in hits)
        out.append({'t':t,'key':key,'expectedOwnerPayment':expected,'observedOwnerPayment':paid,'hitCount':len(hits),'pass':near(paid,expected,1e-10)})
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_r239_structural_smoke3_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in REPLAY_MIDS:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];comparisons=[];receiptChecks=[]
        for mid in REPLAY_MIDS:
            tape=tmp/f'{mid}.json.xz';winner=co[mid]['winner'];base=run_one(tape,winner,mid,False);cand=run_one(tape,winner,mid,True);rows.extend([base,cand])
            d={k:float(cand['terminal'][k])-float(base['terminal'][k]) for k in ('submits','fills','pnl','floor','best','maxSlots')}
            rc=receipt_matches(cand,mid);receiptChecks.extend([{'marketId':mid,**x} for x in rc])
            comparisons.append({'marketId':mid,'physicalDelta':d,'candidateBindEvents':cand['bindEvents'],'receiptChecks':rc,
                                'unauthorizedOverflowQty':cand['unauthorizedOverflowQty'],'repairQuotaExcessMax':cand['repairQuotaExcessMax']})
            print(json.dumps({'marketId':mid,'physicalDelta':d,'receiptChecks':rc,'bindEvents':cand['bindEvents']},ensure_ascii=False),flush=True)
        no_double=True
        for r in rows:
            if r['branch']!='STRUCTURAL_NATIVE_REPAIR_PAYMENT_BINDING':continue
            dedicated={str(e.get('key')) for e in r['r239Events'] if e.get('event')=='R239_HANDOFF_REPAIR_FILL'}
            if any(str(e.get('key')) in dedicated for e in r['bindEvents']):no_double=False
        gates={'frozen1946468ExactAnchorPass':FROZEN_ANCHOR['verdict']=='PASS_GEN3_PAYMENT_BINDING_NO_PHYSICAL_DELTA',
               'allNewRequiredReceiptsBoundExact':all(x['pass'] and x['hitCount']==1 for x in receiptChecks),
               'noPhysicalDeltaPerReplayMarket':all(all(abs(float(v))<=1e-12 for v in c['physicalDelta'].values()) for c in comparisons),
               'ownerPaymentNeverExceedsConfirmedRepair':all(float(e.get('liabilityPaidQty') or 0.0)<=float(e.get('repairQty') or 0.0)+EPS for r in rows for e in r['bindEvents']),
               'noDoublePayDedicatedHandoff':no_double,
               'noUnauthorizedOverflow':all(float(c['unauthorizedOverflowQty'])<=EPS for c in comparisons),
               'noRepairQuotaExcess':all(float(c['repairQuotaExcessMax'])<=EPS for c in comparisons),
               'max4Preserved':all(r['terminal']['maxSlots']<=4 for r in rows)}
        gates['correctnessAllPass']=all(gates.values())
        out={'version':'LANE_G_R239_NATIVE_REPAIR_PAYMENT_BINDING_STRUCTURAL_SMOKE3_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,
             'cohort':[1946468,1946640,1946899],'frozenExactAnchor':FROZEN_ANCHOR,'replayedMarkets':list(REPLAY_MIDS),'rows':rows,'comparisons':comparisons,
             'receiptChecks':receiptChecks,'gates':gates,'verdict':'PASS_STRUCTURAL_NATIVE_REPAIR_PAYMENT_BINDING_SMOKE3' if gates['correctnessAllPass'] else 'FAIL_STRUCTURAL_NATIVE_REPAIR_PAYMENT_BINDING_SMOKE3',
             'boundary':['1946468 is frozen exact anchor and is not rerun','new realistic-HFT replay only on consumed 1946640/1946899','bookkeeping attribution only','same confirmed physical fills','dedicated R239 handoff accounting remains first','only confirmed overflow may birth/augment successor','no new authority/credit/capacity','max4 and <=180s inherited','pending-zero unchanged','no fresh','no dream fill','no 8781','no fixed-time/window/rank-age gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'verdict':out['verdict'],'gates':gates},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
