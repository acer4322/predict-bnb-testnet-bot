from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class ObligationLiveCompetitiveContinuationShadow(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.r302=Counter();self.r302Events=[];self._r302FavorableContext=False
        super().__init__(tape,fanout_limit,max_slots)

    def _try_favorable_replenishment(self,t,end):
        self._r302FavorableContext=True
        try:return super()._try_favorable_replenishment(t,end)
        finally:self._r302FavorableContext=False

    def _live_repair_rows(self):
        if self.scopeSide is None:return []
        side=self._repair_side();gen=int(self.scopeGeneration);out=[]
        for sid,key,o,role in self._live_role_rows(side=side):
            if role not in REPAIR_ROLES or int(self.key_scope_gen.get(key,-1))!=gen:continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            except Exception:st='';cum=float(o.get('cum') or 0.0)
            if st in v2.TERMINAL_STATUSES:continue
            out.append({'slotId':int(sid),'key':str(key),'role':str(role),'price':float(o['price']),'qty':float(o['qty']),
                        'remaining':max(0.0,float(o['qty'])-cum),'cancelRequested':bool(o.get('cancelRequested')),
                        'repairQuotaRemaining':float(self.keyRepairQuotaRemaining.get(key,0.0))})
        return out

    def _live_obligation(self):
        if self.scopeSide is None:return None
        ob=self.riskRepairObligations.get(int(self.scopeGeneration))
        if not ob:return None
        if float(ob.get('outstanding') or 0.0)<=EPS:return None
        if ob.get('closeReason') not in (None,''):return None
        return ob

    def _is_live_off_thesis_expand(self,side,role):
        if self._r302FavorableContext:return False
        if role!='SATELLITE_EXPAND' or self.intentThesisSide not in {'UP','DOWN'}:return False
        if str(side)==str(self.intentThesisSide):return False
        return self._live_obligation() is not None

    def _structural_geometry(self,thesis,debt,reserved):
        used=self._used_prices(thesis);exclusive=None;shared=None
        for raw in self._live_price_levels(thesis):
            p=float(v2.kprice(raw))
            if p<=EPS or p in used:continue
            venue=1.0/p
            # Current/exclusive representation: only unreserved debt may be claimed.
            avail=max(0.0,float(debt)-float(reserved));qex=avail+venue
            if exclusive is None and avail>EPS and math.isfinite(qex) and qex<=12.0+EPS:
                sp=self._repair_split(thesis,p,qex)
                if sp is not None and float(sp.get('repairQty') or 0)>EPS and float(sp.get('overflowQty') or 0)>EPS:
                    exclusive={'price':p,'physicalQty':qex,'repairQty':float(sp['repairQty']),'overflowQty':float(sp['overflowQty']),'overflowRisk':float(sp['overflowRisk'])}
            # Shared-parent geometry only: sibling reservations share parent debt; no behavior authority.
            qsh=float(debt)+venue
            if shared is None and debt>EPS and math.isfinite(qsh) and qsh<=12.0+EPS:
                repair_floor=float(self._candidate_alone_floor(thesis,p,float(debt)))
                full_floor=float(self._candidate_alone_floor(thesis,p,qsh))
                shared={'price':p,'physicalQty':qsh,'repairQty':float(debt),'overflowQty':venue,
                        'overflowRisk':max(0.0,repair_floor-full_floor)}
            if exclusive is not None and shared is not None:break
        return exclusive,shared

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if self._is_live_off_thesis_expand(side,role):
            ob=self._live_obligation();repairs=self._live_repair_rows();thesis=self.intentThesisSide;repair_side=self._repair_side()
            debt=max(0.0,float(self._scope_debt_qty()));reserved=max(0.0,float(self._reserved_repair_quota(repair_side)))
            credit=max(0.0,float(self._available_expand_risk_credit()));free=int(self.max_slots-len(self.slot_key))
            ex,sh=self._structural_geometry(thesis,debt,reserved) if thesis==repair_side else (None,None)
            born=max(EPS,float(ob.get('bornQty') or 0.0));outstanding=max(0.0,float(ob.get('outstanding') or 0.0));repaid=max(0.0,born-outstanding)
            ev={'t':int(t),'event':'R302_OBLIGATION_LIVE_OFF_THESIS_EXPAND','generation':int(self.scopeGeneration),
                'scopeSide':self.scopeSide,'repairSide':repair_side,'thesisSide':thesis,'thesisIsRepairSide':thesis==repair_side,
                'ordinarySide':str(side),'ordinaryPrice':float(p),'ordinaryQty':float(q),'physicalFloor':float(self._physical_floor()),
                'best':float(max(self.inv.values())-self.cost),'riskBornQty':born,'riskOutstandingQty':outstanding,
                'riskRepaidQty':repaid,'riskRepaidFrac':min(1.0,max(0.0,repaid/born)),'passiveRepaidQty':float(ob.get('passiveRepaidQty') or 0.0),
                'activeRepaidQty':float(ob.get('activeRepaidQty') or 0.0),'debt':debt,'reservedRepairQuota':reserved,
                'availableExpandCredit':credit,'freeSlotsBeforeOrdinary':free,'spareSlotAfterOrdinary':free>=2,
                'liveRepairCarrierCount':len(repairs),'liveRepairCarriers':repairs,'activeLiveCount':len(getattr(self,'activeKeys',set())),
                'exclusiveComposite':ex,'sharedParentComposite':sh}
            self.r302Events.append(ev);self.r302['OPPORTUNITIES']+=1
            if ev['riskRepaidFrac']<=EPS:self.r302['ZERO_REPAIR_PROGRESS']+=1
            elif ev['riskRepaidFrac']<1.0-EPS:self.r302['PARTIAL_REPAIR_PROGRESS']+=1
            if len(repairs)==0:self.r302['ZERO_REPAIR_SIBLING']+=1
            elif len(repairs)==1:self.r302['SINGLE_REPAIR_SIBLING']+=1
            else:self.r302['MULTI_REPAIR_SIBLING']+=1
            if free>=2:self.r302['SPARE_AFTER_ORDINARY']+=1
            if ex is not None:self.r302['EXCLUSIVE_COMPOSITE_REACHABLE']+=1
            if sh is not None:self.r302['SHARED_COMPOSITE_REACHABLE']+=1
            if ex is not None and free>=2:self.r302['EXCLUSIVE_PLUS_SPARE']+=1
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}';self.r302['THESIS_BIRTH']+=1
        return ok

    def run_r302(self,winner):
        r=super().run_r264(winner)
        r.update({'r302Version':'MS4_R3_02_OBLIGATION_LIVE_COMPETITIVE_CONTINUATION_SHADOW_V1','r302Stats':dict(self.r302),
                  'r302Events':self.r302Events[:8000],'r302IntentThesisSide':self.intentThesisSide})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r302_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            w=co[m]['winner'];s=ObligationLiveCompetitiveContinuationShadow(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_r302(w)
            finally:s.close()
            st=r.get('r302Stats',{});row={'marketId':m,'winnerPostHocOnly':w,'pnlPostHocOnly':float(r['pnlDiagnosticOnly']),
                'best':float(r['best']),'floor':float(r['floor']),'thesis':r.get('r302IntentThesisSide'),'correct':bool(r.get('r264CorrectnessPass')),
                'opportunities':int(st.get('OPPORTUNITIES',0)),'zeroProgress':int(st.get('ZERO_REPAIR_PROGRESS',0)),
                'partialProgress':int(st.get('PARTIAL_REPAIR_PROGRESS',0)),'zeroSibling':int(st.get('ZERO_REPAIR_SIBLING',0)),
                'singleSibling':int(st.get('SINGLE_REPAIR_SIBLING',0)),'multiSibling':int(st.get('MULTI_REPAIR_SIBLING',0)),
                'spareAfterOrdinary':int(st.get('SPARE_AFTER_ORDINARY',0)),'exclusiveReachable':int(st.get('EXCLUSIVE_COMPOSITE_REACHABLE',0)),
                'sharedReachable':int(st.get('SHARED_COMPOSITE_REACHABLE',0)),'exclusivePlusSpare':int(st.get('EXCLUSIVE_PLUS_SPARE',0)),
                'events':r.get('r302Events',[])}
            rows.append(row);print(json.dumps({k:row[k] for k in ['marketId','opportunities','zeroProgress','partialProgress','zeroSibling','singleSibling','multiSibling','spareAfterOrdinary','exclusiveReachable','sharedReachable','exclusivePlusSpare','correct']},ensure_ascii=False),flush=True)
        structural=[r['marketId'] for r in rows if r['opportunities']>0]
        clean=[r['marketId'] for r in rows if r['exclusivePlusSpare']>0]
        out={'version':'MS4_R3_02_OBLIGATION_LIVE_COMPETITIVE_CONTINUATION_SHADOW_V1','researchOnly':True,'behaviorChange':False,
             'markets':mids,'rows':rows,'summary':{'marketCount':len(rows),'opportunityMarkets':structural,'cleanExclusiveSpareMarkets':clean,
                'totalOpportunities':sum(r['opportunities'] for r in rows),'zeroProgress':sum(r['zeroProgress'] for r in rows),
                'partialProgress':sum(r['partialProgress'] for r in rows),'zeroSibling':sum(r['zeroSibling'] for r in rows),
                'singleSibling':sum(r['singleSibling'] for r in rows),'multiSibling':sum(r['multiSibling'] for r in rows),
                'spareAfterOrdinary':sum(r['spareAfterOrdinary'] for r in rows),'exclusiveReachable':sum(r['exclusiveReachable'] for r in rows),
                'sharedReachable':sum(r['sharedReachable'] for r in rows),'exclusivePlusSpare':sum(r['exclusivePlusSpare'] for r in rows)},
             'boundary':['R2.64 exact behavior unchanged','trigger requires realized risk obligation still outstanding','R2.47 favorable replenishment excluded','ordinary off-thesis Expand remains untouched','persistent intent thesis diagnostic only','exclusive geometry uses current frozen V8 split','shared-parent geometry is behavior-inert hypothetical only','winner/PnL posthoc only','consumed full24','no threshold fitting/no fresh/no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
