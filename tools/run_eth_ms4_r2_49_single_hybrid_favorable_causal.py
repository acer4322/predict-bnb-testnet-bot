from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
EPS=1e-9

class SingleHybridFavorableCausalSim(r247.BoundedCoreServiceFavorableRecycleSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r249=Counter();self.r249Events=[];self.hybridMaterialized=False;self.hybridIntervention=None
    def _try_favorable_replenishment(self,t,end):
        # Preserve exact R2.47 full-lot path first.
        if super()._try_favorable_replenishment(t,end):return True
        if self.hybridMaterialized:return False
        if int(end)-int(t)<=r247.v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None:return False
        if self._has_stale_scope_reservation() or self._has_live_replenishment():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand; p=float(p);q=float(q)
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
        credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return False
        elig=float(self._eligible_qty(side,p))
        if elig<=EPS or elig+EPS>=q:return False
        pair_need=max(0.0,(risk-credit)/p) if p>EPS else math.inf
        if not math.isfinite(pair_need) or pair_need<=EPS or pair_need>elig+EPS or pair_need>=q-EPS:return False
        res=self._reserve_lots(side,p,pair_need)
        if not res:return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self._rollback_lots(res);return False
        key=f'{side}_{before_n}'
        monetary_qty=max(0.0,q-pair_need)
        self.hybridKeys.add(key);self.keyLotReservations[key]=res;self.hybridMeta[key]={
            'key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,
            'pairQtyAuthorized':pair_need,'pairQtyRemaining':pair_need,
            'monetaryQtyAuthorized':monetary_qty,'monetaryQtyRemaining':monetary_qty,
            'riskCost':risk,'monetaryCreditAtSubmit':credit,
        }
        self.hybridMaterialized=True;self.r249['HYBRID_SUBMIT']+=1
        ev={'t':int(t),'event':'R249_SINGLE_HYBRID_FAVORABLE_SUBMIT','key':key,'generation':int(self.scopeGeneration),
            'side':side,'expandPrice':p,'expandQty':q,'riskCost':risk,'availableMonetaryCredit':credit,
            'pairQtyNeeded':pair_need,'monetaryQty':monetary_qty,'monetaryRisk':monetary_qty*p,
            'reservedLots':[{'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),'qty':float(a['authorized']),'pairSum':float(a['pairSum'])} for a in res],
            'floorBefore':float(self._physical_floor()),'bestBefore':float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost))}
        self.hybridIntervention=ev;self.r249Events.append(ev);self.slot_history.append(ev);return True
    def run_r249(self,w):
        r=super().run_r247(w);hfill=int(r.get('r247Stats',{}).get('HYBRID_FAVORABLE_FILL',0));
        r.update({'r249Stats':dict(self.r249),'r249Events':self.r249Events[:1000],'r249HybridMaterialized':bool(self.hybridMaterialized),
                  'r249HybridIntervention':self.hybridIntervention,'r249HybridFillEvents':hfill,
                  'r249HybridMeta':{k:dict(v) for k,v in self.hybridMeta.items()}})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r249_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';w=cr['winner']
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            csim=SingleHybridFavorableCausalSim(tape,1,4)
            try:c=csim.run_r249(w)
            finally:csim.close()
            rows += [{'marketId':mid,'cell':'MS4_R247_CONTROL','winnerPostHocOnly':w,**b},{'marketId':mid,'cell':'MS4_R249_SINGLE_HYBRID','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'hybridMaterialized':bool(c['r249HybridMaterialized']),'hybridFillEvents':int(c['r249HybridFillEvents']),
               'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],
               'gapDelta':(c['best']-c['floor'])-(b['best']-b['floor']),'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'correct':bool(c.get('r247ServiceCorrectnessPass')),'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0)),
               'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0)),'intervention':c.get('r249HybridIntervention')}
            cmp.append(d);print(json.dumps({k:v for k,v in d.items() if k!='intervention'},ensure_ascii=False),flush=True)
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R249_SINGLE_HYBRID'}
        gates={'correctnessPass':all(bool(C[m].get('r247ServiceCorrectnessPass')) and float(C[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(C[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
               'atMostOneHybridSubmit':all(int(C[m].get('r249Stats',{}).get('HYBRID_SUBMIT',0))<=1 for m in mids),
               'hybridExercised':any(bool(C[m].get('r249HybridMaterialized')) for m in mids)}
        out={'version':'MS4_R2_49_SINGLE_HYBRID_FAVORABLE_CAUSAL_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsR247':cmp,'gates':gates,
             'boundary':['R2.47 exact control','R2.47 full-lot favorable replenishment retains first priority','at most one added partial-hybrid submit per market','hybrid uses minimum favorable Repair-lot qty needed to cover existing monetary-credit shortfall','pair-covered fill reverses only corresponding native Expand credit spend','unmatched fill remains native monetary credit spend','no borrowed/free credit','no pair veto on ordinary actions','<=180s unchanged','no winner/future/Target runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'gapDelta':sum(x['gapDelta'] for x in cmp)}},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
