from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9

class ExecutionFirstAltPriceCausalSim(r247.BoundedCoreServiceFavorableRecycleSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r252=Counter();self.r252Events=[];self.altMaterialized=False;self.altIntervention=None
    def _match_no_mutation(self,side,p,q):
        claims=self._pending_ordinary_claims();need=float(q);used=[]
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if need<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(p)>1.0+EPS:continue
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            take=min(need,free);need-=take;used.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+float(p)})
        gain=sum((1.0-u['pairSum'])*u['qty'] for u in used)
        return used,need,gain
    def _try_favorable_replenishment(self,t,end):
        if super()._try_favorable_replenishment(t,end):return True
        if self.altMaterialized:return False
        if int(end)-int(t)<=r247.v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None:return False
        if self._has_stale_scope_reservation() or self._has_live_replenishment():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        used_prices=self._used_prices(side);levels=[]
        for raw in self._live_price_levels(side):
            p=float(r247.v2.kprice(raw))
            if p in used_prices or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS:continue
            risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
            credit=float(self._available_expand_risk_credit())
            lots,left,gain=self._match_no_mutation(side,p,q)
            levels.append({'p':p,'q':q,'risk':risk,'credit':credit,'lots':lots,'left':left,'gain':gain})
        if len(levels)<=1:return False
        default=levels[0]
        alts=[z for z in levels[1:] if z['left']<=EPS and z['credit']+EPS<z['risk']]
        if not alts:return False
        best=max(alts,key=lambda z:(float(z['p']),float(z['gain'])))
        res=self._reserve_lots(side,best['p'],best['q'])
        if not res:return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',best['p'],best['q'],self._candidate_alone_floor(side,best['p'],best['q']),None):
            self._rollback_lots(res);return False
        key=f'{side}_{before_n}';self.replenishmentKeys.add(key);self.keyLotReservations[key]=res;self.altMaterialized=True;self.r252['ALT_FULL_COVER_SUBMIT']+=1
        ev={'t':int(t),'event':'R252_EXECUTION_FIRST_ALT_PRICE_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,
            'defaultPrice':float(default['p']),'defaultQty':float(default['q']),'defaultEligibleQty':float(default['q']-default['left']),
            'altPrice':float(best['p']),'altQty':float(best['q']),'matchedPairGainAtSubmit':float(best['gain']),
            'availableMonetaryCredit':float(best['credit']),'riskCost':float(best['risk']),
            'reservedLots':[{'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),'qty':float(a['authorized']),'pairSum':float(a['pairSum'])} for a in res],
            'floorBefore':float(self._physical_floor()),'bestBefore':float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost))}
        self.altIntervention=ev;self.r252Events.append(ev);self.slot_history.append(ev);return True
    def run_r252(self,w):
        r=super().run_r247(w);r.update({'r252Stats':dict(self.r252),'r252Events':self.r252Events[:1000],
            'r252AltMaterialized':bool(self.altMaterialized),'r252AltIntervention':self.altIntervention});return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r252_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';w=cr['winner']
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            csim=ExecutionFirstAltPriceCausalSim(tape,1,4)
            try:c=csim.run_r252(w)
            finally:csim.close()
            rows += [{'marketId':mid,'cell':'MS4_R247_CONTROL','winnerPostHocOnly':w,**b},{'marketId':mid,'cell':'MS4_R252_EXECUTION_FIRST_ALT_PRICE','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'altMaterialized':bool(c['r252AltMaterialized']),'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],
               'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'gapDelta':(c['best']-c['floor'])-(b['best']-b['floor']),
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],'correct':bool(c.get('r247ServiceCorrectnessPass')),
               'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0)),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0)),
               'intervention':c.get('r252AltIntervention')}
            cmp.append(d);print(json.dumps({k:v for k,v in d.items() if k!='intervention'},ensure_ascii=False),flush=True)
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R252_EXECUTION_FIRST_ALT_PRICE'}
        gates={'correctnessPass':all(bool(C[m].get('r247ServiceCorrectnessPass')) and float(C[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(C[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
               'atMostOneAltSubmit':all(int(C[m].get('r252Stats',{}).get('ALT_FULL_COVER_SUBMIT',0))<=1 for m in mids),'altExercised':any(bool(C[m].get('r252AltMaterialized')) for m in mids)}
        out={'version':'MS4_R2_52_EXECUTION_FIRST_ALT_PRICE_CAUSAL_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsR247':cmp,'gates':gates,
             'boundary':['R2.47 exact control','ordinary/default R2.47 path first','at most one added alternative-price full-covered replenishment per market','current strict-past unused Maker price levels only','full venue-min quantity covered by one-use same-generation actual Repair lots','every reserved pair unit repairPrice+expandPrice<=1','no monetary substitution for missing Repair qty','legal set unchanged from R2.51; alternative price ranked by highest current Maker price, matched pair gain tie-breaker','<=180s unchanged','no winner/future/Target input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'gapDelta':sum(x['gapDelta'] for x in cmp)}},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
