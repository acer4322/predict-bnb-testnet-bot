from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9
v2=r264.v2

class MultiActionAdmissibilityScan(r264.ExecutionRepresentedPreRepairReexpandSim):
    """Behavior-inert Lane G scan.

    Capture strict-past state immediately before native SATELLITE_REPAIR frontier reanchor.
    No action is changed.  Alternative action admissibility is computed without submitting,
    cancelling, or consuming authority.  Fixed wall-clock windows are deliberately absent.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.scan=[]
        self.market_end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])

    def _live_status(self,o):
        try:return str(self.snap(o).get('status') or '').upper()
        except Exception:return ''

    def _strict_live_repair(self,gen):
        rows=[]
        for key,o in self._live_dedicated_repair_rows(gen):
            if self._live_status(o) in v2.TERMINAL_STATUSES:continue
            try:s=self.snap(o); rem=float(s.get('leavesQty')) if s.get('leavesQty') is not None else self._remaining(key)
            except Exception:rem=self._remaining(key)
            rows.append((str(key),o,max(0.0,float(rem))))
        return rows

    def _first_unused_price(self,side):
        used=self._used_prices(side)
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p>EPS and p not in used:return p
        return None

    def _ordinary_reexpand(self,t,ob,repairs):
        if self.scopeSide is None or self.market_end-int(t)<=v2.NO_NEW_EXPOSURE_MS:return {'ok':False,'reason':'LATE_OR_NO_SCOPE'}
        gen=int(ob['generation'])
        if gen in self.r263GenerationUsed:return {'ok':False,'reason':'GENERATION_ALREADY_USED'}
        if float(ob.get('repaidQty') or 0.0)>EPS:return {'ok':False,'reason':'REPAIR_ALREADY_PAID'}
        if not repairs:return {'ok':False,'reason':'NO_LIVE_REPAIR'}
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_,_ in repairs)
        if quota+EPS<outstanding:return {'ok':False,'reason':'OBLIGATION_NOT_FULLY_REPRESENTED','quota':quota,'outstanding':outstanding}
        side=str(ob.get('scopeSideAtBirth') or self.scopeSide)
        if side not in {'UP','DOWN'}:return {'ok':False,'reason':'NO_SIDE'}
        if self._samegen_expand_live(gen,side):return {'ok':False,'reason':'SAMEGEN_EXPAND_LIVE'}
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return {'ok':False,'reason':'CAPACITY_FULL'}
        if self._has_stale_scope_reservation():return {'ok':False,'reason':'STALE_SCOPE'}
        p=self._first_unused_price(side)
        if p is None:return {'ok':False,'reason':'NO_DISTINCT_PRICE'}
        q=1.0/p
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after)
        if risk<=EPS:return {'ok':False,'reason':'NOT_RISK_BEARING'}
        return {'ok':True,'side':side,'price':p,'qty':q,'riskAuthorized':risk,'floorBefore':before,'floorAfter':after}

    def _r303_composite(self,t,ob,repairs):
        gen=int(ob['generation']);outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_,_ in repairs)
        if quota+EPS<outstanding:return {'ok':False,'reason':'OBLIGATION_NOT_FULLY_REPRESENTED','quota':quota,'outstanding':outstanding}
        thesis=getattr(self,'intentThesisSide',None);repair_side=self._repair_side()
        if thesis not in {'UP','DOWN'}:return {'ok':False,'reason':'NO_THESIS'}
        if thesis!=repair_side:return {'ok':False,'reason':'THESIS_NOT_REPAIR_SIDE','thesis':thesis,'repairSide':repair_side}
        if len(repairs)!=1:return {'ok':False,'reason':'REQUIRES_EXACT_ONE_REPAIR','repairCount':len(repairs)}
        if self.market_end-int(t)<=v2.NO_NEW_EXPOSURE_MS:return {'ok':False,'reason':'LATE'}
        if self._has_stale_scope_reservation():return {'ok':False,'reason':'STALE_SCOPE'}
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return {'ok':False,'reason':'CAPACITY_FULL'}
        if self._samegen_expand_live(gen,str(ob.get('scopeSideAtBirth') or self.scopeSide)):return {'ok':False,'reason':'SAMEGEN_EXPAND_LIVE'}
        p=self._first_unused_price(thesis)
        if p is None:return {'ok':False,'reason':'NO_DISTINCT_THESIS_PRICE'}
        sibling_key,sibling_o,sib_rem=repairs[0]
        debt=max(0.0,float(self._scope_debt_qty()));venue=1.0/p;unreserved=max(0.0,debt-max(0.0,sib_rem));q=unreserved+venue
        if (not math.isfinite(q)) or q<=EPS or q>12.0+EPS:return {'ok':False,'reason':'STRUCTURAL_QTY_INFEASIBLE','q':q}
        return {'ok':True,'side':thesis,'price':p,'physicalQty':q,'parentDebt':debt,'siblingKey':sibling_key,
                'siblingRemaining':sib_rem,'unreservedDebt':unreserved,'venueMinOverflowCap':venue,'riskCap':venue*p,
                'representedQuota':quota,'outstanding':outstanding}

    def _inherited_active(self,t,target_key,target_o,ob):
        """Pure check of frozen FillabilityActiveRepair actuator semantics on the existing Repair intent.

        We do not mutate quota or call _submit_active.  Existing passive reservation remains authoritative,
        so most states correctly remain inadmissible.  No elapsed-time trigger is introduced.
        """
        if not hasattr(self,'_score') or not hasattr(self,'_has_live_active'):
            return {'ok':False,'reason':'NO_INHERITED_ACTIVE_ENGINE'}
        side=str(target_o.get('side'));role=str(self.key_role.get(target_key) or '')
        if role not in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:return {'ok':False,'reason':'NOT_REPAIR_ROLE'}
        q=max(0.0,float(self.keyRepairQuotaRemaining.get(target_key,0.0)))
        if q<=EPS:return {'ok':False,'reason':'NO_REMAINING_REPAIR_QUOTA'}
        split={'repairQty':q,'overflowQty':0.0}
        try:score,diag=self._score(side,role,float(target_o.get('price') or 0.0),q,split)
        except Exception as e:return {'ok':False,'reason':'SCORE_ERROR','detail':type(e).__name__}
        if score>=0.5:return {'ok':False,'reason':'FROZEN_FILLABILITY_KEEP_PASSIVE','score':float(score),'diag':diag}
        if self._has_live_active():return {'ok':False,'reason':'LIVE_ACTIVE_EXISTS','score':float(score),'diag':diag}
        qv=v2.base.quotes(self.book);ask=(qv or {}).get(side,{}).get('ask') if qv else None
        if ask is None:return {'ok':False,'reason':'NO_ACTIVE_ASK','score':float(score),'diag':diag}
        ap=float(ask)
        if role=='ECONOMIC_CORE' and not self._pair_ok(side,ap):return {'ok':False,'reason':'ACTIVE_CORE_PAIR_BLOCK','score':float(score),'activePrice':ap,'diag':diag}
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,q))
        if after<=before+EPS:return {'ok':False,'reason':'ACTIVE_NOT_FLOOR_IMPROVING','score':float(score),'activePrice':ap,'floorBefore':before,'floorAfter':after,'diag':diag}
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
        if avail+EPS<q:return {'ok':False,'reason':'ACTIVE_REPAIR_QUOTA_UNAVAILABLE','score':float(score),'activePrice':ap,'qty':q,'debt':debt,'reserved':reserved,'available':avail,'diag':diag}
        return {'ok':True,'score':float(score),'activePrice':ap,'qty':q,'debt':debt,'reserved':reserved,'available':avail,'diag':diag}

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));o=self.orders.get(key) if key is not None else None;role=self.key_role.get(key) if key is not None else None
        if o is not None and key and role=='SATELLITE_REPAIR' and reason=='SATELLITE_FRONTIER_REANCHOR' and not o.get('cancelRequested'):
            ob=self._obligation_current()
            if ob:
                gen=int(ob['generation']);repairs=self._strict_live_repair(gen)
                quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_,_ in repairs)
                ordx=self._ordinary_reexpand(t,ob,repairs);comp=self._r303_composite(t,ob,repairs);act=self._inherited_active(t,str(key),o,ob)
                payoff={'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'cost':float(self.cost)}
                payoff['upPayoff']=payoff['upQty']-payoff['cost'];payoff['downPayoff']=payoff['downQty']-payoff['cost']
                payoff['best']=max(payoff['upPayoff'],payoff['downPayoff']);payoff['floor']=min(payoff['upPayoff'],payoff['downPayoff']);payoff['gap']=payoff['best']-payoff['floor']
                actions=['KEEP_REPAIR','NATIVE_REANCHOR']
                if ordx.get('ok'):actions.append('ORDINARY_REEXPAND')
                if comp.get('ok'):actions.append('R303_CONTINGENT_COMPOSITE')
                if act.get('ok'):actions.append('INHERITED_ACTIVE')
                self.scan.append({'t':int(t),'key':str(key),'slotId':int(sid),'side':str(o.get('side')),'price':float(o.get('price') or 0.0),
                    'generation':gen,'scopeSide':self.scopeSide,'intentThesisSide':getattr(self,'intentThesisSide',None),
                    'obligation':{'outstanding':float(ob.get('outstanding') or 0.0),'repaidQty':float(ob.get('repaidQty') or 0.0),'bornQty':float(ob.get('bornQty') or 0.0)},
                    'liveRepairCount':len(repairs),'liveRepairQuota':quota,'livePassiveSlots':len(self.slot_key),'liveActiveSlots':len(self.activeKeys),
                    'maxSlots':int(self.max_slots),'scopeRepairProgressClocks':int(self.scopeRepairProgressClocks),
                    'availableExpandRiskCredit':float(self._available_expand_risk_credit()),'payoff':payoff,
                    'admissibleActions':actions,'ordinaryReexpand':ordx,'r303Composite':comp,'inheritedActive':act})
        return super()._request_cancel(t,sid,reason)

    def run_scan(self,winner):
        r=super().run_r264(winner)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_multi_scan_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];market_summary=[]
        for m in mids:
            s=MultiActionAdmissibilityScan(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_scan(co[m]['winner'])
            finally:s.close()
            for x in s.scan:
                y={'marketId':m,**x};rows.append(y)
            n3=sum(len(x['admissibleActions'])>=3 for x in s.scan);n4=sum(len(x['admissibleActions'])>=4 for x in s.scan);n5=sum(len(x['admissibleActions'])>=5 for x in s.scan)
            sm={'marketId':m,'captured':len(s.scan),'atLeast3':n3,'atLeast4':n4,'all5':n5,'correct':bool(r.get('r264CorrectnessPass'))}
            market_summary.append(sm);print(json.dumps(sm,ensure_ascii=False),flush=True)
        candidates=[x for x in rows if len(x['admissibleActions'])>=3]
        # Outcome-blind deterministic prereg suggestion: chronological earliest state per distinct market.
        chosen=[];seen=set()
        for x in sorted(candidates,key=lambda z:(int(z['t']),int(z['marketId']),str(z['key']))):
            if int(x['marketId']) in seen:continue
            chosen.append({'marketId':int(x['marketId']),'t':int(x['t']),'key':str(x['key']),'admissibleActions':list(x['admissibleActions'])})
            seen.add(int(x['marketId']))
            if len(chosen)>=3:break
        out={'version':'LANE_G_MULTI_ACTION_ADMISSIBILITY_SCAN_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'behaviorMutation':False,'marketCount':len(mids),'capturedDecisionCount':len(rows),'candidateCountAtLeast3':len(candidates),
             'marketSummary':market_summary,'rows':rows,'preregisterSuggestionOutcomeBlind':chosen,
             'gates':{'baselineCorrectnessPass':all(x['correct'] for x in market_summary),'atLeastOneThreeActionState':bool(candidates)},
             'selectionBoundary':['consumed cohort only','state captured immediately before native SATELLITE_REPAIR SATELLITE_FRONTIER_REANCHOR mutation','strict-past state only','no winner/terminal/future Target action used for selection','>=3 means KEEP_REPAIR + NATIVE_REANCHOR + one or more independently admissible alternatives','first state per distinct market by chronological t for prereg suggestion'],
             'policyBoundary':['behavior-inert scan','no fixed seconds/windows/rank-age gate','<=180s research boundary retained','max4 retained','pending Repair never protection','no risk/credit/qty consumed by scan','INHERITED_ACTIVE only frozen actuator semantics when naturally admissible']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'candidateCountAtLeast3':len(candidates),'preregisterSuggestion':chosen},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
