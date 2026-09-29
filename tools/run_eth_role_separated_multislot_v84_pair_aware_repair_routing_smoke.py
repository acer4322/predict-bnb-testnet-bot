from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v82',_STAGED);v82=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v82)
else:
    import tools.run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke as v82
v8=v82.v8;v7=v82.v7;v2=v82.v2;EPS=1e-9

class PairAwareRepairRoutingSim(v82.SafeParallelCapacitySim):
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots);self.repairRouting=Counter();self.repairPairSums=[]
    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_REPAIR':return super()._candidate_from_levels_v8(side,role,require_pair)
        used=self._used_prices(side);levels=[float(v2.kprice(p)) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        if not levels:return None
        opp='DOWN' if side=='UP' else 'UP';avg=self.unmatched_avg(opp)
        ordered=[]
        if avg is not None:
            compatible=sorted([p for p in levels if float(avg)+p<=1.0+EPS],reverse=True)
            incompatible=sorted([p for p in levels if p not in compatible])
            ordered=compatible+incompatible
        else:ordered=levels
        for p in ordered:
            q=1.0/p
            sp=self._repair_split(side,p,q)
            if sp is None:continue
            ps=(float(avg)+p) if avg is not None else None
            if ps is not None and ps<=1.0+EPS:self.repairRouting['PAIR_COMPATIBLE_MOST_FILLABLE']+=1
            else:self.repairRouting['NO_PAIR_COMPATIBLE_MIN_DAMAGE']+=1
            if ps is not None:self.repairPairSums.append(ps)
            return float(p),float(q),sp['fullFloor'],sp
        return None
    def run_v84(self,winner):
        r=super().run_v82(winner);r['repairRouting']=dict(self.repairRouting);r['repairPairSums']=self.repairPairSums[:500];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='v84_repair_route_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=v82.SafeParallelCapacitySim(tape,4)
            try:r0=ctl.run_v82(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'V82_SAFE_PARALLEL_CONTROL','winnerPostHocOnly':cr['winner'],**r0});sim=PairAwareRepairRoutingSim(tape,4)
            try:r=sim.run_v84(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V84_PAIR_AWARE_REPAIR_ROUTING','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v82Pnl':r0['pnlDiagnosticOnly'],'v84Pnl':r['pnlDiagnosticOnly'],'v82Floor':r0['floor'],'v84Floor':r['floor'],'v82Fills':r0['fillEvents'],'v84Fills':r['fillEvents'],'v84Submits':r['submits'],'routing':r['repairRouting'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V82_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V84_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V84_PAIR_AWARE_REPAIR_ROUTING_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessSplitPass':correct,'allMarketFillRetention50pctResearchGate':livepass,'note50pct':'anti-collapse research only'},'boundary':['V82 safety/correctness frozen','only SATELLITE_REPAIR live-price ordering changed','prefer most-fillable pair-compatible live Repair price against current unmatched opposite average','if none, minimum pair damage live price','no hard veto added','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'routing':n[m]['repairRouting']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
