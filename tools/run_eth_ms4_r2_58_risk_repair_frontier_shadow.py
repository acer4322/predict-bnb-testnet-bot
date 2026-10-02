from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
v2=r247.v2; EPS=1e-9

class RiskRepairFrontierShadow(r247.BoundedCoreServiceFavorableRecycleSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.frontier=[];self.seen=set()
    def _audit_frontier(self,t):
        if self.scopeSide is None or int(self.scopeRepairProgressClocks)!=0:return
        gen=int(self.scopeGeneration)
        if gen in self.seen:return
        self.seen.add(gen);rs=str(self.scopeSide);ps=self._repair_side()
        risk=[];repair=[]
        for rank,p in enumerate(self._live_price_levels(rs),1):
            p=float(v2.kprice(p));q=1.0/p if p>EPS else math.inf
            if math.isfinite(q) and 0<q<=12+EPS:risk.append({'rank':rank,'price':p,'qty':q})
        for rank,p in enumerate(self._live_price_levels(ps),1):
            p=float(v2.kprice(p));q=1.0/p if p>EPS else math.inf
            if math.isfinite(q) and 0<q<=12+EPS:repair.append({'rank':rank,'price':p,'qty':q})
        if not risk or not repair:return
        combos=[]
        for a in risk:
            for b in repair:
                combos.append({'riskRank':a['rank'],'riskPrice':a['price'],'repairRank':b['rank'],'repairPrice':b['price'],
                    'pairSum':a['price']+b['price']})
        default=next(x for x in combos if x['riskRank']==1 and x['repairRank']==1)
        le1=[x for x in combos if x['pairSum']<=1+EPS]
        near=[x for x in le1 if x['riskRank']<=2 and x['repairRank']<=2]
        risk_rank1=[x for x in le1 if x['repairRank']==1]
        repair_rank1=[x for x in le1 if x['riskRank']==1]
        best=min(combos,key=lambda x:(x['pairSum'],x['riskRank']+x['repairRank']))
        ev={'t':int(t),'generation':gen,'scopeSide':rs,'defaultRiskPrice':default['riskPrice'],
            'defaultRepairPrice':default['repairPrice'],'defaultPairSum':default['pairSum'],
            'riskLevels':risk[:12],'repairLevels':repair[:12],'pairLe1Exists':bool(le1),
            'pairLe1WithinTop2Both':bool(near),
            'minRiskRankWithRepairRank1':min((x['riskRank'] for x in risk_rank1),default=None),
            'minRepairRankWithRiskRank1':min((x['repairRank'] for x in repair_rank1),default=None),
            'bestPair':best}
        self.frontier.append(ev);self.slot_history.append({'event':'R258_RISK_REPAIR_FRONTIER_SHADOW',**ev})
    def _open_one_option(self,t,qv,end):
        self._audit_frontier(t);return super()._open_one_option(t,qv,end)
    def run_shadow(self,w):
        r=super().run_r247(w);r['r258Frontier']=self.frontier;return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r258_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[];allf=[]
  for m in mids:
   s=RiskRepairFrontierShadow(tmp/f'{m}.json.xz',1,4)
   try:r=s.run_shadow(co[m]['winner'])
   finally:s.close()
   fs=r['r258Frontier'];allf+= [{'marketId':m,**x} for x in fs];rows.append({'marketId':m,'frontier':fs})
   print(json.dumps({'marketId':m,'frontiers':len(fs),'states':[{'defaultPairSum':x['defaultPairSum'],'pairLe1':x['pairLe1Exists'],'top2':x['pairLe1WithinTop2Both'],'minRiskRankWithRepairRank1':x['minRiskRankWithRepairRank1'],'minRepairRankWithRiskRank1':x['minRepairRankWithRiskRank1']} for x in fs]},ensure_ascii=False),flush=True)
  agg={'frontiers':len(allf),'defaultPairLe1':sum(x['defaultPairSum']<=1+EPS for x in allf),'anyPairLe1':sum(x['pairLe1Exists'] for x in allf),'top2BothPairLe1':sum(x['pairLe1WithinTop2Both'] for x in allf),'medianDefaultPairSum':statistics.median([x['defaultPairSum'] for x in allf]) if allf else None}
  out={'version':'MS4_R2_58_RISK_REPAIR_FRONTIER_SHADOW_V1','researchOnly':True,'rows':rows,'aggregate':agg,'boundary':['behavior inert','pre-Repair scope frontier only','no entry wait/gate','no Target/winner/future runtime input','Maker live levels only','consumed realistic HFT','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
