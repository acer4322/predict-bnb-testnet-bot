from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class PostRepaidCompetitiveOpportunityScan(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.r301=Counter();self.r301Events=[];self._r301FavorableContext=False
        super().__init__(tape,fanout_limit,max_slots)

    def _try_favorable_replenishment(self,t,end):
        self._r301FavorableContext=True
        try:return super()._try_favorable_replenishment(t,end)
        finally:self._r301FavorableContext=False

    def _live_repair_rows(self):
        if self.scopeSide is None:return []
        side=self._repair_side();gen=int(self.scopeGeneration);out=[]
        for sid,key,o,role in self._live_role_rows(side=side):
            if role not in REPAIR_ROLES or int(self.key_scope_gen.get(key,-1))!=gen:continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            except Exception:st='';cum=float(o.get('cum') or 0.0)
            if st in v2.TERMINAL_STATUSES:continue
            out.append({'slotId':int(sid),'key':str(key),'role':str(role),'side':str(o['side']),'price':float(o['price']),
                        'qty':float(o['qty']),'cum':cum,'remaining':max(0.0,float(o['qty'])-cum),
                        'ageMs':None,'cancelRequested':bool(o.get('cancelRequested')),
                        'repairQuotaRemaining':float(self.keyRepairQuotaRemaining.get(key,0.0))})
        return out

    def _is_post_repaid_off_thesis(self,side,role):
        if self._r301FavorableContext:return False
        if role!='SATELLITE_EXPAND' or self.intentThesisSide not in {'UP','DOWN'}:return False
        if str(side)==str(self.intentThesisSide):return False
        ob=self.riskRepairObligations.get(int(self.scopeGeneration))
        return bool(ob is not None and str(ob.get('closeReason'))=='REPAID')

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if self._is_post_repaid_off_thesis(side,role):
            repairs=self._live_repair_rows();debt=max(0.0,float(self._scope_debt_qty()));repair_side=self._repair_side()
            reserved=max(0.0,float(self._reserved_repair_quota(repair_side)));credit=max(0.0,float(self._available_expand_risk_credit()))
            ev={'t':int(t),'event':'R301_POST_REPAID_OFF_THESIS_OPPORTUNITY','generation':int(self.scopeGeneration),
                'scopeSide':self.scopeSide,'repairSide':repair_side,'thesisSide':self.intentThesisSide,
                'ordinarySide':str(side),'ordinaryRole':str(role),'ordinaryPrice':float(p),'ordinaryQty':float(q),
                'physicalFloor':float(self._physical_floor()),'best':float(max(self.inv.values())-self.cost),
                'debt':debt,'reservedRepairQuota':reserved,'availableExpandCredit':credit,
                'liveSlots':len(self.slot_key),'freeSlots':int(self.max_slots-len(self.slot_key)),
                'liveRepairCarrierCount':len(repairs),'liveRepairCarriers':repairs,
                'singleRepairCarrier':len(repairs)==1,'zeroRepairCarrier':len(repairs)==0,'multiRepairCarrier':len(repairs)>1}
            self.r301Events.append(ev);self.r301['OPPORTUNITIES']+=1
            if len(repairs)==1:self.r301['SINGLE_REPAIR_CARRIER']+=1
            elif len(repairs)==0:self.r301['ZERO_REPAIR_CARRIER']+=1
            else:self.r301['MULTI_REPAIR_CARRIER']+=1
            if len(self.slot_key)>=self.max_slots:self.r301['FULL_CAPACITY']+=1
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
            self.r301['THESIS_BIRTH']+=1
        return ok

    def run_r301(self,winner):
        r=super().run_r264(winner)
        r.update({'r301Version':'MS4_R3_01_POST_REPAID_COMPETITIVE_OPPORTUNITY_SCAN_V1','r301Stats':dict(self.r301),
                  'r301Events':self.r301Events[:4000],'r301IntentThesisSide':self.intentThesisSide,
                  'r301IntentThesisBornAt':self.intentThesisBornAt,'r301IntentThesisSourceKey':self.intentThesisSourceKey})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r301_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            w=co[m]['winner'];s=PostRepaidCompetitiveOpportunityScan(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_r301(w)
            finally:s.close()
            row={'marketId':m,'winnerPostHocOnly':w,'pnlPostHocOnly':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),
                 'thesis':r.get('r301IntentThesisSide'),'opportunities':int(r.get('r301Stats',{}).get('OPPORTUNITIES',0)),
                 'singleRepair':int(r.get('r301Stats',{}).get('SINGLE_REPAIR_CARRIER',0)),
                 'zeroRepair':int(r.get('r301Stats',{}).get('ZERO_REPAIR_CARRIER',0)),
                 'multiRepair':int(r.get('r301Stats',{}).get('MULTI_REPAIR_CARRIER',0)),
                 'fullCapacity':int(r.get('r301Stats',{}).get('FULL_CAPACITY',0)),
                 'events':r.get('r301Events',[]),'correct':bool(r.get('r264CorrectnessPass'))}
            rows.append(row);print(json.dumps({k:row[k] for k in ['marketId','thesis','opportunities','singleRepair','zeroRepair','multiRepair','fullCapacity','correct']},ensure_ascii=False),flush=True)
        structural=[r['marketId'] for r in rows if r['opportunities']>0]
        single=[r['marketId'] for r in rows if r['singleRepair']>0]
        untouched=[m for m in sorted(single) if m not in {1946475,1946792,1945898,1946784}]
        frozen_replication=untouched[:6]
        out={'version':'MS4_R3_01_POST_REPAID_COMPETITIVE_OPPORTUNITY_SCAN_V1','researchOnly':True,'behaviorChange':False,
             'markets':mids,'rows':rows,'summary':{'marketCount':len(rows),'opportunityMarkets':structural,'singleRepairMarkets':single,
             'totalOpportunities':sum(r['opportunities'] for r in rows),'totalSingleRepair':sum(r['singleRepair'] for r in rows),
             'totalMultiRepair':sum(r['multiRepair'] for r in rows),'totalFullCapacity':sum(r['fullCapacity'] for r in rows),
             'outcomeBlindReplication6':frozen_replication},
             'boundary':['R2.64 baseline behavior unchanged','persistent intent identity diagnostic only','winner/PnL stored posthoc but not used for structural selection','replication selection=ascending marketId among previously untested single-Repair structural markets','no threshold fitting','consumed full24 only','no fresh/no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
