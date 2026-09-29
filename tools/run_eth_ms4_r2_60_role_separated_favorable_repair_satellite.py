from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
v2=r257.v2; EPS=1e-9

class RoleSeparatedFavorableRepairSatelliteSim(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.favSatKeys=set();self.favSatMeta={};self.r260Events=[];self.lastFavAttemptPrice={}
        self.r260Submits=0;self.r260Fills=0;self.r260ZeroFill=0

    def _has_live_fav_sat(self,gen):
        for key in list(self.favSatKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(gen):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False

    def _try_favorable_satellite(self,t):
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if self._has_stale_scope_reservation() or self._has_live_fav_sat(gen):return False
        if self._live_fanout_count()>=self.fanoutLimit:return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return False
        risk_key=str(ob['bornFromRiskKey']);rm=self.riskTrancheMeta.get(risk_key)
        if not rm:return False
        risk_price=float(rm['price']);ceiling=1.0-risk_price;side=self._repair_side();used=self._used_prices(side)
        chosen=None
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p<=EPS or p>ceiling+EPS or p in used:continue
            if self.lastFavAttemptPrice.get(gen) is not None and abs(float(self.lastFavAttemptPrice[gen])-p)<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            split=self._repair_split(side,p,q)
            if split is None or float(split.get('repairQty') or 0.0)<=EPS:continue
            if float(split.get('overflowQty') or 0.0)>EPS:continue
            chosen=(p,q,split['fullFloor'],split);break
        if chosen is None:return False
        p,q,proj,split=chosen;before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,proj,split):return False
        key=f'{side}_{before_n}';self.favSatKeys.add(key);self.fanoutKeys.add(key);self.lastFavAttemptPrice[gen]=p
        self.favSatMeta[key]={'generation':gen,'riskKey':risk_key,'riskPrice':risk_price,'repairPrice':p,'pairSum':risk_price+p,'submittedAt':int(t)}
        self.r260Submits+=1
        ev={'t':int(t),'event':'R260_FAVORABLE_REPAIR_SATELLITE_SUBMIT','key':key,'generation':gen,
            'riskPrice':risk_price,'repairPrice':p,'pairSum':risk_price+p,'qty':q,'outstanding':float(ob['outstanding'])}
        self.r260Events.append(ev);self.slot_history.append(ev);return True

    def _open_one_option(self,t,qv,end):
        # Preserve R2.57 ordinary Core priority and frozen R2.47 activity.
        super()._open_one_option(t,qv,end)
        # Secondary favorable lane may use only truly spare fanout capacity.
        self._try_favorable_satellite(t)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.favSatMeta and inc>EPS:
                self.r260Fills+=1;m=self.favSatMeta[key]
                self.r260Events.append({'t':int(t),'event':'R260_FAVORABLE_REPAIR_SATELLITE_FILL','key':key,
                    'generation':m['generation'],'fillQty':inc,'repairAllocated':float(ev.get('repairAllocated') or 0.0),
                    'riskPrice':m['riskPrice'],'repairPrice':float(ev.get('price') or 0.0),
                    'pairSum':m['riskPrice']+float(ev.get('price') or 0.0)})
        for key in list(self.favSatKeys):
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            except Exception:continue
            mark='terminalSeen:'+key
            if st in v2.TERMINAL_STATUSES and cum<=EPS and not self.favSatMeta[key].get(mark):
                self.favSatMeta[key][mark]=True;self.r260ZeroFill+=1
                self.r260Events.append({'t':int(t),'event':'R260_FAVORABLE_REPAIR_SATELLITE_ZERO_FILL','key':key,'status':st})

    def run_r260(self,winner):
        r=super().run_r257(winner)
        pairs=[x['pairSum'] for x in self.r260Events if x.get('event')=='R260_FAVORABLE_REPAIR_SATELLITE_FILL']
        r.update({'r260Version':'MS4_R2_60_ROLE_SEPARATED_FAVORABLE_REPAIR_SATELLITE_V1',
            'r260Events':self.r260Events[:4000],'r260Submits':self.r260Submits,'r260Fills':self.r260Fills,
            'r260ZeroFillTerminals':self.r260ZeroFill,'r260FilledPairSums':pairs})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r260_'))
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
            sim=RoleSeparatedFavorableRepairSatelliteSim(tape,1,4)
            try:c=sim.run_r260(w)
            finally:sim.close()
            rows += [{'marketId':m,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},{'marketId':m,'cell':'R260_FAVORABLE_SATELLITE','winnerPostHocOnly':w,**c}]
            d={'marketId':m,'riskFills':c['riskTrancheFillEvents'],'favSubmits':c['r260Submits'],'favFills':c['r260Fills'],
               'favPairSums':c['r260FilledPairSums'],'obligationsRepaid':c['riskRepairObligationsRepaid'],
               'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'pnlDeltaVsR257':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDeltaVsR257':float(c['floor'])-float(b['floor']),
               'gapDeltaVsR257':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDeltaVsR257':int(c['fillEvents'])-int(b['fillEvents']),'submitDeltaVsR257':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_60_ROLE_SEPARATED_FAVORABLE_REPAIR_SATELLITE_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'riskFillExercised':any(x['riskFills']>0 for x in cmp),
                      'favorableSatelliteSubmitExercised':any(x['favSubmits']>0 for x in cmp),'favorableSatelliteFillExercised':any(x['favFills']>0 for x in cmp)},
             'boundary':['R2.57 ordinary Core/Active unchanged','favorable pair only adds pure Repair satellite on spare existing fanout capacity','risk entry ungated','same-price zero-fill not blindly resubmitted','no extra slot capacity','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
