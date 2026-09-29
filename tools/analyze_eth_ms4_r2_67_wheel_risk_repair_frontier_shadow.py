from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys,statistics
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_65_marketwide_loss_bearing_risk_wheel as r265
v2=r265.v2; EPS=r265.EPS

class WheelRiskRepairFrontierShadowSim(r265.MarketWideLossBearingRiskWheelSim):
    """Behavior-inert per-wheel-submit risk/Repair price-frontier audit."""
    def __init__(self,*a,**kw):
        self.r267Events=[];self.r267=Counter()
        super().__init__(*a,**kw)

    def _frontier_snapshot(self,t):
        if self.scopeSide is None:return None
        risk_side=str(self.scopeSide); repair_side=self._repair_side()
        risk_levels=[];repair_levels=[]
        used_risk=self._used_prices(risk_side)
        for rank,p in enumerate(self._live_price_levels(risk_side),1):
            p=float(v2.kprice(p));q=1.0/p if p>EPS else math.inf
            if math.isfinite(q) and 0<q<=12+EPS and v2.kprice(p) not in used_risk:
                risk_levels.append({'rank':rank,'price':p,'qty':q})
        for rank,p in enumerate(self._live_price_levels(repair_side),1):
            p=float(v2.kprice(p));q=1.0/p if p>EPS else math.inf
            if math.isfinite(q) and 0<q<=12+EPS:repair_levels.append({'rank':rank,'price':p,'qty':q})
        if not risk_levels or not repair_levels:return None
        rp=risk_levels[0]['price'];default=rp+repair_levels[0]['price']
        combos=[{'riskRank':a['rank'],'repairRank':b['rank'],'riskPrice':a['price'],'repairPrice':b['price'],'pairSum':a['price']+b['price']}
                for a in risk_levels[:4] for b in repair_levels[:4]]
        return {'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':risk_side,
                'riskPrice':rp,'riskQty':risk_levels[0]['qty'],'repairRank1Price':repair_levels[0]['price'],
                'defaultPairSum':default,'bestTop4Pair':min(combos,key=lambda x:(x['pairSum'],x['riskRank']+x['repairRank'])),
                'pairLe1Top2':any(x['pairSum']<=1+EPS and x['riskRank']<=2 and x['repairRank']<=2 for x in combos),
                'wheelLossBurnBefore':float(self.wheelLossBurn),'wheelFreeBefore':float(self._wheel_free()),
                'nativeFloorBefore':float(self._physical_floor()),'nativeDebtBefore':float(self._scope_debt_qty())}

    def _try_pre_repair_risk_tranche(self,t,end):
        snap=self._frontier_snapshot(t)
        before=int(self.r265.get('WHEEL_RISK_SUBMIT',0))
        ok=super()._try_pre_repair_risk_tranche(t,end)
        if ok and int(self.r265.get('WHEEL_RISK_SUBMIT',0))>before and snap is not None:
            self.r267['WHEEL_SUBMIT']+=1
            self.r267['DEFAULT_PAIR_LE1']+=int(snap['defaultPairSum']<=1+EPS)
            self.r267['TOP2_PAIR_LE1']+=int(snap['pairLe1Top2'])
            self.r267Events.append({'event':'R267_WHEEL_RISK_REPAIR_FRONTIER_SHADOW',**snap})
        return ok

    def run_shadow(self,w):
        r=super().run_r265(w);r.update({'r267Stats':dict(self.r267),'r267Events':self.r267Events[:5000]});return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r267_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];summary=[]
        for m in mids:
            s=WheelRiskRepairFrontierShadowSim(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(co[m]['winner'])
            finally:s.close()
            ev=r['r267Events'];pairs=[float(x['defaultPairSum']) for x in ev]
            row={'marketId':m,'pnl':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),'fills':int(r['fillEvents']),
                 'wheelSubmits':int(r['wheelRiskSubmits']),'frontierSubmits':len(ev),
                 'defaultPairLe1':sum(x<=1+EPS for x in pairs),'defaultPairGt1':sum(x>1+EPS for x in pairs),
                 'medianDefaultPairSum':statistics.median(pairs) if pairs else None,
                 'meanDefaultPairSum':sum(pairs)/len(pairs) if pairs else None,
                 'minDefaultPairSum':min(pairs) if pairs else None,'maxDefaultPairSum':max(pairs) if pairs else None,
                 'top2PairLe1':sum(bool(x['pairLe1Top2']) for x in ev),'correct':bool(r['r265CorrectnessPass'])}
            summary.append(row);rows.append({'marketId':m,'winnerPostHocOnly':co[m]['winner'],**r});print(json.dumps(row,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_67_WHEEL_RISK_REPAIR_FRONTIER_SHADOW_V1','researchOnly':True,'behaviorChanged':False,
             'markets':mids,'summary':summary,'rows':rows,
             'boundary':['exact R2.65 behavior','strict-past Maker live price levels only','frontier is option evidence not realized protection','no admission change','no pairSum threshold','no Target/winner runtime input']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
