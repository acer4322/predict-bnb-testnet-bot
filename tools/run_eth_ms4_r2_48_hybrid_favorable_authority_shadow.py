from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9

class HybridFavorableAuthorityShadow(r247.BoundedCoreServiceFavorableRecycleSim):
    """Behavior-inert R2.48 shadow.

    R2.47 requires favorable Repair lots to cover the FULL venue-min re-expand
    whenever monetary credit alone is insufficient.  This shadow asks whether
    partial favorable-lot authority plus existing monetary credit could cover the
    same order without any borrowed/free authority.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r248=Counter();self.r248Events=[];self._seenHybrid=set()
    def _try_favorable_replenishment(self,t,end):
        if int(end)-int(t)>r247.v2.NO_NEW_EXPOSURE_MS and self.scopeSide is not None and not self._has_stale_scope_reservation() and not self._has_live_replenishment():
            side=str(self.scopeSide)
            if len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
                cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
                if cand is not None:
                    p,q,proj,split=cand
                    risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
                    credit=float(self._available_expand_risk_credit())
                    if credit+EPS<risk:
                        gross=float(self._gross_eligible_qty(side,p)); elig=float(self._eligible_qty(side,p)); matched=min(float(q),elig)
                        pair_authority=matched*float(p); monetary_required=max(0.0,risk-pair_authority)
                        key=(int(self.scopeGeneration),int(self.scopeRepairProgressClocks),side,round(float(p),8))
                        if key not in self._seenHybrid:
                            self._seenHybrid.add(key);self.r248['MONETARY_BLOCKED_UNIQUE_EPOCH']+=1
                            if elig<=EPS:self.r248['NO_FAVORABLE_LOT']+=1
                            elif elig+EPS>=q:self.r248['FULL_LOT_R247_ELIGIBLE']+=1
                            elif credit+pair_authority+EPS>=risk:self.r248['HYBRID_PARTIAL_LOT_PLUS_CREDIT_CAN_COVER']+=1
                            else:self.r248['HYBRID_STILL_SHORT']+=1
                            if gross+EPS>=q and elig+EPS<q:self.r248['PENDING_ORDINARY_CLAIM_REDUCED_BELOW_FULL']+=1
                            ev={'t':int(t),'generation':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks),'side':side,
                                 'expandPrice':float(p),'expandQty':float(q),'riskCost':risk,'availableMonetaryCredit':credit,
                                 'grossFavorableRepairQty':gross,'unclaimedFavorableRepairQty':elig,'matchedQtyIfHybrid':matched,
                                 'pairAuthorityNotional':pair_authority,'monetaryRequiredAfterPairAuthority':monetary_required,
                                 'hybridCoverable':bool(elig>EPS and elig+EPS<q and credit+pair_authority+EPS>=risk)}
                            self.r248Events.append(ev)
        return super()._try_favorable_replenishment(t,end)
    def run_shadow(self,w):
        r=super().run_r247(w);r['r248Stats']=dict(self.r248);r['r248Events']=self.r248Events[:3000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r248_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];agg=Counter()
        for mid in mids:
            cr=co[mid];sim=HybridFavorableAuthorityShadow(tmp/'tapes'/f'{mid}.json.xz',1,4)
            try:r=sim.run_shadow(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r});agg.update(r.get('r248Stats') or {})
            if r.get('r248Stats'):print(json.dumps({'marketId':mid,'stats':r['r248Stats'],'events':r['r248Events'][:12]},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_48_HYBRID_FAVORABLE_AUTHORITY_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'markets':mids,'aggregate':dict(agg),'rows':rows,
             'boundary':['R2.47 behavior exactly unchanged','shadow only after frozen ordinary action gave no same-receipt new option','no pairSum relaxation: only actual unconsumed Repair lot units with repairPrice+expandPrice<=1 contribute pair authority','hybrid pair authority equals matched favorable qty * expandPrice','remaining immediate Expand floor risk must be covered by already-existing monetary continuation credit','no borrowed/free credit','unique shadow state keyed by generation, repairProgressClock, side, expandPrice','no winner/future/Target input']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':dict(agg),'hybridMarkets':[(r['marketId'],r.get('r248Stats',{}).get('HYBRID_PARTIAL_LOT_PLUS_CREDIT_CAN_COVER',0)) for r in rows if r.get('r248Stats',{}).get('HYBRID_PARTIAL_LOT_PLUS_CREDIT_CAN_COVER',0)]},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
