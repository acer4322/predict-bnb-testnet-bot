from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
v2=r257.v2; EPS=1e-9

class PreRepairReexpandReachabilityShadow(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r262=Counter(); self.r262Events=[]; self._seen=set()

    def _live_dedicated_repair_rows(self,gen):
        out=[]
        for key in list(self.riskRepairCarrierKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(gen): continue
            o=self.orders.get(key)
            if not o: continue
            try: st=str(self.snap(o).get('status') or '').upper()
            except Exception: st=''
            if st in v2.TERMINAL_STATUSES: continue
            out.append({'key':key,'side':str(o['side']),'price':float(o['price']),'qty':float(o.get('qty') or 0.0),'role':str(self.key_role.get(key,''))})
        return out

    def _live_samegen_expand_rows(self,gen,side):
        out=[]
        for _,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND'):
            if int(self.key_scope_gen.get(key,-1))!=int(gen) or str(o['side'])!=str(side): continue
            out.append({'key':key,'price':float(o['price']),'qty':float(o.get('qty') or 0.0),'role':role})
        return out

    def _audit(self,t,end):
        ob=self._obligation_current()
        if not ob: return
        gen=int(ob['generation'])
        if float(ob.get('repaidQty') or 0.0)>EPS: return
        repair_rows=self._live_dedicated_repair_rows(gen)
        if not repair_rows: return
        side=str(ob.get('scopeSideAtBirth') or self.scopeSide or '')
        if side not in {'UP','DOWN'}: return
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None: return
        p,q,proj,split=cand
        same_exp=self._live_samegen_expand_rows(gen,side)
        physical=len(self.slot_key)+len(self.activeKeys)
        key=(gen,round(float(ob.get('outstanding') or 0.0),9),tuple(sorted(x['key'] for x in repair_rows)),tuple(sorted(x['key'] for x in same_exp)),round(float(p),8))
        if key in self._seen:return
        self._seen.add(key)
        repair_best=min((float(x['price']) for x in repair_rows),default=None)
        ev={
            't':int(t),'generation':gen,'scopeSide':side,
            'obligationBornQty':float(ob.get('bornQty') or 0.0),'obligationOutstanding':float(ob.get('outstanding') or 0.0),
            'repaidQty':float(ob.get('repaidQty') or 0.0),'riskKey':ob.get('bornFromRiskKey'),
            'repairCarriers':repair_rows,'liveDedicatedRepairCount':len(repair_rows),
            'sameGenerationExpandAlreadyLive':same_exp,'sameGenerationExpandLiveCount':len(same_exp),
            'physicalOccupancy':physical,'maxSlots':int(self.max_slots),'sparePhysicalCapacity':max(0,int(self.max_slots)-physical),
            'expandPrice':float(p),'expandQty':float(q),'candidateFloor':float(self._candidate_alone_floor(side,p,q)),
            'floorBefore':float(self._physical_floor()),'bestBefore':float(max(self.inv.values())-self.cost),
            'gapBefore':float(max(self.inv.values())-self.cost)-float(self._physical_floor()),
            'riskSpendIfFilled':max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q))),
            'bestLiveRepairPrice':repair_best,
            'diagnosticPairSumVsLiveRepair':(float(p)+repair_best if repair_best is not None else None),
            'ordinaryContinuationCredit':float(self._available_expand_risk_credit()),
            'secondsLeft':max(0.0,(int(end)-int(t))/1000.0),
            'reachableNoDuplicate':bool(physical<int(self.max_slots) and not same_exp)
        }
        self.r262['PRE_REPAIR_LIVE_REPAIR_STATE']+=1
        if ev['sparePhysicalCapacity']>0:self.r262['SPARE_CAPACITY_STATE']+=1
        if same_exp:self.r262['ORDINARY_EXPAND_ALREADY_LIVE']+=1
        if ev['reachableNoDuplicate']:self.r262['CAUSAL_REEXPAND_REACHABLE']+=1
        if ev['diagnosticPairSumVsLiveRepair'] is not None and ev['diagnosticPairSumVsLiveRepair']<=1.0+EPS:self.r262['PAIR_LE1_DIAGNOSTIC']+=1
        self.r262Events.append(ev)

    def _open_one_option(self,t,qv,end):
        # Frozen R2.57 behavior first; audit the actual post-routing state without changing it.
        super()._open_one_option(t,qv,end)
        self._audit(t,end)

    def run_r262(self,winner):
        r=super().run_r257(winner)
        r.update({'r262Version':'MS4_R2_62_PRE_REPAIR_REEXPAND_REACHABILITY_SHADOW_V1',
                  'r262Stats':dict(self.r262),'r262Events':self.r262Events[:4000]})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r262_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];agg=Counter();markets_reach=[]
        for mid in mids:
            sim=PreRepairReexpandReachabilityShadow(tmp/f'{mid}.json.xz',1,4)
            try:r=sim.run_r262(co[mid]['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':co[mid]['winner'],**r});agg.update(r.get('r262Stats') or {})
            if int((r.get('r262Stats') or {}).get('CAUSAL_REEXPAND_REACHABLE',0))>0:markets_reach.append(mid)
            print(json.dumps({'marketId':mid,'riskFills':r.get('riskTrancheFillEvents',0),'obligations':r.get('riskRepairObligationsBorn',0),
                'states':r.get('r262Stats',{}),'firstReachable':next((x for x in r.get('r262Events',[]) if x.get('reachableNoDuplicate')),None)},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_62_PRE_REPAIR_REEXPAND_REACHABILITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,
             'markets':mids,'aggregate':dict(agg),'marketsReachable':markets_reach,'rows':rows,
             'gates':{'behaviorInvariantByConstruction':True,'reachableInAtLeastTwoMarkets':len(set(markets_reach))>=2,
                      'modernPreRepairStateExercised':agg.get('PRE_REPAIR_LIVE_REPAIR_STATE',0)>0},
             'boundary':['R2.57 behavior frozen','live Repair is execution-capacity evidence only, never credit/protection','no new orders/reservations/credit mutation','pairSum diagnostic only','consumed realistic HFT','no Target/winner/future runtime input','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':dict(agg),'marketsReachable':markets_reach,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
