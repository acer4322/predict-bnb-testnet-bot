from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
v2=r257.v2; EPS=1e-9

class RiskAwareFanoutPriceRoutingSim(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r261Events=[];self.r261FavChosen=0;self.r261FallbackChosen=0;self.r261Fills=0
        self.r261PairSums=[];self.r261Keys=set()

    def _parallel_repair_fill(self,t:int,side:str):
        ob=self._obligation_current()
        if ob is None:
            return super()._parallel_repair_fill(t,side)
        made=0
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:
            self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        risk_meta=self.riskTrancheMeta.get(str(ob['bornFromRiskKey'])) or {}
        risk_price=float(risk_meta.get('price') or 0.0);ceiling=1.0-risk_price
        while len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
            if self._live_fanout_count()>=self.fanoutLimit:
                self.capacityBlocks+=1;break
            used=self._used_prices(side);fav=[];fallback=[]
            for raw in self._live_price_levels(side):
                p=float(v2.kprice(raw))
                if p in used or p<=EPS:continue
                q=1.0/p
                if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
                sp=self._repair_split(side,p,q)
                if sp is None:continue
                if float(sp.get('overflowQty') or 0.0)>EPS:
                    self.r26['FANOUT_OVERFLOW_NOT_ALLOWED']+=1;continue
                row=(p,q,sp)
                fallback.append(row)
                if p<=ceiling+EPS:fav.append(row)
            chosen=(fav[0] if fav else (fallback[0] if fallback else None))
            if chosen is None:
                self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;break
            p,q,sp=chosen;before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
                self.r26['FANOUT_SUBMIT_BLOCKED']+=1;break
            key=f'{side}_{before_n}';self.fanoutKeys.add(key);self.r261Keys.add(key);made+=1;self.r26['FANOUT_SUBMIT']+=1
            favorable=p<=ceiling+EPS
            if favorable:self.r261FavChosen+=1
            else:self.r261FallbackChosen+=1
            ev={'t':int(t),'event':'R261_RISK_AWARE_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),
                'side':side,'riskPrice':risk_price,'repairPrice':p,'pairSum':risk_price+p,
                'favorableChosen':favorable,'qty':q,'outstanding':float(ob['outstanding'])}
            self.r261Events.append(ev);self.slot_history.append(ev)
        return made

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if inc<=EPS or key not in self.r261Keys:continue
            gen=int(ev.get('generationAtSubmit') or -1);ob=self.riskRepairObligations.get(gen)
            if not ob:continue
            rm=self.riskTrancheMeta.get(str(ob['bornFromRiskKey'])) or {};rp=float(rm.get('price') or 0.0)
            pair=rp+float(ev.get('price') or 0.0);self.r261Fills+=1;self.r261PairSums.append(pair)
            self.r261Events.append({'t':int(t),'event':'R261_RISK_AWARE_FANOUT_FILL','key':key,'generation':gen,
                'fillQty':inc,'repairAllocated':float(ev.get('repairAllocated') or 0.0),'riskPrice':rp,
                'repairPrice':float(ev.get('price') or 0.0),'pairSum':pair})

    def run_r261(self,winner):
        r=super().run_r257(winner)
        r.update({'r261Version':'MS4_R2_61_RISK_AWARE_FANOUT_PRICE_ROUTING_V1','r261Events':self.r261Events[:4000],
            'r261FavorableSubmits':self.r261FavChosen,'r261FallbackSubmits':self.r261FallbackChosen,
            'r261FanoutFills':self.r261Fills,'r261FilledPairSums':self.r261PairSums})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r261_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=RiskAwareFanoutPriceRoutingSim(tape,1,4)
            try:c=sim.run_r261(w)
            finally:sim.close()
            rows += [{'marketId':m,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},{'marketId':m,'cell':'R261_RISK_AWARE_FANOUT','winnerPostHocOnly':w,**c}]
            d={'marketId':m,'riskFills':c['riskTrancheFillEvents'],'favSubmits':c['r261FavorableSubmits'],
               'fallbackSubmits':c['r261FallbackSubmits'],'fanoutFills':c['r261FanoutFills'],'pairSums':c['r261FilledPairSums'],
               'obligationsRepaid':c['riskRepairObligationsRepaid'],'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'pnlDeltaVsR257':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDeltaVsR257':float(c['floor'])-float(b['floor']),
               'gapDeltaVsR257':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDeltaVsR257':int(c['fillEvents'])-int(b['fillEvents']),'submitDeltaVsR257':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_61_RISK_AWARE_FANOUT_PRICE_ROUTING_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'riskFillExercised':any(x['riskFills']>0 for x in cmp),
                      'favorableRoutingExercised':any(x['favSubmits']>0 for x in cmp),'fanoutFillExercised':any(x['fanoutFills']>0 for x in cmp)},
             'boundary':['risk entry ungated','existing fanoutLimit=1 only','favorable price is a routing preference with immediate fallback, never a Repair veto','ordinary Core/Active unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
