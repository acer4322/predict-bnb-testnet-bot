from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_96_composite_thesis_return as r296
r264=r296.r264;v2=r296.v2;EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class SingleCarrierCompositeHandoffSim(r296.CompositeThesisReturnSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.r299=Counter();self.r299Events=[];self.r299Pending=None;self.r299UsedGenerations=set();self.r299Keys=set()
        super().__init__(tape,fanout_limit,max_slots)

    def _current_live_repair_rows(self):
        out=[]
        if self.scopeSide is None:return out
        rs=self._repair_side();gen=int(self.scopeGeneration)
        for sid,key,o,role in self._live_role_rows(side=rs):
            if role not in REPAIR_ROLES or int(self.key_scope_gen.get(key,-1))!=gen:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st in v2.TERMINAL_STATUSES:continue
            out.append((int(sid),key,o,role))
        return out

    def _is_post_repaid_off_thesis(self,side,role):
        if self._r296FavorableContext or self._r296CompositeContext:return False
        if role!='SATELLITE_EXPAND' or self.intentThesisSide not in {'UP','DOWN'}:return False
        if str(side)==str(self.intentThesisSide):return False
        ob=self.riskRepairObligations.get(int(self.scopeGeneration))
        return bool(ob and str(ob.get('closeReason'))=='REPAID')

    def _hypothetical_after_cancel(self,source_row):
        sid,key,o,role=source_row;thesis=self.intentThesisSide
        debt=max(0.0,float(self._scope_debt_qty()));reserved=max(0.0,float(self._reserved_repair_quota(thesis)))
        freed=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)));reserved_after=max(0.0,reserved-freed)
        available=max(0.0,debt-reserved_after);credit=max(0.0,float(self._available_expand_risk_credit()));floor0=float(self._physical_floor())
        used=self._used_prices(thesis);used.discard(float(o['price']))
        for raw in self._live_price_levels(thesis):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            venue=1.0/p;q=available+venue
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            rf=float(self._candidate_alone_floor(thesis,p,available)) if available>EPS else floor0
            ff=float(self._candidate_alone_floor(thesis,p,q));risk=max(0.0,rf-ff)
            if available<=EPS or rf<=floor0+EPS or credit+EPS<risk:continue
            if reserved_after>EPS:continue
            return {'sourceSlot':sid,'sourceKey':key,'sourceRole':role,'sourcePrice':float(o['price']),'sourceRepairQuota':freed,
                    'debtAtTrigger':debt,'reservedAtTrigger':reserved,'reservedAfterHypCancel':reserved_after,
                    'repairQtyIfMaterializedNow':available,'thesisPriceAtTrigger':p,'venueMinOverflowAtTrigger':venue,
                    'physicalQtyAtTrigger':q,'overflowRiskAtTrigger':risk,'creditAtTrigger':credit}
        return None

    def _start_handoff(self,t,orig_side,orig_p,orig_q):
        gen=int(self.scopeGeneration)
        if self.r299Pending is not None or gen in self.r299UsedGenerations:return False
        rows=self._current_live_repair_rows()
        if len(rows)!=1:
            self.r299['TRIGGER_NOT_SINGLE_REPAIR_CARRIER']+=1;return False
        hyp=self._hypothetical_after_cancel(rows[0])
        if hyp is None:
            self.r299['TRIGGER_NO_REPLACEMENT_GEOMETRY']+=1;return False
        sid,key,o,role=rows[0]
        if not self._request_cancel(int(t),sid,'R299_COMPOSITE_HANDOFF'):
            self.r299['SOURCE_CANCEL_REQUEST_FAILED']+=1;return False
        self.r299UsedGenerations.add(gen)
        self.r299Pending={'generation':gen,'scopeSide':self.scopeSide,'thesisSide':self.intentThesisSide,'sourceKey':key,'sourceSlot':sid,
            'requestedAt':int(t),'originalExpandSide':str(orig_side),'originalExpandPrice':float(orig_p),'originalExpandQty':float(orig_q),
            **hyp,'terminalObserved':False}
        self.r299['HANDOFF_CANCEL_REQUEST']+=1
        ev={'t':int(t),'event':'R299_HANDOFF_CANCEL_REQUEST',**self.r299Pending}
        self.r299Events.append(ev);self.slot_history.append(ev);return True

    def _source_terminal(self,p):
        o=self.orders.get(str(p['sourceKey']))
        if not o:return True,'MISSING'
        try:st=str(self.snap(o).get('status') or '').upper()
        except Exception:return False,'UNKNOWN'
        return st in v2.TERMINAL_STATUSES,st

    def _try_materialize_pending(self,t,end):
        p=self.r299Pending
        if not p:return False
        terminal,status=self._source_terminal(p)
        if not terminal:
            self.r299['WAIT_SOURCE_TERMINAL']+=1;return False
        if not p.get('terminalObserved'):
            p['terminalObserved']=True;p['sourceTerminalStatus']=status;p['sourceTerminalAt']=int(t)
            self.r299['SOURCE_TERMINAL']+=1
            self.r299Events.append({'t':int(t),'event':'R299_SOURCE_TERMINAL','sourceKey':p['sourceKey'],'status':status,'generation':p['generation']})
        if self.scopeSide!=p['scopeSide'] or int(self.scopeGeneration)!=int(p['generation']):
            self.r299['ABANDON_SCOPE_CHANGED']+=1;self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'SCOPE_CHANGED',**p});self.r299Pending=None;return False
        if self.intentThesisSide!=p['thesisSide'] or self.intentThesisSide!=self._repair_side():
            self.r299['ABANDON_THESIS_NOT_REPAIR_SIDE']+=1;self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'THESIS_NOT_REPAIR_SIDE',**p});self.r299Pending=None;return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self.r299['ABANDON_LATE_180S']+=1;self.r299Pending=None;return False
        if self._has_stale_scope_reservation():
            self.r299['WAIT_STALE_SCOPE_RESERVATION']+=1;return False
        if len(self.slot_key)>=self.max_slots:
            self.r299['WAIT_GLOBAL_SLOT']+=1;return False
        thesis=self.intentThesisSide;role=self._composite_role(thesis)
        if role is None:
            self.r299['WAIT_ROLE_CAPACITY']+=1;return False
        debt=max(0.0,float(self._scope_debt_qty()));reserved=max(0.0,float(self._reserved_repair_quota(thesis)));available=max(0.0,debt-reserved)
        if available<=EPS:
            self.r299['ABANDON_NO_UNRESERVED_DEBT']+=1;self.r299Pending=None;return False
        used=self._used_prices(thesis);chosen=None
        for raw in self._live_price_levels(thesis):
            px=float(v2.kprice(raw))
            if px in used or px<=EPS:continue
            venue=1.0/px;q=available+venue
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            sp=self._repair_split(thesis,px,q)
            if sp is None:continue
            if float(sp.get('repairQty') or 0)<=EPS or float(sp.get('overflowQty') or 0)<=EPS:continue
            chosen=(px,q,sp,venue,debt,reserved,available);break
        if chosen is None:
            self.r299['ABANDON_NO_COMPOSITE_AFTER_CANCEL']+=1;self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'NO_COMPOSITE_AFTER_CANCEL',**p});self.r299Pending=None;return False
        px,q,sp,venue,debt,reserved,available=chosen;before_n=self.n
        self._r296CompositeContext=True
        try:ok=r264.ExecutionRepresentedPreRepairReexpandSim._submit_role_v8(self,t,thesis,role,px,q,float(sp['fullFloor']),sp)
        finally:self._r296CompositeContext=False
        if not ok:
            self.r299['COMPOSITE_SUBMIT_BLOCKED_AFTER_CANCEL']+=1;return False
        key=f'{thesis}_{before_n}'
        self.r299Keys.add(key);self.r296CompositeKeys.add(key)
        meta={'key':key,'submittedAt':int(t),'generation':int(self.scopeGeneration),'thesisSide':thesis,'scopeSideAtSubmit':self.scopeSide,
              'role':role,'price':float(px),'qty':float(q),'repairQtyAuthorized':float(sp['repairQty']),'overflowQtyAuthorized':float(sp['overflowQty']),
              'overflowRiskAuthorized':float(sp['overflowRisk']),'structuralAvailableDebt':available,'structuralVenueMinOverflowQty':venue,
              'sourceKey':p['sourceKey'],'sourceTerminalStatus':status,'cancelRequestedAt':p['requestedAt']}
        self.r296CompositeMeta[key]=meta
        if role=='SATELLITE_REPAIR':self.fanoutKeys.add(key)
        self.r296['COMPOSITE_SUBMIT']+=1;self.r299['COMPOSITE_SUBMIT_AFTER_HANDOFF']+=1
        self.r299Events.append({'t':int(t),'event':'R299_COMPOSITE_SUBMIT_AFTER_HANDOFF',**meta});self.slot_history.append({'t':int(t),'event':'R299_COMPOSITE_SUBMIT_AFTER_HANDOFF',**meta})
        self.r299Pending=None;return True

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if self._is_post_repaid_off_thesis(side,role):
            gen=int(self.scopeGeneration)
            if self.r299Pending is not None:
                self.r299['OFF_THESIS_SUPPRESSED_WHILE_HANDOFF_PENDING']+=1;return False
            if gen not in self.r299UsedGenerations and self._start_handoff(t,side,p,q):
                self.r299['TRIGGERING_OFF_THESIS_SUBSTITUTED']+=1;return False
        before_n=self.n
        ok=r264.ExecutionRepresentedPreRepairReexpandSim._submit_role_v8(self,t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
            self.r299['THESIS_BIRTH']+=1;self.r299Events.append({'t':int(t),'event':'R299_INTENT_THESIS_BIRTH','side':self.intentThesisSide,'sourceKey':self.intentThesisSourceKey})
        return ok

    def _open_one_option(self,t,qv,end):
        if self._try_materialize_pending(t,end):return
        return super(r296.CompositeThesisReturnSim,self)._open_one_option(t,qv,end)

    def process(self,t):
        before=int(self.r296.get('COMPOSITE_FILL',0))
        super().process(t)
        if int(self.r296.get('COMPOSITE_FILL',0))>before:
            for e in reversed(self.r296Events):
                if e.get('event')=='R296_COMPOSITE_THESIS_RETURN_FILL' and str(e.get('key')) in self.r299Keys and int(e.get('t') or -1)==int(t):
                    self.r299['COMPOSITE_FILL']+=1
                    self.r299['COMPOSITE_REPAIR_QTY_MILLI']+=int(round(float(e.get('repairAllocated') or 0)*1000))
                    self.r299['COMPOSITE_OVERFLOW_QTY_MILLI']+=int(round(float(e.get('thesisOverflowRealized') or 0)*1000))
                    self.r299Events.append({'event':'R299_COMPOSITE_FILL',**dict(e)});break

    def run_r299(self,winner):
        r=super().run_r296(winner)
        correct=bool(r.get('r296CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0))<=EPS and float(r.get('repairQuotaExcessMax',0))<=EPS
        r.update({'r299Version':'MS4_R2_99_SINGLE_CARRIER_COMPOSITE_HANDOFF_V1','r299Stats':dict(self.r299),'r299Events':self.r299Events[:6000],
                  'r299PendingEnd':self.r299Pending,'r299UsedGenerations':sorted(self.r299UsedGenerations),'r299Keys':sorted(self.r299Keys),
                  'r299CancelRequests':int(self.r299.get('HANDOFF_CANCEL_REQUEST',0)),'r299CompositeSubmits':int(self.r299.get('COMPOSITE_SUBMIT_AFTER_HANDOFF',0)),
                  'r299CompositeFills':int(self.r299.get('COMPOSITE_FILL',0)),'r299CompositeRepairQty':float(self.r299.get('COMPOSITE_REPAIR_QTY_MILLI',0))/1000.0,
                  'r299CompositeOverflowQty':float(self.r299.get('COMPOSITE_OVERFLOW_QTY_MILLI',0))/1000.0,'r299CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r299_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            s=SingleCarrierCompositeHandoffSim(tape,1,4)
            try:r=s.run_r299(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R299_SINGLE_CARRIER_COMPOSITE_HANDOFF','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r296IntentThesisSide'),'thesisWinnerAligned':r.get('r296IntentThesisSide')==w,
               'cancelRequests':int(r.get('r299CancelRequests',0)),'compositeSubmits':int(r.get('r299CompositeSubmits',0)),'compositeFills':int(r.get('r299CompositeFills',0)),
               'compositeRepairQty':float(r.get('r299CompositeRepairQty',0)),'compositeOverflowQty':float(r.get('r299CompositeOverflowQty',0)),
               'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),
               'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),
               'submitDelta':int(r['submits'])-int(br['submits']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),
               'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),
               'correct':bool(r.get('r299CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        ctrl=[x for x in cmp if x['marketId']==1946784]
        out={'version':'MS4_R2_99_SINGLE_CARRIER_COMPOSITE_HANDOFF_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'cancelExercised':any(x['cancelRequests']>0 for x in cmp),'compositeSubmitExercised':any(x['compositeSubmits']>0 for x in cmp),
                      'compositeFillExercised':any(x['compositeFills']>0 for x in cmp),'confirmedOverflowExercised':any(x['compositeOverflowQty']>EPS for x in cmp),
                      'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp),'noEffect1946784':all(abs(x[k])<=1e-9 for x in ctrl for k in ['pnlDelta','bestDelta','floorDelta','gapDelta','fillDelta','submitDelta']) if ctrl else None},
             'boundary':['conservative single-live-Repair handoff only','cancel must be terminal before composite submit','no pending cancel counts as Repair/protection/credit','physical qty = current unreserved debt + one venue-min thesis overflow','frozen V8 Repair-first/overflow-second split','one handoff attempt per generation','R2.47 favorable replenishment exempt','inherited Passive/Active remain operational','max4/fanout1','<=180s unchanged','no winner/Target/future runtime input','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'bestDelta':sum(x['bestDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'fillDelta':sum(x['fillDelta'] for x in cmp)}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
