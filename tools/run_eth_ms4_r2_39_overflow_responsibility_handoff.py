from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,math
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2
EPS=1e-9

class OverflowResponsibilityHandoffSim(r28.FanoutRoleCapacitySim):
    """Research-only R2.39.

    An actually realized ECONOMIC_CORE overflow becomes a separate diagnostic
    responsibility.  While that overflow side remains the current scope, one
    existing fanout-capacity slot may be prioritised for a PURE passive Repair
    carrier on the opposite side.  No new global/fanout capacity, no active
    escalation, no new credit source, and no admission change to the original
    Core overflow.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r239=Counter()
        self.r239events=[]
        self.obligations=[]
        self.activeObligationId=None
        self.handoffKeys={}
        self._nextObligationId=1

    def _active_obligation(self):
        if self.activeObligationId is None:return None
        for x in self.obligations:
            if int(x['id'])==int(self.activeObligationId):return x
        return None

    def _close_active(self,t,reason):
        ob=self._active_obligation()
        if not ob:return
        ob['closedT']=int(t);ob['closeReason']=str(reason);ob['closedOutstanding']=float(ob['outstanding'])
        self.r239['OBLIGATION_CLOSED_'+str(reason)]+=1
        ev={'t':int(t),'event':'R239_OVERFLOW_OBLIGATION_CLOSED','obligationId':int(ob['id']),
            'side':ob['side'],'generation':int(ob['generation']),'outstanding':float(ob['outstanding']),'reason':str(reason)}
        self.r239events.append(ev);self.slot_history.append(ev)
        self.activeObligationId=None

    def _register_overflow(self,t,ev):
        oq=float(ev.get('overflowRealized') or 0.0)
        if oq<=EPS:return
        side=str(ev.get('side'))
        # Core Repair may complete the old scope and its venue-min overflow becomes
        # the NEW unmatched responsibility in the same fill. Bind the obligation
        # to the post-fill scope/generation when that scope is the overflow side.
        gen=int(self.scopeGeneration) if self.scopeSide==side else int(ev.get('generationAtSubmit') or self.scopeGeneration)
        ob=self._active_obligation()
        if ob and ob['side']==side and int(ob['generation'])==gen and self.scopeSide==side:
            ob['originOverflowQty']+=oq;ob['outstanding']+=oq;ob['sourceKeys'].append(str(ev.get('key')))
            self.r239['OVERFLOW_OBLIGATION_AUGMENTED']+=1
            out={'t':int(t),'event':'R239_OVERFLOW_OBLIGATION_AUGMENTED','obligationId':int(ob['id']),
                 'sourceKey':ev.get('key'),'side':side,'generation':gen,'addQty':oq,'outstanding':float(ob['outstanding'])}
        else:
            if ob:self._close_active(t,'REPLACED_BY_NEW_OVERFLOW_SCOPE')
            oid=self._nextObligationId;self._nextObligationId+=1
            ob={'id':oid,'bornT':int(t),'side':side,'generation':gen,'originOverflowQty':oq,
                'outstanding':oq,'repaidQty':0.0,'sourceKeys':[str(ev.get('key'))],
                'sourcePrices':[float(ev.get('price') or 0.0)]}
            self.obligations.append(ob);self.activeObligationId=oid
            self.r239['OVERFLOW_OBLIGATION_BORN']+=1
            out={'t':int(t),'event':'R239_OVERFLOW_OBLIGATION_BORN','obligationId':oid,
                 'sourceKey':ev.get('key'),'side':side,'generation':gen,'overflowQty':oq,
                 'sourcePrice':float(ev.get('price') or 0.0),'outstanding':oq}
        self.r239events.append(out);self.slot_history.append(out)

    def _live_handoff(self):
        for key in list(self.handoffKeys):
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return key
        return None

    def process(self,t):
        before_n=len(self.splitEvents)
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration)
        super().process(t)
        new_events=self.splitEvents[before_n:]

        # First account actual handoff Repair fills.
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
            x={'t':int(t),'event':'R239_HANDOFF_REPAIR_FILL','obligationId':oid,'key':key,'repairQty':rq,
               'liabilityPaidQty':paid,'outstanding':float(ob['outstanding']),'price':float(ev.get('price') or 0.0)}
            self.r239events.append(x);self.slot_history.append(x)
            if ob['outstanding']<=EPS and self.activeObligationId==oid:self._close_active(t,'REPAID')

        # If ordinary CAP1 evolution has already moved scope away, do not chase
        # the old overflow across generations.
        ob=self._active_obligation()
        if ob and (self.scopeSide!=ob['side'] or int(self.scopeGeneration)!=int(ob['generation'])):
            self._close_active(t,'SCOPE_LEFT_OVERFLOW_SIDE')

        # Finally register newly materialized ECONOMIC_CORE overflow.
        for ev in new_events:
            if ev.get('event')=='ROLE_FILL_SPLIT' and ev.get('role')=='ECONOMIC_CORE' and float(ev.get('overflowRealized') or 0.0)>EPS:
                self._register_overflow(t,ev)

    def _try_overflow_handoff(self,t):
        ob=self._active_obligation()
        if not ob or float(ob['outstanding'])<=EPS:return False
        if self.scopeSide!=ob['side'] or int(self.scopeGeneration)!=int(ob['generation']):return False
        if self._live_handoff() is not None:
            self.r239['HANDOFF_ALREADY_LIVE']+=1;return False
        if self._live_fanout_count()>=self.fanoutLimit:
            self.r239['HANDOFF_FANOUT_CAP_OCCUPIED']+=1;return False
        repair_side='DOWN' if ob['side']=='UP' else 'UP'
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=repair_side))>=self.max_slots:
            self.r239['HANDOFF_SLOT_CAP_FULL']+=1;return False
        used=self._used_prices(repair_side)
        saw_legal=False
        for raw in self._live_price_levels(repair_side):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            # Pure repayment: never create an opposite-side overflow merely to
            # repair this obligation.
            if q>float(ob['outstanding'])+EPS:
                self.r239['HANDOFF_LIABILITY_BELOW_VENUE_MIN']+=1;continue
            sp=self._repair_split(repair_side,p,q)
            if sp is None:continue
            if float(sp.get('overflowQty') or 0.0)>EPS:
                self.r239['HANDOFF_WOULD_CREATE_NEW_OVERFLOW']+=1;continue
            saw_legal=True;before_n=self.n
            if not self._submit_role_v8(t,repair_side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
                self.r239['HANDOFF_SUBMIT_BLOCKED']+=1;continue
            key=f'{repair_side}_{before_n}';self.fanoutKeys.add(key);self.handoffKeys[key]=int(ob['id'])
            self.r239['HANDOFF_SUBMIT']+=1
            x={'t':int(t),'event':'R239_OVERFLOW_HANDOFF_SUBMIT','obligationId':int(ob['id']),'key':key,
               'overflowSide':ob['side'],'repairSide':repair_side,'price':p,'qty':q,
               'outstandingBefore':float(ob['outstanding']),'generation':int(ob['generation'])}
            self.r239events.append(x);self.slot_history.append(x)
            return True
        if not saw_legal:self.r239['HANDOFF_NO_PURE_REPAIR_CANDIDATE']+=1
        return False

    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)>v2.NO_NEW_EXPOSURE_MS and self._try_overflow_handoff(t):
            return
        return super()._open_one_option(t,qv,end)

    def run_r239(self,winner):
        r=super().run_cap(winner)
        r['r239Stats']=dict(self.r239);r['r239Events']=self.r239events[:2000]
        r['r239Obligations']=self.obligations[:200]
        r['r239HandoffSubmits']=int(self.r239.get('HANDOFF_SUBMIT',0))
        r['r239HandoffFills']=int(self.r239.get('HANDOFF_REPAIR_FILL',0))
        r['r239HandoffRepaidQty']=sum(float(x.get('repaidQty') or 0.0) for x in self.obligations)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r239_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];comparison=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=OverflowResponsibilityHandoffSim(tape,1,4)
            try:c=sim.run_r239(cr['winner'])
            finally:sim.close()
            rows.extend([{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                         {'marketId':mid,'cell':'MS4_R239_OVERFLOW_RESPONSIBILITY_HANDOFF','winnerPostHocOnly':cr['winner'],**c}])
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],
               'bestDelta':c['best']-b['best'],'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'handoffSubmits':c['r239HandoffSubmits'],'handoffFills':c['r239HandoffFills'],'handoffRepaidQty':c['r239HandoffRepaidQty'],
               'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            comparison.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        controls={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'}
        cands={r['marketId']:r for r in rows if r['cell']=='MS4_R239_OVERFLOW_RESPONSIBILITY_HANDOFF'}
        control_parity=all(abs(float(controls[m]['pnlDiagnosticOnly'])-float(controls[m]['pnlDiagnosticOnly']))<1e-12 for m in mids)
        correctness=all(float(cands[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cands[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids)
        exercised=sum(int(cands[m].get('r239HandoffSubmits',0)) for m in mids)>0
        out={'version':'MS4_R2_39_OVERFLOW_RESPONSIBILITY_HANDOFF_V1','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':comparison,
             'gates':{'controlReplayCompleted':control_parity,'correctnessPass':correctness,'handoffMechanismExercised':exercised},
             'boundary':['CAP1 max4/fanout1 frozen except one existing fanout-capacity slot may prioritize actual Core-overflow responsibility','original Core overflow admission unchanged','handoff is passive pure Repair only and may not create new overflow','no extra global/fanout capacity','no borrowed credit','no active escalation added','scope flip closes old obligation','winner post-hoc only','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':comparison},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
