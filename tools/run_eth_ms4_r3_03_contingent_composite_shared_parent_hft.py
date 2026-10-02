from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_eth_ms4_r3_02_obligation_live_competitive_continuation_shadow as r302
from tools import run_eth_role_separated_multislot_v8_repair_overflow_split_smoke as v8
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
r264=r302.r264; v2=r302.v2; EPS=1e-9; REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class ContingentCompositeSharedParentHFT(r302.ObligationLiveCompetitiveContinuationShadow):
    """R3.03: replace one R2.63 pre-Repair risk action by a shared-parent contingent composite.

    The existing R2.63 one-unit risk authority is preserved, not enlarged.  A single live
    dedicated Repair sibling and a new thesis-side contingent carrier share the same actual
    parent debt.  Confirmed fill is allocated at fill-time Repair-first / overflow-second.
    Pending sibling fill is never counted as protection.  Aggregate new overflow risk is capped
    by the original one venue-min risk unit regardless of sibling/contingent fill order.
    """
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.r303=Counter();self.r303Events=[]
        self.r303Ledger=GenerationAwareSharedParentDebtAllocationLedgerV3()
        self.r303Parent=None;self.r303SharedKeys=set();self.r303BaseCum={}
        self.r303Authority=None;self.r303ExpectedByReceipt={};self.r303SplitMismatch=0
        self.r303OverflowRisk=0.0;self.r303OverflowQty=0.0;self.r303RepairQty=0.0
        self.r303NewObligationQty=0.0;self.r303AuthorityOverrun=0.0

    # ---- shared reservation semantics: one parent debt, not duplicated per-key quotas ----
    def _shared_live_keys(self):
        out=[]
        for key in list(self.r303SharedKeys):
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:out.append(key)
        return out

    def _reserved_repair_quota(self,repair_side=None):
        total=float(super()._reserved_repair_quota(repair_side))
        if not self.r303Parent:return total
        rs=repair_side or self._repair_side()
        if rs!=self.r303Parent['side'] or int(self.scopeGeneration)!=int(self.r303Parent['generation']):return total
        # Shared-parent ownership is a reservation fact, not a venue-status fact.  During
        # submit latency snap(status) may be NONE, so subtract every registered shared local
        # quota that still owns a slot/order, then add the parent residual exactly once.
        shared_owned=[]
        slot_values=set(str(x) for x in self.slot_key.values())
        for key in list(self.r303SharedKeys):
            o=self.orders.get(key)
            if o is None:continue
            if str(o['side'])!=rs or int(self.key_scope_gen.get(key,-1))!=int(self.r303Parent['generation']):continue
            owns=(str(key) in slot_values)
            if not owns:
                try:owns=str(self.snap(o).get('status') or '').upper() not in v2.TERMINAL_STATUSES
                except Exception:owns=True
            if not owns:continue
            shared_owned.append(key)
            total-=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
        if shared_owned:
            st=self.r303Ledger.describe_parent(int(self.r303Parent['pid']))
            if st is not None:total+=max(0.0,float(st.get('remainingDebt') or 0.0))
        return max(0.0,float(total))

    def _reserved_repair_overflow_risk(self):
        # R3.03 pending overflow is covered by its explicit one-unit risk authority, not generic credit.
        total=0.0
        for _,key,o,role in self._live_role_rows():
            if role not in REPAIR_ROLES or key in self.r303SharedKeys:continue
            oq=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
            if oq>EPS:total+=oq*float(o['price'])
        return float(total)

    def _risk_authority_current_generation(self):
        base=float(super()._risk_authority_current_generation())
        a=self.r303Authority
        if a and int(a.get('generation',-1))==int(self.scopeGeneration):
            base+=float(a.get('held',0.0))+float(a.get('spent',0.0))
        return base

    def _audit_r247(self):
        super()._audit_r247()
        a=self.r303Authority
        if a:
            err=abs(float(a['authorized'])-float(a['held'])-float(a['spent'])-float(a['released']))
            self.r303AuthorityOverrun=max(self.r303AuthorityOverrun,err,max(0.0,float(a['spent'])-float(a['authorized'])))

    # ---- one structural replacement of R2.63 authority ----
    def _try_r263(self,t,end):
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if gen in self.r263GenerationUsed:return False
        if float(ob.get('repaidQty') or 0.0)>EPS:return False
        live=self._live_dedicated_repair_rows(gen)
        if not live:return False
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        if quota+EPS<outstanding:
            self.r264['BLOCK_CURRENT_OBLIGATION_NOT_FULLY_REPRESENTED']+=1;return False
        # R3.03 is only the clean structural seam found by R3.02.
        thesis=self.intentThesisSide;repair_side=self._repair_side()
        if (thesis not in {'UP','DOWN'} or thesis!=repair_side or len(live)!=1 or
            int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self._has_stale_scope_reservation() or
            len(self.slot_key)+len(self.activeKeys)>=self.max_slots):
            return super()._try_r263(t,end)
        sibling_key,sibling_o=live[0]
        if self._samegen_expand_live(gen,str(ob.get('scopeSideAtBirth') or self.scopeSide)):
            self.r303['BLOCK_ORDINARY_EXPAND_ALREADY_LIVE']+=1;return False
        # Choose a distinct current strict-past thesis/Repair-side Maker price.
        used=self._used_prices(thesis);p=None
        for raw in self._live_price_levels(thesis):
            px=float(v2.kprice(raw))
            if px>EPS and px not in used:
                p=px;break
        if p is None:
            self.r303['BLOCK_NO_DISTINCT_THESIS_PRICE']+=1;return False
        try:ss=self.snap(sibling_o);sib_rem=float(ss.get('leavesQty')) if ss.get('leavesQty') is not None else self._remaining(sibling_key)
        except Exception:sib_rem=self._remaining(sibling_key)
        debt=max(0.0,float(self._scope_debt_qty()));venue=1.0/p
        unreserved=max(0.0,debt-max(0.0,sib_rem));q=unreserved+venue
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:
            self.r303['BLOCK_STRUCTURAL_QTY_INFEASIBLE']+=1;return False
        # One venue-min of possible overflow is exactly the frozen R2.63 risk unit.
        risk=float(venue*p)
        before_n=self.n;key=f'{thesis}_{before_n}';pid=100000+gen
        base_sib=float(sibling_o.get('cum') or 0.0)
        self.r303Ledger.register_carrier(str(sibling_key),pid,debt)
        self.r303Ledger.register_carrier(str(key),pid,debt)
        self.r303Parent={'pid':pid,'generation':gen,'side':thesis,'initialDebt':debt,'siblingKey':str(sibling_key),'contingentKey':key,
                         'venueMin':venue,'price':p,'riskCap':risk,'bornAt':int(t)}
        self.r303SharedKeys={str(sibling_key),key};self.r303BaseCum={str(sibling_key):base_sib,key:0.0}
        self.r303Authority={'authorized':risk,'held':risk,'spent':0.0,'released':0.0,'generation':gen,'bornAt':int(t)}
        # Submit through V8 directly to bypass only V8.2's per-key exclusivity.  Local split is
        # provisional; every confirmed fill is rewritten to shared-parent allocation pre-process.
        repair_floor=float(self._candidate_alone_floor(thesis,p,unreserved)) if unreserved>EPS else float(self._physical_floor())
        full_floor=float(self._candidate_alone_floor(thesis,p,q))
        sp={'repairQty':unreserved,'overflowQty':venue,'overflowRisk':risk,'repairOnlyFloor':repair_floor,'fullFloor':full_floor,
            'debt':debt,'reservedRepairBefore':quota,'availableDebtBefore':unreserved,'r303SharedParentProvisional':True}
        ok=v8.RepairOverflowSplitSim._submit_role_v8(self,t,thesis,'SATELLITE_REPAIR',p,q,full_floor,sp)
        if not ok:
            self.r303Parent=None;self.r303SharedKeys=set();self.r303BaseCum={};self.r303Authority=None
            self.r303['SUBMIT_BLOCKED']+=1;return False
        self.r263GenerationUsed.add(gen)
        self.r303['SUBMIT']+=1;self.r264['SUBMIT']+=1
        ev={'t':int(t),'event':'R303_CONTINGENT_COMPOSITE_SUBMIT','generation':gen,'parentId':pid,'siblingKey':str(sibling_key),
            'contingentKey':key,'thesisSide':thesis,'scopeSide':self.scopeSide,'price':p,'physicalQty':q,'parentDebt':debt,
            'siblingRemaining':sib_rem,'unreservedDebt':unreserved,'venueMinOverflowCap':venue,'riskCap':risk,
            'repairProgressAtSubmit':float(ob.get('repaidQty') or 0.0),'physicalOccupancyAfter':len(self.slot_key)+len(self.activeKeys)}
        self.r303Events.append(ev);self.slot_history.append(ev);self._audit_r247();return True

    def _prepare_shared_fill_allocation(self,t):
        if not self.r303Parent:return
        pid=int(self.r303Parent['pid']);expected={}
        # Base V8 iterates physical orders in insertion order; reproduce that exact order.
        keys=[k for k in self.r303SharedKeys if k in self.orders]
        keys.sort(key=lambda k:int(self.orders[k].get('n') or 0))
        for key in keys:
            o=self.orders[key]
            try:s=self.snap(o);cur_abs=float(s.get('cumExecQty') or 0.0)
            except Exception:continue
            rel=max(0.0,cur_abs-float(self.r303BaseCum.get(key,0.0)))
            ar=self.r303Ledger.allocate_cumulative(str(key),pid,rel,float(self.r303Parent['initialDebt']))
            if ar is None:continue
            expected[key]={'repair':float(ar.repair_increment),'overflow':float(ar.overflow_increment),'fill':float(ar.fill_increment),
                           'debtBefore':float(ar.debt_before),'debtAfter':float(ar.debt_after)}
            # Force frozen V8 split to equal the already-confirmed shared-parent allocation this receipt.
            self.keyRepairQuotaRemaining[key]=float(ar.repair_increment)
            self.keyOverflowQtyRemaining[key]=float(ar.overflow_increment)
        if expected:
            self.r303ExpectedByReceipt[int(t)]=expected

    def process(self,t):
        self._prepare_shared_fill_allocation(int(t))
        start=len(self.splitEvents)
        super().process(t)
        expected=self.r303ExpectedByReceipt.get(int(t),{})
        actual={}
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.r303SharedKeys:continue
            actual[key]={'repair':float(ev.get('repairAllocated') or 0.0),'overflow':float(ev.get('overflowRealized') or 0.0),
                         'fill':float(ev.get('fillInc') or 0.0),'price':float(ev.get('price') or 0.0),'side':str(ev.get('side'))}
        overflow_birth=0.0
        for key in set(expected)|set(actual):
            e=expected.get(key,{'repair':0.0,'overflow':0.0,'fill':0.0});a=actual.get(key,{'repair':0.0,'overflow':0.0,'fill':0.0,'price':0.0})
            ok=all(abs(float(e[x])-float(a[x]))<=1e-8 for x in ('repair','overflow','fill'))
            if not ok:self.r303SplitMismatch+=1
            self.r303RepairQty+=float(a['repair']);self.r303OverflowQty+=float(a['overflow']);overflow_birth+=float(a['overflow'])
            orisk=float(a['overflow'])*float(a.get('price') or 0.0);self.r303OverflowRisk+=orisk
            if orisk>EPS and self.r303Authority:
                take=min(float(self.r303Authority['held']),orisk);self.r303Authority['held']-=take;self.r303Authority['spent']+=orisk
                self.r303AuthorityOverrun=max(self.r303AuthorityOverrun,max(0.0,float(self.r303Authority['spent'])-float(self.r303Authority['authorized'])))
            self.r303Events.append({'t':int(t),'event':'R303_SHARED_FILL_ALLOCATED','key':key,'expected':e,'actual':a,'match':ok})
        # Confirmed overflow is new thesis exposure; only now birth its Repair obligation.
        if overflow_birth>EPS and self.scopeSide in {'UP','DOWN'}:
            ngen=int(self.scopeGeneration)
            if self.r303Authority:self.r303Authority['generation']=ngen
            ob=self.riskRepairObligations.setdefault(ngen,{'generation':ngen,'scopeSideAtBirth':self.scopeSide,'bornAt':int(t),
                'bornFromRiskKey':'R303_SHARED_OVERFLOW','bornQty':0.0,'outstanding':0.0,'repaidQty':0.0,'passiveRepaidQty':0.0,
                'activeRepaidQty':0.0,'carrierSubmits':0,'carrierFills':0,'zeroFillTerminals':0,'closedAt':None,'closeReason':None})
            ob['bornQty']+=overflow_birth;ob['outstanding']+=overflow_birth;self.r303NewObligationQty+=overflow_birth
            self.riskTrancheGenerationUsed.add(ngen)
            self.r303['OVERFLOW_OBLIGATION_BORN']+=1
            self.r303Events.append({'t':int(t),'event':'R303_CONFIRMED_OVERFLOW_BIRTHS_RISK_OBLIGATION','generation':ngen,
                                    'scopeSide':self.scopeSide,'qty':overflow_birth,'outstanding':ob['outstanding']})
        # Release any unused risk authority only after both shared physical carriers are terminal.
        if self.r303Authority and float(self.r303Authority['held'])>EPS and not self._shared_live_keys():
            q=float(self.r303Authority['held']);self.r303Authority['held']=0.0;self.r303Authority['released']+=q
            self.r303Events.append({'t':int(t),'event':'R303_UNUSED_RISK_AUTHORITY_RELEASED','amount':q})
        self._audit_r247()

    def run_r303(self,winner):
        r=super().run_r302(winner)
        parent=self.r303Ledger.describe_parent(int(self.r303Parent['pid'])) if self.r303Parent else None
        cons=True
        if parent is not None:
            cons=abs(float(parent['repairPaid'])+float(parent['remainingDebt'])-float(parent['initialDebt']))<=1e-8
        correct=(bool(r.get('r264CorrectnessPass')) and self.r303SplitMismatch==0 and self.r303AuthorityOverrun<=EPS and
                 self.r303OverflowRisk<=1.0+1e-8 and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and
                 float(r.get('repairQuotaExcessMax',0.0))<=EPS and cons)
        r.update({'r303Version':'MS4_R3_03_CONTINGENT_COMPOSITE_SHARED_PARENT_HFT_V1','r303Stats':dict(self.r303),
                  'r303Events':self.r303Events[:4000],'r303Parent':self.r303Parent,'r303ParentState':parent,
                  'r303SplitMismatch':int(self.r303SplitMismatch),'r303RepairQty':float(self.r303RepairQty),
                  'r303OverflowQty':float(self.r303OverflowQty),'r303OverflowRisk':float(self.r303OverflowRisk),
                  'r303NewObligationQty':float(self.r303NewObligationQty),'r303Authority':self.r303Authority,
                  'r303AuthorityOverrun':float(self.r303AuthorityOverrun),'r303CorrectnessPass':bool(correct)})
        return r

def compact(r):
    return {'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),'best':float(r.get('best') or 0.0),'floor':float(r.get('floor') or 0.0),
            'fills':int(r.get('fillEvents') or 0),'submits':int(r.get('submits') or 0),'activeRepaidQty':float(r.get('riskRepairActiveRepaidQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r303_hft_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            tape=tmp/f'{m}.json.xz';w=co[m]['winner']
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            c=ContingentCompositeSharedParentHFT(tape,1,4)
            try:rr=c.run_r303(w)
            finally:c.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},
                     {'marketId':m,'cell':'R303_SHARED_PARENT_CONTINGENT_COMPOSITE','winnerPostHocOnly':w,**rr}]
            bc=compact(br);cc=compact(rr)
            d={'marketId':m,'r303Submits':int(rr.get('r303Stats',{}).get('SUBMIT',0)),'repairQty':rr.get('r303RepairQty'),
               'overflowQty':rr.get('r303OverflowQty'),'overflowRisk':rr.get('r303OverflowRisk'),'splitMismatch':rr.get('r303SplitMismatch'),
               'newObligationQty':rr.get('r303NewObligationQty'),'pnlDelta':cc['pnl']-bc['pnl'],'bestDelta':cc['best']-bc['best'],
               'floorDelta':cc['floor']-bc['floor'],'gapDelta':(cc['best']-cc['floor'])-(bc['best']-bc['floor']),
               'fillDelta':cc['fills']-bc['fills'],'submitDelta':cc['submits']-bc['submits'],'activeDelta':cc['activeRepaidQty']-bc['activeRepaidQty'],
               'correct':bool(rr.get('r303CorrectnessPass')),'unauthorizedOverflowQty':float(rr.get('unauthorizedOverflowQty',0.0)),
               'repairQuotaExcessMax':float(rr.get('repairQuotaExcessMax',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R3_03_CONTINGENT_COMPOSITE_SHARED_PARENT_HFT_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'candidateExercised':any(x['r303Submits']>0 for x in cmp),
                      'confirmedCrossingExercised':any(float(x['overflowQty'] or 0)>EPS for x in cmp)},
             'boundary':['R2.64 exact control','one R2.63 pre-Repair risk authority per generation is re-represented, never duplicated','exactly one live dedicated Repair sibling structural seam','persistent first-PROBE intent thesis is research identity only','pending sibling gives zero protection/payment','pre-process confirmed fill visibility drives same-receipt shared-parent allocation','GenerationAwareSharedParentDebtAllocationLedgerV3 Repair-first overflow-second','aggregate R3.03 overflow risk <= original one venue-min risk unit','old-generation carriers retain native stale-scope cancellation','Passive/Active/R2.39/R2.40/R2.47 hooks preserved','max4 and <=180s unchanged','winner posthoc only','consumed HFT','no Target runtime input/no dream fill/no 8781']}
        import os
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
