from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_55_pre_repair_risk_tranche as r255
r247=r255.r247; v2=r255.v2; EPS=1e-9; REPAIR_ROLES=r255.REPAIR_ROLES

class RiskFillPassiveRepairObligationSim(r255.PreRepairRiskTrancheSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r257=Counter(); self.r257Events=[]; self.riskRepairObligations={}; self.riskRepairCarrierKeys=set()

    def _obligation_current(self):
        if self.scopeSide is None:return None
        x=self.riskRepairObligations.get(int(self.scopeGeneration))
        if not x or float(x['outstanding'])<=EPS:return None
        return x

    def _has_live_risk_repair_carrier(self,gen):
        for key in list(self.riskRepairCarrierKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(gen):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False

    def _try_risk_repair_carrier(self,t):
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if self._has_stale_scope_reservation() or self._has_live_risk_repair_carrier(gen):return False
        repair_side=self._repair_side()
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self.r257['RISK_REPAIR_SHARED_CAPACITY_BLOCK']+=1;return False
        cand=self._candidate_from_levels_v8(repair_side,'ECONOMIC_CORE',False)
        if cand is None:
            self.r257['RISK_REPAIR_NO_LEGAL_PASSIVE_CARRIER']+=1;return False
        p,q,proj,split=cand
        if split is None or float(split.get('repairQty') or 0.0)<=EPS:return False
        before_n=self.n
        if not self._submit_role_v8(t,repair_side,'ECONOMIC_CORE',p,q,proj,split):
            self.r257['RISK_REPAIR_SUBMIT_BLOCKED']+=1;return False
        key=f'{repair_side}_{before_n}';self.riskRepairCarrierKeys.add(key)
        ob['carrierSubmits']+=1;ob['lastCarrierKey']=key;ob['lastCarrierSubmitAt']=int(t)
        self.r257['RISK_REPAIR_CARRIER_SUBMIT']+=1
        ev={'t':int(t),'event':'R257_RISK_REPAIR_CARRIER_SUBMIT','generation':gen,'key':key,
            'side':repair_side,'price':float(p),'qty':float(q),'repairQuota':float(split['repairQty']),
            'outstandingBefore':float(ob['outstanding'])}
        self.r257Events.append(ev);self.slot_history.append(ev);return True

    def _open_one_option(self,t,qv,end):
        # A realized risk obligation gets first passive service priority, but entry itself
        # was already allowed by R2.55 before this obligation exists.
        if self._try_risk_repair_carrier(t):
            # Keep remaining slots available to frozen R2.47; do not serialize the portfolio.
            r247.BoundedCoreServiceFavorableRecycleSim._open_one_option(self,t,qv,end)
            return
        super()._open_one_option(t,qv,end)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));gen=int(ev.get('generationAtSubmit') or -1);inc=float(ev.get('fillInc') or 0.0)
            rq=float(ev.get('repairAllocated') or 0.0);side=str(ev.get('side'));role=str(ev.get('role'))
            if key in self.riskTrancheKeys and inc>EPS:
                ob=self.riskRepairObligations.setdefault(gen,{'generation':gen,'scopeSideAtBirth':self.riskTrancheMeta.get(key,{}).get('scopeSide'),
                    'bornAt':int(t),'bornFromRiskKey':key,'bornQty':0.0,'outstanding':0.0,'repaidQty':0.0,
                    'passiveRepaidQty':0.0,'activeRepaidQty':0.0,'carrierSubmits':0,'carrierFills':0,
                    'zeroFillTerminals':0,'closedAt':None,'closeReason':None})
                ob['bornQty']+=inc;ob['outstanding']+=inc;self.r257['RISK_REPAIR_OBLIGATION_BORN']+=1
                self.r257Events.append({'t':int(t),'event':'R257_RISK_REPAIR_OBLIGATION_BORN','generation':gen,
                    'riskKey':key,'qty':inc,'outstanding':ob['outstanding']})
            # Any actual authoritative Repair allocation pays this realized risk obligation.
            ob=self.riskRepairObligations.get(gen)
            if ob and rq>EPS and float(ob['outstanding'])>EPS and role in REPAIR_ROLES:
                pay=min(float(ob['outstanding']),rq);ob['outstanding']-=pay;ob['repaidQty']+=pay
                active=key in self.activeMeta
                if active:ob['activeRepaidQty']+=pay
                else:ob['passiveRepaidQty']+=pay
                if key in self.riskRepairCarrierKeys:ob['carrierFills']+=1;self.r257['RISK_REPAIR_CARRIER_FILL']+=1
                self.r257['RISK_REPAIR_PASSIVE_REPAID_MILLI' if not active else 'RISK_REPAIR_ACTIVE_REPAID_MILLI']+=int(round(pay*1000))
                self.r257Events.append({'t':int(t),'event':'R257_RISK_REPAIR_OBLIGATION_PAYMENT','generation':gen,
                    'key':key,'role':role,'active':active,'repairAllocated':rq,'paid':pay,'outstandingAfter':ob['outstanding']})
                if ob['outstanding']<=EPS and ob['closedAt'] is None:
                    ob['closedAt']=int(t);ob['closeReason']='REPAID';self.r257['RISK_REPAIR_OBLIGATION_REPAID']+=1
        # Track terminal zero-fill of dedicated carriers for re-materialization attribution.
        for key in list(self.riskRepairCarrierKeys):
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            except Exception:continue
            if st in v2.TERMINAL_STATUSES and cum<=EPS:
                gen=int(self.key_scope_gen.get(key,-1));ob=self.riskRepairObligations.get(gen)
                marker='zeroSeen:'+key
                if ob is not None and not ob.get(marker):
                    ob[marker]=True;ob['zeroFillTerminals']+=1;self.r257['RISK_REPAIR_ZERO_FILL_TERMINAL']+=1
        # Scope departure closes only the service obligation, never rewrites native debt.
        for gen,ob in self.riskRepairObligations.items():
            if ob['closedAt'] is not None:continue
            if self.scopeSide is None or int(self.scopeGeneration)!=int(gen):
                ob['closedAt']=int(t);ob['closeReason']='SCOPE_LEFT';self.r257['RISK_REPAIR_OBLIGATION_SCOPE_LEFT']+=1

    def run_r257(self,winner):
        r=super().run_r255(winner)
        obs=[{k:v for k,v in x.items() if not str(k).startswith('zeroSeen:')} for x in self.riskRepairObligations.values()]
        r.update({'r257Version':'MS4_R2_57_RISK_FILL_PASSIVE_REPAIR_OBLIGATION_V1','r257Stats':dict(self.r257),
            'r257Events':self.r257Events[:4000],'riskRepairObligations':obs,
            'riskRepairObligationsBorn':sum(1 for x in obs if x['bornQty']>EPS),
            'riskRepairObligationsRepaid':sum(1 for x in obs if x['closeReason']=='REPAID'),
            'riskRepairPassiveRepaidQty':sum(float(x['passiveRepaidQty']) for x in obs),
            'riskRepairActiveRepaidQty':sum(float(x['activeRepaidQty']) for x in obs),
            'riskRepairCarrierSubmits':sum(int(x['carrierSubmits']) for x in obs),
            'riskRepairCarrierFills':sum(int(x['carrierFills']) for x in obs)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r257_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            sim=RiskFillPassiveRepairObligationSim(tape,1,4)
            try:c=sim.run_r257(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R247_CONTROL','winnerPostHocOnly':w,**b},{'marketId':mid,'cell':'R257_RISK_FILL_REPAIR_OBLIGATION','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'riskFills':c['riskTrancheFillEvents'],'obligationsBorn':c['riskRepairObligationsBorn'],
               'obligationsRepaid':c['riskRepairObligationsRepaid'],'carrierSubmits':c['riskRepairCarrierSubmits'],
               'carrierFills':c['riskRepairCarrierFills'],'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'activeRepaidQty':c['riskRepairActiveRepaidQty'],'riskDebtPeak':c['riskDebtPeak'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_57_RISK_FILL_PASSIVE_REPAIR_OBLIGATION_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'riskFillExercised':any(x['riskFills']>0 for x in cmp),
                      'passiveRepairServiceExercised':any(x['carrierSubmits']>0 or x['passiveRepaidQty']>0 for x in cmp),
                      'obligationRepaidExercised':any(x['obligationsRepaid']>0 for x in cmp)},
             'boundary':['risk entry never waits for Repair outcome','Repair obligation born only from confirmed risk fill','persistent rolling passive repair priority','native debt/quota authoritative','R2.47 ordinary/multislot remains active','max4','<=180s new exposure unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
