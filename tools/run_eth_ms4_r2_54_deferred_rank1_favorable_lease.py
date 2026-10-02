from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9

class DeferredRank1FavorableLeaseSim(r247.BoundedCoreServiceFavorableRecycleSim):
    """R2.54 research-only deferred favorable lease.

    No order/lot/credit reservation while waiting.  Exact R2.47 acts first on
    every receipt.  At most one additional order per market materializes only
    when a fully Repair-quantity-covered favorable unused price reaches rank 1.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r254=Counter();self.r254Events=[];self.lease=None;self.seenLeaseGen=set();self.materialized=False;self.materializedKey=None

    def _full_cover_levels(self,side):
        used=self._used_prices(side);claims=self._pending_ordinary_claims();out=[]
        for raw in self._live_price_levels(side):
            p=float(r247.v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS:continue
            need=q;lots=[]
            for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
                if need<=EPS:break
                if x['side']!=side or float(x['repairPrice'])+p>1.0+EPS:continue
                free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
                if free<=EPS:continue
                take=min(need,free);need-=take
                lots.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+p})
            if need<=EPS:
                gain=sum((1.0-float(z['pairSum']))*float(z['qty']) for z in lots)
                out.append({'price':p,'qty':q,'rank':int(self._rank(side,p)),'depth':float(self._depth(side,p)),'gain':gain,'lots':lots})
        out.sort(key=lambda z:(-float(z['price']),int(z['rank']),-float(z['gain'])))
        return out

    def _default_fully_covered(self,side):
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand;p=float(p);q=float(q);claims=self._pending_ordinary_claims();need=q
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if need<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+p>1.0+EPS:continue
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            need-=min(need,free)
        return need<=EPS

    def _maybe_birth_or_drop(self,t,end):
        if self.materialized:return
        if self.lease is not None:
            if self.scopeSide!=self.lease['scopeSide'] or int(self.scopeGeneration)!=int(self.lease['generation']):
                self.r254['LEASE_DROP_SCOPE_LEFT']+=1
                self.r254Events.append({'t':int(t),'event':'R254_LEASE_DROP_SCOPE_LEFT',**self.lease})
                self.lease=None
            return
        if self.scopeSide is None:return
        gen=int(self.scopeGeneration)
        if gen in self.seenLeaseGen or int(end)-int(t)<=r247.v2.NO_NEW_EXPOSURE_MS:return
        if self._last_new_receipt==int(t) or self._has_stale_scope_reservation() or self._has_live_replenishment():return
        side=str(self.scopeSide)
        if self._default_fully_covered(side):return
        levels=self._full_cover_levels(side)
        if not levels:return
        z=levels[0];self.seenLeaseGen.add(gen)
        self.lease={'generation':gen,'scopeSide':side,'bornT':int(t),'birthPrice':float(z['price']),'birthRank':int(z['rank']),'birthDepth':float(z['depth'])}
        self.r254['LEASE_BORN']+=1;self.r254Events.append({'t':int(t),'event':'R254_LEASE_BORN',**self.lease})

    def _try_materialize_rank1(self,t,end):
        if self.materialized or self.lease is None:return False
        if int(end)-int(t)<=r247.v2.NO_NEW_EXPOSURE_MS:return False
        if self._last_new_receipt==int(t) or self._has_stale_scope_reservation() or self._has_live_replenishment():return False
        if self.scopeSide!=self.lease['scopeSide'] or int(self.scopeGeneration)!=int(self.lease['generation']):return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        levels=self._full_cover_levels(side)
        if not levels:return False
        z=levels[0]
        if int(z['rank'])!=1:
            self.r254['LEASE_WAIT_NOT_RANK1']+=1;return False
        res=self._reserve_lots(side,float(z['price']),float(z['qty']))
        if not res:return False
        before_n=self.n
        proj=float(self._candidate_alone_floor(side,float(z['price']),float(z['qty'])))
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',float(z['price']),float(z['qty']),proj,None):
            self._rollback_lots(res);return False
        key=f'{side}_{before_n}';self.replenishmentKeys.add(key);self.keyLotReservations[key]=res
        self.materialized=True;self.materializedKey=key;self.r254['RANK1_SUBMIT']+=1
        ev={'t':int(t),'event':'R254_DEFERRED_RANK1_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,
            'price':float(z['price']),'qty':float(z['qty']),'rank':int(z['rank']),'depth':float(z['depth']),'matchedGainAtSubmit':float(z['gain']),
            'waitMs':int(t)-int(self.lease['bornT']),'birthPrice':self.lease['birthPrice'],'birthRank':self.lease['birthRank'],
            'reservedLots':[{'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),'qty':float(a['authorized']),'pairSum':float(a['pairSum'])} for a in res]}
        self.r254Events.append(ev);self.slot_history.append(ev);return True

    def _open_one_option(self,t,qv,end):
        # Exact R2.47 always gets first refusal.
        super()._open_one_option(t,qv,end)
        self._maybe_birth_or_drop(t,end)
        self._try_materialize_rank1(t,end)

    def run_r254(self,w):
        r=super().run_r247(w)
        fill=0;fillq=0.0
        if self.materializedKey is not None:
            a=float(self.keyOverflowQtyAuthorized.get(self.materializedKey,0.0));rem=float(self.keyOverflowQtyRemaining.get(self.materializedKey,a));fillq=max(0.0,a-rem);fill=int(fillq>EPS)
        r.update({'r254Stats':dict(self.r254),'r254Events':self.r254Events[:2000],'r254Lease':self.lease,
                  'r254Materialized':bool(self.materialized),'r254MaterializedKey':self.materializedKey,'r254Fill':fill,'r254FillQty':fillq})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r254_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';w=cr['winner']
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            csim=DeferredRank1FavorableLeaseSim(tape,1,4)
            try:c=csim.run_r254(w)
            finally:csim.close()
            rows += [{'marketId':mid,'cell':'MS4_R247_CONTROL','winnerPostHocOnly':w,**b},{'marketId':mid,'cell':'MS4_R254_DEFERRED_RANK1_LEASE','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'leaseBorn':int(c.get('r254Stats',{}).get('LEASE_BORN',0)),'materialized':bool(c['r254Materialized']),
               'fill':int(c['r254Fill']),'fillQty':float(c['r254FillQty']),'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],
               'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'gapDelta':(c['best']-c['floor'])-(b['best']-b['floor']),
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],'correct':bool(c.get('r247ServiceCorrectnessPass')),
               'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0)),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R254_DEFERRED_RANK1_LEASE'}
        gates={'correctnessPass':all(bool(C[m].get('r247ServiceCorrectnessPass')) and float(C[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(C[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
               'atMostOneSubmit':all(int(C[m].get('r254Stats',{}).get('RANK1_SUBMIT',0))<=1 for m in mids),'mechanismExercised':any(bool(C[m]['r254Materialized']) for m in mids)}
        out={'version':'MS4_R2_54_DEFERRED_RANK1_FAVORABLE_LEASE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsR247':cmp,'gates':gates,
             'boundary':['R2.47 exact control','one behavior-inert lease per generation until rank1','no order/lot/credit reservation while waiting','ordinary R2.47 first refusal every receipt','materialize only current rank1 unused Maker price with full venue-min actual Repair-lot coverage and pairSum<=1 every unit','at most one added rank1 submit per market','no age threshold','no hybrid authority','<=180s unchanged','no winner/future/Target runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'gapDelta':sum(x['gapDelta'] for x in cmp),'fillDelta':sum(x['fillDelta'] for x in cmp)}},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
