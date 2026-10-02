from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
v2=base.v3.v2
EPS=1e-9

class PairOnlyRepairFloorValueSim(base.MinimalPairRoleSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots,False)
        self.repairFloorValueChecks=0;self.repairFloorValueOverrides=0;self.repairFloorValueBlocks=0

    def _project_physical_floor_full_fill(self,side,p,q):
        up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)+float(p)*float(q)
        if side=='UP': up+=float(q)
        else: dn+=float(q)
        return float(min(up,dn)-cost)

    def _repair_floor_value_ok(self,side,p,q):
        self.repairFloorValueChecks+=1
        before=self._physical_floor();after=self._project_physical_floor_full_fill(side,p,q)
        return after>before+EPS,after,before

    def _candidate_role_aware(self,side,role):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=v2.kprice(p)
            if p in used: continue
            q=1.0/p
            self.minimal_pair_checks+=1
            if self._pair_ok(side,p):
                return float(p),float(q),None
            self.minimal_pair_blocks+=1
            if role in ('ECONOMIC_CORE','SATELLITE_REPAIR'):
                ok,after,before=self._repair_floor_value_ok(side,p,q)
                if ok:
                    self.repairFloorValueOverrides+=1
                    return float(p),float(q),{'floorValueOverride':True,'floorBefore':before,'floorAfterFullFill':after}
                self.repairFloorValueBlocks+=1;self.veto['REPAIR_NO_FLOOR_VALUE']+=1
            else:
                self.veto['MINIMAL_PAIR_ECONOMICS']+=1
        return None

    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self.veto['LATE_180S']+=1;return
        side,role,_,_=self._role_decision(qv)
        if len(self._live_role_rows(side=side))>=self.max_slots:
            self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_role_aware(side,role)
        if cand is None:
            self.role_budget_blocks[role]+=1;return
        p,q,proj=cand
        self._submit_role(t,side,role,p,q,proj,'PAIR_ONLY_REPAIR_FLOOR_VALUE')

    def _reanchor_stale(self,t):
        state,held,weak=self._state()
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'): continue
            role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=v2.kprice(o['price'])
            levels=[v2.kprice(x) for x in self._live_price_levels(side)]
            if role=='ECONOMIC_CORE':
                rem=self._remaining(key)
                ok_floor=False
                if role in ('ECONOMIC_CORE','SATELLITE_REPAIR') and rem>EPS:
                    ok_floor,_,_=self._repair_floor_value_ok(side,p,rem)
                if p in levels and (self._pair_ok(side,p) or ok_floor):
                    self.core_preserved_clocks+=1;continue
                self._request_cancel(t,sid,'CORE_INVALIDATED');continue
            if p not in levels:
                if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'): self.reanchors+=1

    def run_candidate(self,winner):
        r=self.run_minimal(winner)
        r.update({'repairFloorValueChecks':self.repairFloorValueChecks,'repairFloorValueOverrides':self.repairFloorValueOverrides,'repairFloorValueBlocks':self.repairFloorValueBlocks})
        return r

def slim2(r):
    s=base.slim(r);s.update({'repairFloorValueChecks':int(r.get('repairFloorValueChecks') or 0),'repairFloorValueOverrides':int(r.get('repairFloorValueOverrides') or 0),'repairFloorValueBlocks':int(r.get('repairFloorValueBlocks') or 0)})
    return s

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='repair_floor_value_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=PairOnlyRepairFloorValueSim(tape,4)
            try:rr=c.run_candidate(cr['winner'])
            finally:c.close()
            bs,cs=base.slim(br),slim2(rr);rows.append({'marketId':mid,'baseline':bs,'candidate':cs})
            print(json.dumps({'marketId':mid,'baseline':bs,'candidate':cs},ensure_ascii=False),flush=True)
        def agg(which):
            xs=[r[which] for r in rows];return {'markets':len(xs),'totalFills':sum(x['fills'] for x in xs),'avgFills':sum(x['fills'] for x in xs)/len(xs),'totalAlts':sum(x['fillSideAlternations'] for x in xs),'avgAlts':sum(x['fillSideAlternations'] for x in xs)/len(xs),'twoSided':sum(x['twoSidedMaterialized'] for x in xs),'totalPnl':sum(x['pnl'] for x in xs),'avgPnl':sum(x['pnl'] for x in xs)/len(xs),'avgFloor':sum(x['floor'] for x in xs)/len(xs)}
        out={'version':'ETH_PAIR_ONLY_REPAIR_FLOOR_VALUE_SMOKE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('baseline'),'candidate':agg('candidate')},'rows':rows,'boundary':['Pair economics remains exact for Probe/Expand','Repair/Core may exceed pair<=1 only when deterministic full-fill current physical Floor strictly improves','no new threshold, no Target runtime, no winner at decision time','no serialization/shared Floor budget/risk contraction/one-new-per-receipt veto','max4 and <=180s retained','realistic HFT; no dream fill; no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
