from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9

class FavorablePriceLadderShadow(r247.BoundedCoreServiceFavorableRecycleSim):
    """Behavior-inert search for current-book alternative Maker re-expand prices.

    R2.47 uses the first unused live price returned by _candidate_from_levels_v8.
    This shadow asks whether another currently-live unused price can support FULL
    venue-min quantity coverage by unconsumed actual Repair lots with pairSum<=1.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r250=Counter();self.r250Events=[];self._seen=set()
    def _match_without_mutation(self,side,p,q):
        claims=self._pending_ordinary_claims();need=float(q);used=[]
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if need<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(p)>1.0+EPS:continue
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            take=min(need,free);need-=take
            used.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+float(p)})
        gain=sum((1.0-u['pairSum'])*u['qty'] for u in used)
        return used,need,gain
    def _try_favorable_replenishment(self,t,end):
        if int(end)-int(t)>r247.v2.NO_NEW_EXPOSURE_MS and self.scopeSide is not None and not self._has_stale_scope_reservation() and not self._has_live_replenishment():
            side=str(self.scopeSide)
            if len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
                used_prices=self._used_prices(side); levels=[]
                for raw in self._live_price_levels(side):
                    p=float(r247.v2.kprice(raw))
                    if p in used_prices or p<=EPS:continue
                    q=1.0/p
                    if not math.isfinite(q) or q<=EPS:continue
                    risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
                    credit=float(self._available_expand_risk_credit())
                    lots,left,gain=self._match_without_mutation(side,p,q)
                    elig=q-left
                    levels.append({'price':p,'qty':q,'risk':risk,'credit':credit,'eligibleQty':elig,'full':left<=EPS,'gain':gain,'lots':lots})
                if levels:
                    default=levels[0];alts=[z for z in levels[1:] if z['full'] and z['credit']+EPS<z['risk']]
                    key=(int(self.scopeGeneration),int(self.scopeRepairProgressClocks),side)
                    if key not in self._seen:
                        self._seen.add(key);self.r250['UNIQUE_EPOCH_WITH_PRICE_LADDER']+=1
                        if default['full'] and default['credit']+EPS<default['risk']:self.r250['DEFAULT_FULL_COVERABLE']+=1
                        if alts and not (default['full'] and default['credit']+EPS<default['risk']):
                            self.r250['ALT_PRICE_UNLOCKS_FULL_COVERAGE']+=1
                            self.r250['ALT_PRICE_UNLOCK_COUNT_TOTAL']+=len(alts)
                            # Economic ranking, not threshold tuning: maximize immediate matched pair gain.
                            best=max(alts,key=lambda z:(float(z['gain']),-float(z['risk']),float(z['price'])))
                            ev={'t':int(t),'generation':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks),'side':side,
                                'default':{k:v for k,v in default.items() if k!='lots'},
                                'bestAlternative':best,'alternativeCount':len(alts),'liveUnusedPriceCount':len(levels)}
                            self.r250Events.append(ev)
                        elif not alts and not default['full']:self.r250['NO_FULL_COVERAGE_AT_ANY_CURRENT_PRICE']+=1
        return super()._try_favorable_replenishment(t,end)
    def run_shadow(self,w):
        r=super().run_r247(w);r['r250Stats']=dict(self.r250);r['r250Events']=self.r250Events[:3000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r250_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];agg=Counter()
        for mid in mids:
            cr=co[mid];sim=FavorablePriceLadderShadow(tmp/'tapes'/f'{mid}.json.xz',1,4)
            try:r=sim.run_shadow(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r});agg.update(r.get('r250Stats') or {})
            if r.get('r250Stats',{}).get('ALT_PRICE_UNLOCKS_FULL_COVERAGE',0):
                print(json.dumps({'marketId':mid,'stats':r['r250Stats'],'events':r['r250Events'][:10]},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_50_FAVORABLE_PRICE_LADDER_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'markets':mids,'aggregate':dict(agg),'rows':rows,
             'boundary':['R2.47 behavior exactly unchanged','strict-past current live Maker price levels only','same used-price exclusion as _candidate_from_levels_v8','full venue-min Expand qty must be covered by actual unconsumed same-generation Repair lots','every matched unit requires repairPrice+expandPrice<=1','ordinary monetary-credit path and ordinary actions unchanged','alternative shadow ranks full-cover prices by immediate matched pair gain only','no winner/future/Target input']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':dict(agg),'marketsWithAlt':[(r['marketId'],r.get('r250Stats',{}).get('ALT_PRICE_UNLOCKS_FULL_COVERAGE',0)) for r in rows if r.get('r250Stats',{}).get('ALT_PRICE_UNLOCKS_FULL_COVERAGE',0)]},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
