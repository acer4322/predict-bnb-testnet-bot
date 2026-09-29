from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_lane_g_r263_r303_same_authority_scan_v1 as scan
v2=scan.v2; EPS=scan.EPS

class OrdinaryReexpandTriggerCapture(scan.SameAuthorityScan):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw); self.fullOrdinary=[]
    def _payoff(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);b=max(u,d)-c;f=min(u,d)-c
        return {'upQty':u,'downQty':d,'cost':c,'best':b,'floor':f,'gap':b-f}
    def _full_pre(self,t,end):
        ob=self._obligation_current();gen=int(ob['generation']) if ob else int(self.scopeGeneration)
        repairs=[]
        if ob:
            for key,o in self._live_dedicated_repair_rows(gen):
                try:s=self.snap(o);st=str(s.get('status') or '').upper();rem=float(s.get('leavesQty')) if s.get('leavesQty') is not None else float(self._remaining(key));cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                except Exception:st='';rem=float(self._remaining(key));cum=float(o.get('cum') or 0.0)
                repairs.append({'key':str(key),'side':str(o['side']),'price':float(o['price']),'qty':float(o.get('qty') or 0.0),'remaining':rem,'cum':cum,'status':st,'repairQuotaRemaining':float(self.keyRepairQuotaRemaining.get(key,0.0))})
        return {'t':int(t),'generation':gen,'scopeSide':self.scopeSide,'repairSide':self._repair_side() if self.scopeSide else None,'intentThesisSide':self.intentThesisSide,
                'obligationOutstanding':float(ob.get('outstanding') or 0.0) if ob else 0.0,'obligationBornQty':float(ob.get('bornQty') or 0.0) if ob else 0.0,'obligationRepaidQty':float(ob.get('repaidQty') or 0.0) if ob else 0.0,
                'scopeDebt':float(self._scope_debt_qty()) if self.scopeSide else 0.0,'liveRepairs':repairs,'representedRepairQuota':sum(float(x['repairQuotaRemaining']) for x in repairs),
                'slots':len(self.slot_key),'active':len(self.activeKeys),'maxSlots':int(self.max_slots),'staleScope':bool(self._has_stale_scope_reservation()),
                'availableExpandCredit':float(self._available_expand_risk_credit()),'payoff':self._payoff(),'secondsLeftDescriptiveOnly':(int(end)-int(t))/1000.0}
    def _try_r263(self,t,end):
        pre=self._full_pre(t,end);n0=int(self.n);s0=int(self.submits);h0=len(self.r263Events)
        ok=super()._try_r263(t,end)
        if ok and int(self.submits)-s0==1:
            evs=self.r263Events[h0:];ev=next((x for x in evs if x.get('event')=='R263_SINGLE_PRE_REPAIR_REEXPAND_SUBMIT'),None)
            if ev:self.fullOrdinary.append({'t':int(t),'pre':pre,'ordinaryEvent':ev,'ordinaryKey':str(ev['key']),'ordinarySide':str(ev['side']),'ordinaryPrice':float(ev['price']),'ordinaryQty':float(ev['qty']),'riskAuthorized':float(ev['riskAuthorized'])})
        return ok

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_ord_capture_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];chosen={}
        for m in mids:
            sim=OrdinaryReexpandTriggerCapture(tmp/f'{m}.json.xz',1,4)
            try:r=sim.run_scan(co[m]['winner'])
            finally:sim.close()
            caps=sim.fullOrdinary;first=caps[0] if caps else None
            row={'marketId':m,'captures':caps,'captureCount':len(caps),'first':first,'correct':bool(r.get('r264CorrectnessPass'))};rows.append(row)
            if first:
                p=first['pre'];rep=p['liveRepairs']
                chosen[str(m)]={'t':first['t'],'generation':p['generation'],'scopeSide':p['scopeSide'],'repairSide':p['repairSide'],'thesisSide':p['intentThesisSide'],'obligationOutstanding':p['obligationOutstanding'],'obligationRepaid':p['obligationRepaidQty'],'scopeDebt':p['scopeDebt'],'liveRepairs':rep,'representedRepairQuota':p['representedRepairQuota'],'slots':p['slots'],'active':p['active'],'payoff':p['payoff'],'ordinary':{'side':first['ordinarySide'],'price':first['ordinaryPrice'],'qty':first['ordinaryQty'],'risk':first['riskAuthorized']}}
            print(json.dumps({'marketId':m,'captureCount':len(caps),'firstT':first['t'] if first else None,'repairKeys':[x['key'] for x in (first['pre']['liveRepairs'] if first else [])],'correct':row['correct']},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_ORDINARY_REEXPAND_TRIGGER_CAPTURE_H100_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'rows':rows,'markets':chosen,
             'gates':{'allMarketsCaptured':len(chosen)==len(mids),'correctnessPass':all(r['correct'] for r in rows)},
             'boundary':['first actual inherited R263 submit per preregistered distinct market','strict-past pre-call state','behavior inert capture','winner/terminal not used for selection','consumed H100 only','max4 <=180s inherited','no fresh/no dream fill/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'marketCount':len(chosen)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
