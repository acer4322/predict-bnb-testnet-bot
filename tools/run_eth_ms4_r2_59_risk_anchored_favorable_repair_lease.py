from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
r255=r257.r255; r247=r257.r247; v2=r257.v2; EPS=1e-9

class RiskAnchoredFavorableRepairLeaseSim(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r259Events=[];self.r259FavorableCarrierSubmits=0;self.r259FavorableCarrierFills=0
        self.r259NoLiveFavorablePrice=0

    def _try_risk_repair_carrier(self,t):
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if self._has_stale_scope_reservation() or self._has_live_risk_repair_carrier(gen):return False
        repair_side=self._repair_side()
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return False
        risk_key=str(ob['bornFromRiskKey']);risk_meta=self.riskTrancheMeta.get(risk_key)
        if not risk_meta:return False
        risk_price=float(risk_meta['price']);ceiling=1.0-risk_price
        chosen=None
        for raw in self._live_price_levels(repair_side):
            p=float(v2.kprice(raw))
            if p<=EPS or p>ceiling+EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            split=self._repair_split(repair_side,p,q)
            if split is None or float(split.get('repairQty') or 0.0)<=EPS:continue
            chosen=(p,q,split['fullFloor'],split);break
        if chosen is None:
            self.r259NoLiveFavorablePrice+=1
            return False
        p,q,proj,split=chosen;before_n=self.n
        if not self._submit_role_v8(t,repair_side,'ECONOMIC_CORE',p,q,proj,split):return False
        key=f'{repair_side}_{before_n}';self.riskRepairCarrierKeys.add(key)
        ob['carrierSubmits']+=1;ob['lastCarrierKey']=key;ob['lastCarrierSubmitAt']=int(t)
        self.r259FavorableCarrierSubmits+=1
        ev={'t':int(t),'event':'R259_FAVORABLE_REPAIR_CARRIER_SUBMIT','generation':gen,'key':key,
            'riskKey':risk_key,'riskPrice':risk_price,'repairPrice':float(p),'pairSum':risk_price+float(p),
            'qty':float(q),'repairQuota':float(split['repairQty']),'outstandingBefore':float(ob['outstanding'])}
        self.r259Events.append(ev);self.slot_history.append(ev);return True

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.riskRepairCarrierKeys and inc>EPS:
                # Dedicated favorable lane keys are all selected by anchored ceiling.
                self.r259FavorableCarrierFills+=1
                gen=int(ev.get('generationAtSubmit') or -1);ob=self.riskRepairObligations.get(gen)
                risk_price=None
                if ob:risk_price=float(self.riskTrancheMeta.get(str(ob['bornFromRiskKey']),{}).get('price',0.0))
                self.r259Events.append({'t':int(t),'event':'R259_FAVORABLE_REPAIR_CARRIER_FILL','key':key,
                    'generation':gen,'fillQty':inc,'repairAllocated':float(ev.get('repairAllocated') or 0.0),
                    'repairPrice':float(ev.get('price') or 0.0),'riskPrice':risk_price,
                    'pairSum':None if risk_price is None else risk_price+float(ev.get('price') or 0.0)})

    def run_r259(self,winner):
        r=super().run_r257(winner)
        pairs=[x['pairSum'] for x in self.r259Events if x.get('event')=='R259_FAVORABLE_REPAIR_CARRIER_FILL' and x.get('pairSum') is not None]
        r.update({'r259Version':'MS4_R2_59_RISK_ANCHORED_FAVORABLE_REPAIR_LEASE_V1',
            'r259Events':self.r259Events[:4000],'r259FavorableCarrierSubmits':self.r259FavorableCarrierSubmits,
            'r259FavorableCarrierFills':self.r259FavorableCarrierFills,'r259NoLiveFavorablePrice':self.r259NoLiveFavorablePrice,
            'r259FilledPairSums':pairs})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r259_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=RiskAnchoredFavorableRepairLeaseSim(tape,1,4)
            try:c=sim.run_r259(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},{'marketId':mid,'cell':'R259_FAVORABLE_REPAIR_LEASE','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'riskFills':c['riskTrancheFillEvents'],'favRepairSubmits':c['r259FavorableCarrierSubmits'],
               'favRepairFills':c['r259FavorableCarrierFills'],'filledPairSums':c['r259FilledPairSums'],
               'obligationsRepaid':c['riskRepairObligationsRepaid'],'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'pnlDeltaVsR257':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDeltaVsR257':float(c['floor'])-float(b['floor']),
               'gapDeltaVsR257':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDeltaVsR257':int(c['fillEvents'])-int(b['fillEvents']),'submitDeltaVsR257':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_59_RISK_ANCHORED_FAVORABLE_REPAIR_LEASE_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'riskFillExercised':any(x['riskFills']>0 for x in cmp),
                      'favorableRepairSubmitExercised':any(x['favRepairSubmits']>0 for x in cmp),
                      'favorableRepairFillExercised':any(x['favRepairFills']>0 for x in cmp)},
             'boundary':['risk entry unchanged/un-gated','pair<=1 used only for extra post-risk passive Repair price selection','native Repair/Active never vetoed','rolling favorable passive carrier','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
