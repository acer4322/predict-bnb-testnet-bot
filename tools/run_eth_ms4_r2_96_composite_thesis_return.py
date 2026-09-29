from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2; EPS=1e-9

class CompositeThesisReturnSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    """R2.96 research-only.

    Preserve exact R2.64 unless an ordinary post-repaid off-thesis Expand can be
    replaced by a TRUE thesis-side composite carrier. The replacement is legal
    only when frozen V8 split accounting assigns both Repair qty and confirmed-
    fill-authorized overflow qty. If no such candidate can be submitted, the
    original R2.64 action proceeds unchanged.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.intentThesisSide=None;self.intentThesisBornAt=None;self.intentThesisSourceKey=None
        self.r296=Counter();self.r296Events=[];self.r296CompositeKeys=set();self.r296CompositeMeta={}
        self._r296FavorableContext=False;self._r296CompositeContext=False
        super().__init__(tape,fanout_limit,max_slots)

    def _try_favorable_replenishment(self,t,end):
        self._r296FavorableContext=True
        try:return super()._try_favorable_replenishment(t,end)
        finally:self._r296FavorableContext=False

    def _post_repaid_off_thesis_opportunity(self,side,role):
        if self._r296FavorableContext or self._r296CompositeContext:return False
        if role!='SATELLITE_EXPAND' or self.intentThesisSide not in {'UP','DOWN'}:return False
        if str(side)==str(self.intentThesisSide):return False
        ob=self.riskRepairObligations.get(int(self.scopeGeneration))
        return bool(ob is not None and str(ob.get('closeReason'))=='REPAID')

    def _composite_role(self,side):
        if self.scopeSide is None or side!=self._repair_side():return None
        if self._core_for_side(side) is None:return 'ECONOMIC_CORE'
        if self._live_fanout_count()<self.fanoutLimit:return 'SATELLITE_REPAIR'
        return None

    def _try_composite_thesis_return(self,t):
        thesis=self.intentThesisSide
        if thesis not in {'UP','DOWN'} or self.scopeSide is None:return False
        if thesis!=self._repair_side():
            self.r296['COMPOSITE_NOT_CURRENT_REPAIR_SIDE']+=1;return False
        if self._has_stale_scope_reservation():
            self.r296['COMPOSITE_STALE_SCOPE_BLOCK']+=1;return False
        if len(self.slot_key)>=self.max_slots:
            self.r296['COMPOSITE_GLOBAL_SLOT_BLOCK']+=1;return False
        role=self._composite_role(thesis)
        if role is None:
            self.r296['COMPOSITE_ROLE_CAPACITY_BLOCK']+=1;return False
        used=self._used_prices(thesis);chosen=None
        for raw in self._live_price_levels(thesis):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            sp=self._repair_split(thesis,p,q)
            if sp is None:continue
            if float(sp.get('repairQty') or 0.0)<=EPS:continue
            if float(sp.get('overflowQty') or 0.0)<=EPS:
                self.r296['COMPOSITE_PURE_REPAIR_CANDIDATE_SKIPPED']+=1;continue
            chosen=(p,q,sp);break
        if chosen is None:
            self.r296['COMPOSITE_NO_TRUE_CROSSING_CANDIDATE']+=1;return False
        p,q,sp=chosen;before_n=self.n
        self._r296CompositeContext=True
        try:ok=super()._submit_role_v8(t,thesis,role,p,q,float(sp['fullFloor']),sp)
        finally:self._r296CompositeContext=False
        if not ok:
            self.r296['COMPOSITE_SUBMIT_BLOCKED']+=1;return False
        key=f'{thesis}_{before_n}'
        self.r296CompositeKeys.add(key)
        self.r296CompositeMeta[key]={'key':key,'submittedAt':int(t),'generation':int(self.key_scope_gen.get(key,self.scopeGeneration)),
            'thesisSide':thesis,'scopeSideAtSubmit':self.scopeSide,'role':role,'price':float(p),'qty':float(q),
            'repairQtyAuthorized':float(sp['repairQty']),'overflowQtyAuthorized':float(sp['overflowQty']),
            'overflowRiskAuthorized':float(sp['overflowRisk'])}
        if role=='SATELLITE_REPAIR':self.fanoutKeys.add(key)
        self.r296['COMPOSITE_SUBMIT']+=1
        ev={'t':int(t),'event':'R296_COMPOSITE_THESIS_RETURN_SUBMIT',**self.r296CompositeMeta[key],
            'availableCreditAfter':float(self._available_expand_risk_credit())}
        self.r296Events.append(ev);self.slot_history.append(ev)
        return True

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if self._post_repaid_off_thesis_opportunity(side,role):
            self.r296['POST_REPAID_OFF_THESIS_OPPORTUNITY']+=1
            if self._try_composite_thesis_return(t):
                self.r296['POST_REPAID_COMPOSITE_SUBSTITUTION']+=1
                return True
            self.r296['POST_REPAID_BASELINE_FALLBACK']+=1
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side);self.intentThesisBornAt=int(t);self.intentThesisSourceKey=f'{side}_{before_n}'
            ev={'t':int(t),'event':'R296_INTENT_THESIS_BIRTH','side':self.intentThesisSide,'sourceKey':self.intentThesisSourceKey,
                'source':'FIRST_SUCCESSFUL_PROBE_CORE_SUBMIT'}
            self.r296Events.append(ev);self.slot_history.append(ev);self.r296['THESIS_BIRTH']+=1
        return ok

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.r296CompositeKeys:continue
            inc=float(ev.get('fillInc') or 0.0)
            if inc<=EPS:continue
            rq=float(ev.get('repairAllocated') or 0.0);oq=float(ev.get('overflowRealized') or 0.0)
            self.r296['COMPOSITE_FILL']+=1;self.r296['COMPOSITE_FILL_QTY_MILLI']+=int(round(inc*1000))
            self.r296['COMPOSITE_REPAIR_QTY_MILLI']+=int(round(rq*1000));self.r296['COMPOSITE_OVERFLOW_QTY_MILLI']+=int(round(oq*1000))
            x={'t':int(t),'event':'R296_COMPOSITE_THESIS_RETURN_FILL','key':key,'generationAtSubmit':ev.get('generationAtSubmit'),
               'side':ev.get('side'),'price':float(ev.get('price') or 0.0),'fillQty':inc,'repairAllocated':rq,
               'thesisOverflowRealized':oq,'overflowRisk':float(ev.get('overflowRisk') or 0.0),
               'scopeSideAfter':self.scopeSide,'scopeGenerationAfter':int(self.scopeGeneration)}
            self.r296Events.append(x);self.slot_history.append(x)
            if oq>EPS:self.r296['COMPOSITE_CONFIRMED_THESIS_OVERFLOW']+=1

    def run_r296(self,winner):
        r=super().run_r264(winner)
        correct=(bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS
                 and float(r.get('repairQuotaExcessMax',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices',0))<=self.max_slots)
        r.update({'r296Version':'MS4_R2_96_COMPOSITE_THESIS_RETURN_V1','r296Stats':dict(self.r296),
            'r296Events':self.r296Events[:6000],'r296IntentThesisSide':self.intentThesisSide,
            'r296IntentThesisBornAt':self.intentThesisBornAt,'r296IntentThesisSourceKey':self.intentThesisSourceKey,
            'r296CompositeKeys':sorted(self.r296CompositeKeys),'r296CompositeMeta':list(self.r296CompositeMeta.values())[:200],
            'r296CompositeSubmits':int(self.r296.get('COMPOSITE_SUBMIT',0)),'r296CompositeFills':int(self.r296.get('COMPOSITE_FILL',0)),
            'r296CompositeRepairQty':float(self.r296.get('COMPOSITE_REPAIR_QTY_MILLI',0))/1000.0,
            'r296CompositeOverflowQty':float(self.r296.get('COMPOSITE_OVERFLOW_QTY_MILLI',0))/1000.0,
            'r296BaselineFallbacks':int(self.r296.get('POST_REPAID_BASELINE_FALLBACK',0)),'r296CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r296_'))
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
            s=CompositeThesisReturnSim(tape,1,4)
            try:r=s.run_r296(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R296_COMPOSITE_THESIS_RETURN','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r296IntentThesisSide'),'thesisWinnerAligned':r.get('r296IntentThesisSide')==w,
               'opportunities':int(r.get('r296Stats',{}).get('POST_REPAID_OFF_THESIS_OPPORTUNITY',0)),
               'compositeSubmits':int(r.get('r296CompositeSubmits',0)),'compositeFills':int(r.get('r296CompositeFills',0)),
               'compositeRepairQty':float(r.get('r296CompositeRepairQty',0.0)),'compositeOverflowQty':float(r.get('r296CompositeOverflowQty',0.0)),
               'baselineFallbacks':int(r.get('r296BaselineFallbacks',0)),
               'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),
               'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),
               'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),
               'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),
               'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,
               'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'riskFills':int(r.get('riskTrancheFillEvents',0)),
               'r263Fills':int(r.get('r263Fills',0)),'replenishmentFills':int(r.get('r247FavorableReplenishmentFills',0)),
               'correct':bool(r.get('r296CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        c1946784=[x for x in cmp if x['marketId']==1946784]
        out={'version':'MS4_R2_96_COMPOSITE_THESIS_RETURN_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'compositeSubmitExercised':any(x['compositeSubmits']>0 for x in cmp),
                      'compositeFillExercised':any(x['compositeFills']>0 for x in cmp),'confirmedThesisOverflowExercised':any(x['compositeOverflowQty']>EPS for x in cmp),
                      'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp),
                      'noEffect1946784':all(abs(x[k])<=1e-9 for x in c1946784 for k in ['pnlDelta','bestDelta','floorDelta','gapDelta','fillDelta','submitDelta']) if c1946784 else None},
             'boundary':['R2.64 frozen unless a true composite substitution is available','first successful PROBE_CORE submit births research persistent intent thesis','trigger only ordinary post-repaid off-thesis SATELLITE_EXPAND','R2.47 favorable replenishment exempt','candidate must allocate both Repair and overflow under frozen V8 accounting','confirmed fill Repair-first; confirmed overflow only is thesis exposure','if no true composite candidate or submit fails, exact baseline off-thesis Expand is allowed','Passive and inherited Active frozen','no winner/Target/future runtime input','no new slot/fanout capacity','max4','<=180s unchanged','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'bestDelta':sum(x['bestDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'fillDelta':sum(x['fillDelta'] for x in cmp)}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
