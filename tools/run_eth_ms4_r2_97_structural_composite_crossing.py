from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_96_composite_thesis_return as r296
r264=r296.r264; v2=r296.v2; EPS=1e-9

class StructuralCompositeCrossingSim(r296.CompositeThesisReturnSim):
    """R2.97: same R2.96 trigger, structural crossing quantity.

    Physical carrier qty = currently unreserved Repair debt + one venue-min
    thesis overflow tranche. No result-fitted multiplier is used. Frozen V8
    split/accounting remains authoritative; if this true composite cannot be
    legally submitted, R2.96 interception falls back to exact R2.64 behavior.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.r297=Counter();self.r297Events=[]
        super().__init__(tape,fanout_limit,max_slots)

    def _try_composite_thesis_return(self,t):
        thesis=self.intentThesisSide
        if thesis not in {'UP','DOWN'} or self.scopeSide is None:return False
        if thesis!=self._repair_side():
            self.r296['COMPOSITE_NOT_CURRENT_REPAIR_SIDE']+=1;self.r297['NOT_CURRENT_REPAIR_SIDE']+=1;return False
        if self._has_stale_scope_reservation():
            self.r296['COMPOSITE_STALE_SCOPE_BLOCK']+=1;self.r297['STALE_SCOPE_BLOCK']+=1;return False
        if len(self.slot_key)>=self.max_slots:
            self.r296['COMPOSITE_GLOBAL_SLOT_BLOCK']+=1;self.r297['GLOBAL_SLOT_BLOCK']+=1;return False
        role=self._composite_role(thesis)
        if role is None:
            self.r296['COMPOSITE_ROLE_CAPACITY_BLOCK']+=1;self.r297['ROLE_CAPACITY_BLOCK']+=1;return False
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(thesis));available=max(0.0,debt-reserved)
        if available<=EPS:
            self.r297['NO_UNRESERVED_REPAIR_DEBT']+=1;return False
        used=self._used_prices(thesis);chosen=None
        for raw in self._live_price_levels(thesis):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            min_overflow=1.0/p
            q=available+min_overflow
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:
                self.r297['STRUCTURAL_QTY_INFEASIBLE']+=1;continue
            sp=self._repair_split(thesis,p,q)
            if sp is None:
                self.r297['V8_SPLIT_REJECT']+=1;continue
            rq=float(sp.get('repairQty') or 0.0);oq=float(sp.get('overflowQty') or 0.0)
            if rq<=EPS or oq<=EPS:
                self.r297['NOT_TRUE_COMPOSITE_AFTER_SPLIT']+=1;continue
            chosen=(p,q,sp,min_overflow,available,debt,reserved);break
        if chosen is None:
            self.r296['COMPOSITE_NO_TRUE_CROSSING_CANDIDATE']+=1;self.r297['NO_STRUCTURAL_COMPOSITE_CANDIDATE']+=1;return False
        p,q,sp,min_overflow,available,debt,reserved=chosen;before_n=self.n
        self._r296CompositeContext=True
        try:ok=r264.ExecutionRepresentedPreRepairReexpandSim._submit_role_v8(self,t,thesis,role,p,q,float(sp['fullFloor']),sp)
        finally:self._r296CompositeContext=False
        if not ok:
            self.r296['COMPOSITE_SUBMIT_BLOCKED']+=1;self.r297['SUBMIT_BLOCKED']+=1;return False
        key=f'{thesis}_{before_n}'
        self.r296CompositeKeys.add(key)
        self.r296CompositeMeta[key]={'key':key,'submittedAt':int(t),'generation':int(self.key_scope_gen.get(key,self.scopeGeneration)),
            'thesisSide':thesis,'scopeSideAtSubmit':self.scopeSide,'role':role,'price':float(p),'qty':float(q),
            'repairQtyAuthorized':float(sp['repairQty']),'overflowQtyAuthorized':float(sp['overflowQty']),
            'overflowRiskAuthorized':float(sp['overflowRisk']),'structuralAvailableDebt':available,
            'structuralVenueMinOverflowQty':min_overflow,'debtBefore':debt,'reservedRepairBefore':reserved,
            'sizing':'UNRESERVED_REPAIR_DEBT_PLUS_ONE_VENUE_MIN_OVERFLOW'}
        if role=='SATELLITE_REPAIR':self.fanoutKeys.add(key)
        self.r296['COMPOSITE_SUBMIT']+=1;self.r297['STRUCTURAL_COMPOSITE_SUBMIT']+=1
        self.r297['AUTHORIZED_REPAIR_QTY_MILLI']+=int(round(float(sp['repairQty'])*1000))
        self.r297['AUTHORIZED_OVERFLOW_QTY_MILLI']+=int(round(float(sp['overflowQty'])*1000))
        ev={'t':int(t),'event':'R297_STRUCTURAL_COMPOSITE_SUBMIT',**self.r296CompositeMeta[key],
            'availableCreditAfter':float(self._available_expand_risk_credit())}
        self.r297Events.append(ev);self.r296Events.append(ev);self.slot_history.append(ev)
        return True

    def process(self,t):
        before=int(self.r296.get('COMPOSITE_FILL',0))
        super().process(t)
        if int(self.r296.get('COMPOSITE_FILL',0))>before:
            # Mirror only newly generated R296 fill telemetry for structural attribution.
            for e in reversed(self.r296Events):
                if e.get('event')=='R296_COMPOSITE_THESIS_RETURN_FILL' and int(e.get('t') or -1)==int(t):
                    self.r297['STRUCTURAL_COMPOSITE_FILL']+=1
                    if float(e.get('thesisOverflowRealized') or 0.0)>EPS:self.r297['CONFIRMED_THESIS_OVERFLOW_FILL']+=1
                    self.r297Events.append({'event':'R297_STRUCTURAL_COMPOSITE_FILL',**dict(e)})
                    break

    def run_r297(self,winner):
        r=super().run_r296(winner)
        r.update({'r297Version':'MS4_R2_97_STRUCTURAL_COMPOSITE_CROSSING_V1','r297Stats':dict(self.r297),
            'r297Events':self.r297Events[:5000],'r297StructuralSubmits':int(self.r297.get('STRUCTURAL_COMPOSITE_SUBMIT',0)),
            'r297StructuralFills':int(self.r297.get('STRUCTURAL_COMPOSITE_FILL',0)),
            'r297ConfirmedOverflowFills':int(self.r297.get('CONFIRMED_THESIS_OVERFLOW_FILL',0)),
            'r297AuthorizedRepairQty':float(self.r297.get('AUTHORIZED_REPAIR_QTY_MILLI',0))/1000.0,
            'r297AuthorizedOverflowQty':float(self.r297.get('AUTHORIZED_OVERFLOW_QTY_MILLI',0))/1000.0,
            'r297CorrectnessPass':bool(r.get('r296CorrectnessPass'))})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r297_'))
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
            s=StructuralCompositeCrossingSim(tape,1,4)
            try:r=s.run_r297(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R297_STRUCTURAL_COMPOSITE_CROSSING','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r296IntentThesisSide'),'thesisWinnerAligned':r.get('r296IntentThesisSide')==w,
               'opportunities':int(r.get('r296Stats',{}).get('POST_REPAID_OFF_THESIS_OPPORTUNITY',0)),
               'structuralSubmits':int(r.get('r297StructuralSubmits',0)),'structuralFills':int(r.get('r297StructuralFills',0)),
               'confirmedOverflowFills':int(r.get('r297ConfirmedOverflowFills',0)),'authorizedRepairQty':float(r.get('r297AuthorizedRepairQty',0.0)),
               'authorizedOverflowQty':float(r.get('r297AuthorizedOverflowQty',0.0)),'actualCompositeRepairQty':float(r.get('r296CompositeRepairQty',0.0)),
               'actualCompositeOverflowQty':float(r.get('r296CompositeOverflowQty',0.0)),'baselineFallbacks':int(r.get('r296BaselineFallbacks',0)),
               'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),
               'floorDelta':float(r['floor'])-float(br['floor']),'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),
               'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),'submitDelta':int(r['submits'])-int(br['submits']),
               'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),
               'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,
               'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'riskFills':int(r.get('riskTrancheFillEvents',0)),
               'correct':bool(r.get('r297CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        c1946784=[x for x in cmp if x['marketId']==1946784]
        out={'version':'MS4_R2_97_STRUCTURAL_COMPOSITE_CROSSING_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'structuralSubmitExercised':any(x['structuralSubmits']>0 for x in cmp),
                      'structuralFillExercised':any(x['structuralFills']>0 for x in cmp),'confirmedThesisOverflowExercised':any(x['actualCompositeOverflowQty']>EPS for x in cmp),
                      'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp),
                      'noEffect1946784':all(abs(x[k])<=1e-9 for x in c1946784 for k in ['pnlDelta','bestDelta','floorDelta','gapDelta','fillDelta','submitDelta']) if c1946784 else None},
             'boundary':['R2.64 exact fallback when structural composite unavailable','post-repaid off-thesis ordinary Expand trigger only','physical qty = unreserved Repair debt + one venue-min thesis overflow','no fitted qty multiplier','frozen V8 Repair-first/overflow-second accounting','partial fill before boundary is Repair only','only confirmed overflow is thesis exposure','R2.47 favorable replenishment exempt','Passive and inherited Active unchanged','max4/fanout1','<=180s unchanged','no winner/Target/future runtime input','realistic HFT','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'bestDelta':sum(x['bestDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'fillDelta':sum(x['fillDelta'] for x in cmp)}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
