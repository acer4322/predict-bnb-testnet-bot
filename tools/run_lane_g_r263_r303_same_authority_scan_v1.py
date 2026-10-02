from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_eth_ms4_r3_02_obligation_live_competitive_continuation_shadow as r302
r264=r302.r264;v2=r302.v2;EPS=1e-9

class SameAuthorityScan(r302.ObligationLiveCompetitiveContinuationShadow):
    def __init__(self,tape,*a,**kw):
        self.sameAuthority=[]
        super().__init__(tape,*a,**kw)

    def _r303_structural_pre(self,t,end):
        ob=self._obligation_current()
        if not ob:return {'ok':False,'reason':'NO_OBLIGATION'}
        gen=int(ob['generation'])
        if gen in self.r263GenerationUsed:return {'ok':False,'reason':'GENERATION_ALREADY_USED'}
        if float(ob.get('repaidQty') or 0.0)>EPS:return {'ok':False,'reason':'REPAIR_ALREADY_PAID'}
        live=self._live_dedicated_repair_rows(gen)
        if not live:return {'ok':False,'reason':'NO_LIVE_DEDICATED_REPAIR'}
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        if quota+EPS<outstanding:return {'ok':False,'reason':'OBLIGATION_NOT_FULLY_REPRESENTED','quota':quota,'outstanding':outstanding}
        thesis=self.intentThesisSide;repair_side=self._repair_side()
        if thesis not in {'UP','DOWN'}:return {'ok':False,'reason':'NO_THESIS'}
        if thesis!=repair_side:return {'ok':False,'reason':'THESIS_NOT_REPAIR_SIDE','thesis':thesis,'repairSide':repair_side}
        if len(live)!=1:return {'ok':False,'reason':'REQUIRES_EXACT_ONE_REPAIR','repairCount':len(live)}
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return {'ok':False,'reason':'LATE'}
        if self._has_stale_scope_reservation():return {'ok':False,'reason':'STALE_SCOPE'}
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return {'ok':False,'reason':'CAPACITY_FULL'}
        scope_side=str(ob.get('scopeSideAtBirth') or self.scopeSide)
        if self._samegen_expand_live(gen,scope_side):return {'ok':False,'reason':'SAMEGEN_EXPAND_LIVE'}
        sibling_key,sibling_o=live[0]
        used=self._used_prices(thesis);p=None
        for raw in self._live_price_levels(thesis):
            px=float(v2.kprice(raw))
            if px>EPS and px not in used:p=px;break
        if p is None:return {'ok':False,'reason':'NO_DISTINCT_THESIS_PRICE'}
        try:ss=self.snap(sibling_o);sib_rem=float(ss.get('leavesQty')) if ss.get('leavesQty') is not None else self._remaining(sibling_key)
        except Exception:sib_rem=self._remaining(sibling_key)
        debt=max(0.0,float(self._scope_debt_qty()));venue=1.0/p;unreserved=max(0.0,debt-max(0.0,sib_rem));q=unreserved+venue
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:return {'ok':False,'reason':'STRUCTURAL_QTY_INFEASIBLE','q':q}
        return {'ok':True,'generation':gen,'scopeSide':self.scopeSide,'repairSide':repair_side,'thesisSide':thesis,
                'obligationOutstanding':outstanding,'obligationBornQty':float(ob.get('bornQty') or 0.0),'obligationRepaidQty':float(ob.get('repaidQty') or 0.0),
                'representedQuota':quota,'siblingKey':str(sibling_key),'siblingPrice':float(sibling_o['price']),'siblingRemaining':float(sib_rem),
                'debt':debt,'compositePrice':p,'compositePhysicalQty':q,'unreservedDebt':unreserved,'venueMinOverflowCap':venue,'riskCap':float(venue*p),
                'slots':len(self.slot_key),'active':len(self.activeKeys),'maxSlots':int(self.max_slots),'availableExpandCredit':float(self._available_expand_risk_credit())}

    def _try_r263(self,t,end):
        pre=self._r303_structural_pre(t,end)
        n0=int(self.n);s0=int(self.submits);h0=len(self.r263Events)
        ok=super()._try_r263(t,end)
        new_events=self.r263Events[h0:]
        if pre.get('ok') or ok:
            self.sameAuthority.append({'t':int(t),'pre':pre,'ordinaryReturned':bool(ok),'ordinarySubmitDelta':int(self.submits)-s0,'ordinaryNDelta':int(self.n)-n0,
                                       'ordinaryEvents':new_events})
        return ok

    def run_scan(self,winner):
        r=super().run_r302(winner)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_same_auth_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=SameAuthorityScan(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_scan(co[m]['winner'])
            finally:s.close()
            valid=[x for x in s.sameAuthority if x['pre'].get('ok') and x['ordinaryReturned'] and x['ordinarySubmitDelta']==1]
            row={'marketId':m,'candidateCount':len(valid),'candidates':valid,'allObserved':s.sameAuthority,'correct':bool(r.get('r264CorrectnessPass'))};rows.append(row)
            print(json.dumps({'marketId':m,'candidateCount':len(valid),'candidateTimes':[x['t'] for x in valid],'observedCount':len(s.sameAuthority),'correct':row['correct']},ensure_ascii=False),flush=True)
        chosen=[]
        for r in rows:
            if r['candidates']:
                x=r['candidates'][0];chosen.append({'marketId':r['marketId'],'t':x['t'],'generation':x['pre']['generation'],'pre':x['pre']})
        out={'version':'LANE_G_R263_R303_SAME_AUTHORITY_SCAN_V1_20260907','researchOnly':True,'behaviorMutation':False,'rows':rows,
             'preregisterSuggestion':chosen,
             'gates':{'correctnessPass':all(r['correct'] for r in rows),'sameAuthorityCandidateFound':bool(chosen)},
             'selectionBoundary':['strict-past pre-call state at frozen R2.64 _try_r263','ordinary R2.64/R2.63 action must actually submit','R3.03 structural predicate evaluated on exact same pre-call state','no winner/terminal outcome in selection','first valid state per market only'],
             'policyBoundary':['behavior unchanged','no fixed time-window rule','same existing one-unit risk authority','pending Repair not protection','max4 and <=180s retained','consumed only','no fresh/no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'preregisterSuggestion':chosen},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
