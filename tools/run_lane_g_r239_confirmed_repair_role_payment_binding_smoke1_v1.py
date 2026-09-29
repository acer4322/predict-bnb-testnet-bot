from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
EPS=1e-9; MID=1946899
REQ=[(1788539447839,'UP_18','SATELLITE_REPAIR',1.4084507042253522),(1788539462867,'UP_21','ECONOMIC_CORE',0.8554003268829271)]

def near(a,b,tol=1e-10):return abs(float(a)-float(b))<=tol

def term(x):return {'submits':int(x.get('submits') or 0),'fills':int(x.get('fillEvents') or 0),'pnl':float(x.get('pnlDiagnosticOnly') or 0.0),'floor':float(x.get('floor') or 0.0),'best':float(x.get('best') or 0.0),'maxSlots':int(x.get('maxSimultaneousSlots') or 0)}

class RepairRoleBinding(r239.OverflowResponsibilityHandoffSim):
    def __init__(self,tape):super().__init__(tape,1,4);self.bindEvents=[]
    def process(self,t):
        before=len(self.splitEvents);r239.r28.FanoutRoleCapacitySim.process(self,t);new=self.splitEvents[before:]
        # Existing dedicated handoff first.
        for ev in new:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.handoffKeys:continue
            oid=int(self.handoffKeys[key]);ob=next((x for x in self.obligations if int(x['id'])==oid),None)
            if not ob:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            paid=min(float(ob['outstanding']),rq);ob['outstanding']=max(0.0,float(ob['outstanding'])-paid);ob['repaidQty']+=paid
            self.r239['HANDOFF_REPAIR_FILL']+=1;self.r239['HANDOFF_REPAIR_FILL_QTY_MILLI']+=int(round(paid*1000))
            x={'t':int(t),'event':'R239_HANDOFF_REPAIR_FILL','obligationId':oid,'key':key,'repairQty':rq,'liabilityPaidQty':paid,'outstanding':float(ob['outstanding']),'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x)
            if ob['outstanding']<=EPS and self.activeObligationId==oid:self._close_active(t,'REPAID')
        # General confirmed Repair-role attribution, excluding dedicated keys.
        for ev in new:
            if ev.get('event')!='ROLE_FILL_SPLIT' or str(ev.get('role')) not in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:continue
            key=str(ev.get('key'))
            if key in self.handoffKeys:continue
            rq=float(ev.get('repairAllocated') or 0.0)
            if rq<=EPS:continue
            ob=self._active_obligation()
            if not ob or float(ob.get('outstanding') or 0.0)<=EPS:continue
            expected='DOWN' if str(ob.get('side'))=='UP' else 'UP'
            if str(ev.get('side'))!=expected:continue
            if int(ev.get('generationAtSubmit') or ev.get('generation') or -1)!=int(ob.get('generation') or -2):continue
            beforeq=float(ob['outstanding']);paid=min(beforeq,rq);ob['outstanding']=max(0.0,beforeq-paid);ob['repaidQty']=float(ob.get('repaidQty') or 0.0)+paid;ob['passiveRepaidQty']=float(ob.get('passiveRepaidQty') or 0.0)+paid
            x={'t':int(t),'event':'R239_CONFIRMED_REPAIR_ROLE_PAYMENT_BIND','obligationId':int(ob['id']),'key':key,'role':str(ev.get('role')),'generation':int(ob['generation']),'repairQty':rq,'liabilityPaidQty':paid,'repairBeyondOwner':max(0.0,rq-paid),'outstanding':float(ob['outstanding']),'confirmedOverflow':float(ev.get('overflowRealized') or 0.0),'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x);self.bindEvents.append(x)
            if ob['outstanding']<=EPS and self.activeObligationId==int(ob['id']):self._close_active(t,'REPAID')
        ob=self._active_obligation()
        if ob and (self.scopeSide!=ob['side'] or int(self.scopeGeneration)!=int(ob['generation'])):self._close_active(t,'SCOPE_LEFT_OVERFLOW_SIDE')
        for ev in new:
            if ev.get('event')=='ROLE_FILL_SPLIT' and ev.get('role')=='ECONOMIC_CORE' and float(ev.get('overflowRealized') or 0.0)>EPS:self._register_overflow(t,ev)

def run(tape,winner,cand):
    s=RepairRoleBinding(tape) if cand else r239.OverflowResponsibilityHandoffSim(tape,1,4)
    try:
        raw=s.run_r239(winner);return {'branch':'CONFIRMED_REPAIR_ROLE_BINDING' if cand else 'CURRENT_R239','terminal':term(raw),'obligations':[dict(x) for x in s.obligations],'events':[dict(x) for x in s.r239events],'bindEvents':[dict(x) for x in getattr(s,'bindEvents',[])],'unauthorizedOverflowQty':float(raw.get('unauthorizedOverflowQty') or 0.0),'repairQuotaExcessMax':float(raw.get('repairQuotaExcessMax') or 0.0)}
    finally:s.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_r239_role_bind_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};tape=tmp/f'{MID}.json.xz';tape.write_bytes(z.read(f'tapes/{MID}.json.xz'))
        b=run(tape,co[MID]['winner'],False);c=run(tape,co[MID]['winner'],True);d={k:float(c['terminal'][k])-float(b['terminal'][k]) for k in ('submits','fills','pnl','floor','best','maxSlots')}
        checks=[]
        for t,key,role,exp in REQ:
            hits=[e for e in c['bindEvents'] if int(e.get('t') or -1)==t and str(e.get('key'))==key and str(e.get('role'))==role];paid=sum(float(e.get('liabilityPaidQty') or 0.0) for e in hits);checks.append({'t':t,'key':key,'role':role,'expected':exp,'observed':paid,'hitCount':len(hits),'pass':near(paid,exp)})
        owner2=next((x for x in c['obligations'] if int(x.get('id') or -1)==2),None);owner3=next((x for x in c['obligations'] if int(x.get('id') or -1)==3),None)
        dedicated={str(e.get('key')) for e in c['events'] if e.get('event')=='R239_HANDOFF_REPAIR_FILL'}
        gates={'requiredReceiptsExact':all(x['pass'] and x['hitCount']==1 for x in checks),'owner2FullyRepaid':bool(owner2 and near(owner2.get('outstanding',0),0) and near(owner2.get('repaidQty',0),2.2638510311082793) and owner2.get('closeReason')=='REPAID'),'successorOverflowUnchanged':bool(owner3 and near(owner3.get('originOverflowQty',0),0.8395149273543612)),'noDedicatedDoublePay':all(str(e.get('key')) not in dedicated for e in c['bindEvents']),'noPhysicalDelta':all(abs(float(v))<=1e-12 for v in d.values()),'noUnauthorizedOverflow':c['unauthorizedOverflowQty']<=EPS,'noRepairQuotaExcess':c['repairQuotaExcessMax']<=EPS,'max4Preserved':c['terminal']['maxSlots']<=4};gates['correctnessAllPass']=all(gates.values())
        out={'version':'LANE_G_R239_CONFIRMED_REPAIR_ROLE_PAYMENT_BINDING_SMOKE1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'rows':[b,c],'receiptChecks':checks,'physicalDelta':d,'gates':gates,'verdict':'PASS_CONFIRMED_REPAIR_ROLE_PAYMENT_BINDING_SMOKE1' if gates['correctnessAllPass'] else 'FAIL_CONFIRMED_REPAIR_ROLE_PAYMENT_BINDING_SMOKE1','boundary':['consumed 1946899 only','bookkeeping attribution only','dedicated handoff first and excluded from generic binding','only confirmed overflow births successor','no new authority/credit/capacity','max4/<=180s inherited','no fresh','no dream fill','no 8781','no time/window/rank-age gate']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'verdict':out['verdict'],'gates':gates,'receiptChecks':checks,'physicalDelta':d},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
