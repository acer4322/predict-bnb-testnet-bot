from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v82',_STAGED);v82=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v82)
else:
    import tools.run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke as v82
v8=v82.v8;v7=v82.v7;v2=v82.v2;EPS=1e-9

class PairAwareExpandRoutingSim(v82.SafeParallelCapacitySim):
    """V8.3: no safety change; only rank legal Expand prices using live Repair-core pair economics."""
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.expandRouting=Counter();self.expandPairSums=[]

    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_EXPAND':return super()._candidate_from_levels_v8(side,role,require_pair)
        used=self._used_prices(side)
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        if not levels:return None
        repair_side='DOWN' if side=='UP' else 'UP'
        core=self._core_for_side(repair_side)
        cp=float(core[2]['price']) if core is not None and core[2] is not None else None
        if cp is not None:
            compatible=[p for p in levels if p+cp<=1.0+EPS]
            if compatible:
                p=max(compatible);self.expandRouting['PAIR_COMPATIBLE_MOST_FILLABLE']+=1
            else:
                p=min(levels);self.expandRouting['NO_PAIR_COMPATIBLE_MIN_DAMAGE']+=1
            self.expandPairSums.append(float(p+cp))
        else:
            p=levels[0];self.expandRouting['NO_LIVE_CORE_FALLBACK_FRONTIER']+=1
        q=1.0/float(p)
        return float(p),float(q),None,None

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='SATELLITE_EXPAND':
            repair_side='DOWN' if side=='UP' else 'UP';core=self._core_for_side(repair_side)
            cp=float(core[2]['price']) if core is not None and core[2] is not None else None
            self.slot_history.append({'t':int(t),'event':'PAIR_AWARE_EXPAND_SUBMIT','side':side,'price':float(p),'repairCorePrice':cp,'pairSum':(float(p)+cp if cp is not None else None)})
        return ok

    def run_v83(self,winner):
        r=super().run_v82(winner);r['expandRouting']=dict(self.expandRouting);r['expandPairSums']=self.expandPairSums[:500]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='v83_pair_route_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=v82.SafeParallelCapacitySim(tape,4)
            try:r0=ctl.run_v82(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'V82_SAFE_PARALLEL_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            sim=PairAwareExpandRoutingSim(tape,4)
            try:r=sim.run_v83(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V83_PAIR_AWARE_EXPAND_ROUTING','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v82Pnl':r0['pnlDiagnosticOnly'],'v83Pnl':r['pnlDiagnosticOnly'],'v82Floor':r0['floor'],'v83Floor':r['floor'],'v82Fills':r0['fillEvents'],'v83Fills':r['fillEvents'],'v83Submits':r['submits'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'routing':r['expandRouting'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V82_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V83_')};cmp=[]
        for m in mids:
            cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V83_PAIR_AWARE_EXPAND_ROUTING_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessSplitPass':correct,'allMarketFillRetention50pctResearchGate':livepass,'note50pct':'anti-collapse research gate only'},'boundary':['V82 safety/correctness frozen','only legal Expand price ordering changed','prefer most-fillable live price compatible with live ECONOMIC_CORE pair sum <=1','if none, choose live price with minimum pair damage','no fixed tick offset','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'routing':n[m]['expandRouting']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
