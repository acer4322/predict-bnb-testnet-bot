from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys,statistics
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9
HORIZONS=(1000,3000,5000,10000)

class FavorablePairLeaseReachabilityAudit(r247.BoundedCoreServiceFavorableRecycleSim):
    """Behavior-inert one-lease-per-generation reachability audit on exact R2.47."""
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r253=Counter();self.leaseRows=[];self.activeLease=None;self.seenLeaseGenerations=set();self._lastEnd=None

    def _safe_levels(self,side):
        used=self._used_prices(side);claims=self._pending_ordinary_claims();rows=[]
        levels=self._live_price_levels(side)
        best=float(levels[0]) if levels else None
        for raw in levels:
            p=float(r247.v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS:continue
            need=float(q);matched=[]
            for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
                if need<=EPS:break
                if x['side']!=side or float(x['repairPrice'])+p>1.0+EPS:continue
                free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
                if free<=EPS:continue
                take=min(need,free);need-=take
                matched.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+p})
            if need>EPS:continue
            gain=sum((1.0-float(z['pairSum']))*float(z['qty']) for z in matched)
            rank=int(self._rank(side,p));depth=float(self._depth(side,p));dist=None if best is None else max(0.0,(best-p)/0.01)
            rows.append({'price':p,'qty':q,'rank':rank,'depth':depth,'distanceTicksToBest':dist,'gain':gain,'matchedLots':matched})
        # highest legal price first = most aggressive Maker price in this architecture
        rows.sort(key=lambda z:(-float(z['price']),int(z['rank']),-float(z['gain'])))
        return rows

    def _default_state(self,side):
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return None
        p,q,proj,split=cand;p=float(p);q=float(q)
        claims=self._pending_ordinary_claims();need=q;elig=0.0
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if need<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+p>1.0+EPS:continue
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            take=min(need,free);need-=take;elig+=take
        return {'price':p,'qty':q,'eligibleQty':elig,'fullyCovered':need<=EPS}

    def _maybe_birth(self,t,end):
        if self.activeLease is not None or self.scopeSide is None:return
        gen=int(self.scopeGeneration)
        if gen in self.seenLeaseGenerations:return
        if int(end)-int(t)<=r247.v2.NO_NEW_EXPOSURE_MS:return
        # Only after ordinary R2.47 first refusal on this receipt.
        if self._last_new_receipt==int(t):return
        if self._has_stale_scope_reservation() or self._has_live_replenishment():return
        side=str(self.scopeSide);default=self._default_state(side)
        if default is None or bool(default['fullyCovered']):return
        safe=self._safe_levels(side)
        if not safe:return
        z=safe[0]
        lease={'marketId':None,'generation':gen,'scopeSide':side,'bornT':int(t),'closedT':None,'closeReason':None,
               'birth':{**{k:v for k,v in z.items() if k!='matchedLots'},'matchedLots':z['matchedLots'],'default':default},
               'snapshots':[],'ordinaryActionReceipts':0,'coverageMissingReceipts':0,'safeReceipts':0,
               'everRank1':False,'everRank2':False,'firstRank1Ms':None,'firstRank2Ms':None,
               'everMoreAggressive':False,'maxSafePrice':float(z['price']),'minRank':int(z['rank']),
               'horizonReached':{str(h):False for h in HORIZONS},'horizonSafe':{str(h):None for h in HORIZONS}}
        self.activeLease=lease;self.seenLeaseGenerations.add(gen);self.r253['LEASE_BORN']+=1

    def _snapshot(self,t):
        lease=self.activeLease
        if lease is None:return
        if self.scopeSide!=lease['scopeSide'] or int(self.scopeGeneration)!=int(lease['generation']):
            self._close_lease(t,'SCOPE_LEFT');return
        side=lease['scopeSide'];safe=self._safe_levels(side);ordinary=(self._last_new_receipt==int(t))
        if ordinary:lease['ordinaryActionReceipts']+=1
        age=int(t)-int(lease['bornT'])
        snap={'t':int(t),'ageMs':age,'ordinaryActionThisReceipt':bool(ordinary),'scopeRepairProgressClock':int(self.scopeRepairProgressClocks),
              'slotCount':len(self.slot_key),'activeCount':len(self.activeKeys),'availableCredit':float(self._available_expand_risk_credit()),
              'repairLotQty':sum(float(x['remaining']) for x in self.repairLots if int(x['generation'])==int(self.scopeGeneration))}
        if safe:
            z=safe[0];lease['safeReceipts']+=1
            snap.update({'safe':True,'price':float(z['price']),'qty':float(z['qty']),'rank':int(z['rank']),'depth':float(z['depth']),
                         'distanceTicksToBest':z['distanceTicksToBest'],'gain':float(z['gain'])})
            lease['maxSafePrice']=max(float(lease['maxSafePrice']),float(z['price']));lease['minRank']=min(int(lease['minRank']),int(z['rank']))
            if float(z['price'])>float(lease['birth']['price'])+EPS:lease['everMoreAggressive']=True
            if int(z['rank'])<=2 and not lease['everRank2']:
                lease['everRank2']=True;lease['firstRank2Ms']=age
            if int(z['rank'])<=1 and not lease['everRank1']:
                lease['everRank1']=True;lease['firstRank1Ms']=age
        else:
            lease['coverageMissingReceipts']+=1;snap.update({'safe':False})
        # First observed receipt at/after each horizon determines survival state.
        for h in HORIZONS:
            hs=str(h)
            if not lease['horizonReached'][hs] and age>=h:
                lease['horizonReached'][hs]=True;lease['horizonSafe'][hs]=bool(safe)
        if len(lease['snapshots'])<1500:lease['snapshots'].append(snap)

    def _close_lease(self,t,reason):
        lease=self.activeLease
        if lease is None:return
        lease['closedT']=int(t);lease['closeReason']=str(reason);lease['lifeMs']=int(t)-int(lease['bornT'])
        self.leaseRows.append(lease);self.r253['LEASE_CLOSED_'+str(reason)]+=1;self.activeLease=None

    def _open_one_option(self,t,qv,end):
        self._lastEnd=int(end)
        # Exact R2.47 acts first. Audit never submits anything.
        super()._open_one_option(t,qv,end)
        self._maybe_birth(t,end)
        self._snapshot(t)

    def run_audit(self,w):
        r=super().run_r247(w)
        if self.activeLease is not None:self._close_lease(int(self.meta['lastReceivedMs']),'MARKET_END')
        # marketId injected by outer runner after completion
        for x in self.leaseRows:x['terminalPnlDiagnosticOnly']=float(r['pnlDiagnosticOnly']);x['terminalFloor']=float(r['floor']);x['terminalBest']=float(r['best'])
        r['r253Stats']=dict(self.r253);r['r253Leases']=self.leaseRows;return r

def summarize(leases):
    n=len(leases);safe=lambda xs:sum(bool(x) for x in xs)
    def frac(k):return (safe([x.get(k) for x in leases])/n) if n else None
    out={'leases':n,'everRank1':safe([x['everRank1'] for x in leases]),'everRank2':safe([x['everRank2'] for x in leases]),
         'everMoreAggressive':safe([x['everMoreAggressive'] for x in leases]),
         'medianLifeMs':statistics.median([x['lifeMs'] for x in leases]) if leases else None,
         'medianBirthRank':statistics.median([x['birth']['rank'] for x in leases]) if leases else None,
         'medianMinRank':statistics.median([x['minRank'] for x in leases]) if leases else None,
         'medianFirstRank2Ms':statistics.median([x['firstRank2Ms'] for x in leases if x['firstRank2Ms'] is not None]) if any(x['firstRank2Ms'] is not None for x in leases) else None,
         'medianFirstRank1Ms':statistics.median([x['firstRank1Ms'] for x in leases if x['firstRank1Ms'] is not None]) if any(x['firstRank1Ms'] is not None for x in leases) else None,
         'closedByScopeLeft':sum(x['closeReason']=='SCOPE_LEFT' for x in leases),'closedByMarketEnd':sum(x['closeReason']=='MARKET_END' for x in leases)}
    for h in HORIZONS:
        hs=str(h);obs=[x for x in leases if x['horizonReached'][hs]];out[f'h{h}Observed']=len(obs);out[f'h{h}Safe']=sum(bool(x['horizonSafe'][hs]) for x in obs);out[f'h{h}SafeRate']=sum(bool(x['horizonSafe'][hs]) for x in obs)/len(obs) if obs else None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r253_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];leases=[]
        for mid in mids:
            cr=co[mid];sim=FavorablePairLeaseReachabilityAudit(tmp/'tapes'/f'{mid}.json.xz',1,4)
            try:r=sim.run_audit(cr['winner'])
            finally:sim.close()
            for x in r['r253Leases']:x['marketId']=mid
            leases.extend(r['r253Leases']);rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r})
            if r['r253Leases']:
                print(json.dumps({'marketId':mid,'leases':len(r['r253Leases']),'summary':summarize(r['r253Leases'])},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_53_FAVORABLE_PAIR_LEASE_REACHABILITY_AUDIT_V1','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'markets':mids,'aggregate':summarize(leases),'leases':leases,'rows':rows,
             'boundary':['exact R2.47 behavior','audit never submits/cancels/reprices/reserves lots','one shadow lease per scope generation','birth only after ordinary R2.47 first refusal','full venue-min qty actual Repair-lot coverage and pairSum<=1 every unit','later snapshots recompute after pending ordinary Expand claims','no fixed age/tick gate inferred','no winner/future/Target runtime input']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':out['aggregate'],'rank2Markets':sorted(set(x['marketId'] for x in leases if x['everRank2'])),'rank1Markets':sorted(set(x['marketId'] for x in leases if x['everRank1']))},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
