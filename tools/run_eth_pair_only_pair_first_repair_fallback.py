from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_pair_only_repair_floor_value_smoke as v1
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
v2=base.v3.v2
EPS=1e-9

class PairFirstRepairFallbackSim(v1.PairOnlyRepairFloorValueSim):
    def _candidate_role_aware(self,side,role):
        used=self._used_prices(side)
        levels=[v2.kprice(p) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        fallback=None
        # PASS 1: preserve exact Pair economics as primary repair route.
        for p in levels:
            q=1.0/p; self.minimal_pair_checks+=1
            if self._pair_ok(side,p):
                return float(p),float(q),None
            self.minimal_pair_blocks+=1
            if role in ('ECONOMIC_CORE','SATELLITE_REPAIR') and fallback is None:
                ok,after,before=self._repair_floor_value_ok(side,p,q)
                if ok:
                    fallback=(float(p),float(q),{'floorValueFallback':True,'floorBefore':before,'floorAfterFullFill':after})
        # PASS 2: only if no pair-compatible live level exists.
        if fallback is not None:
            self.repairFloorValueOverrides+=1
            return fallback
        if role in ('ECONOMIC_CORE','SATELLITE_REPAIR'):
            self.repairFloorValueBlocks+=1; self.veto['REPAIR_NO_FLOOR_VALUE']+=1
        else:
            self.veto['MINIMAL_PAIR_ECONOMICS']+=1
        return None

    def _reanchor_stale(self,t):
        state,held,weak=self._state()
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'): continue
            role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=v2.kprice(o['price'])
            levels=[v2.kprice(x) for x in self._live_price_levels(side)]
            if role=='ECONOMIC_CORE':
                if p not in levels:
                    self._request_cancel(t,sid,'CORE_INVALIDATED');continue
                if self._pair_ok(side,p):
                    self.core_preserved_clocks+=1;continue
                # If a distinct pair-compatible live price exists, do not preserve an expensive fallback core.
                used_other={v2.kprice(z[2]['price']) for z in self._live_role_rows(side=side) if z[1]!=key and z[2]}
                better_pair_exists=False
                for p2 in levels:
                    if p2==p or p2 in used_other: continue
                    if self._pair_ok(side,p2): better_pair_exists=True; break
                if better_pair_exists:
                    self._request_cancel(t,sid,'CORE_REANCHOR_TO_PAIR');continue
                rem=self._remaining(key)
                ok_floor=False
                if rem>EPS: ok_floor,_,_=self._repair_floor_value_ok(side,p,rem)
                if ok_floor:
                    self.core_preserved_clocks+=1;continue
                self._request_cancel(t,sid,'CORE_INVALIDATED');continue
            if p not in levels:
                if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'): self.reanchors+=1


def slim2(r):
    s=base.slim(r);s.update({'repairFloorValueChecks':int(r.get('repairFloorValueChecks') or 0),'repairFloorValueOverrides':int(r.get('repairFloorValueOverrides') or 0),'repairFloorValueBlocks':int(r.get('repairFloorValueBlocks') or 0)})
    return s


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='pair_first_repair_fallback_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=PairFirstRepairFallbackSim(tape,4)
            try:rr=c.run_candidate(cr['winner'])
            finally:c.close()
            bs,cs=base.slim(br),slim2(rr);rows.append({'marketId':mid,'baseline':bs,'candidate':cs})
            print(json.dumps({'marketId':mid,'baseline':bs,'candidate':cs},ensure_ascii=False),flush=True)
        def agg(which):
            xs=[r[which] for r in rows];return {'markets':len(xs),'totalFills':sum(x['fills'] for x in xs),'avgFills':sum(x['fills'] for x in xs)/len(xs),'totalAlts':sum(x['fillSideAlternations'] for x in xs),'avgAlts':sum(x['fillSideAlternations'] for x in xs)/len(xs),'twoSided':sum(x['twoSidedMaterialized'] for x in xs),'totalPnl':sum(x['pnl'] for x in xs),'avgPnl':sum(x['pnl'] for x in xs)/len(xs),'avgFloor':sum(x['floor'] for x in xs)/len(xs)}
        out={'version':'ETH_PAIR_ONLY_PAIR_FIRST_REPAIR_FALLBACK_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('baseline'),'candidate':agg('candidate')},'rows':rows,'boundary':['Pair-compatible live Repair search is exhaustive and primary','Floor-improving pair>1 Repair/Core used only if no pair-compatible distinct live price is available','Expand remains exact Pair economics only','no serialization/shared Floor budget/risk contraction/one-new-per-receipt veto','no new numeric threshold; max4 and <=180s retained','realistic HFT; no Target runtime; no dream fill; no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
