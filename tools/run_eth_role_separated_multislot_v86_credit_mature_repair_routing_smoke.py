from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v85_adaptive_repair_routing_smoke.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v85',_STAGED);v85=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v85)
else:
    import tools.run_eth_role_separated_multislot_v85_adaptive_repair_routing_smoke as v85
v82=v85.v82;v8=v85.v8;v7=v85.v7;v2=v85.v2;EPS=1e-9

class CreditMatureRepairRoutingSim(v85.AdaptiveRepairRoutingSim):
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots);self.scopeRepairCreditEarned=0.0;self.creditMaturityRouting=Counter()
    def process(self,t):
        g=int(self.scopeGeneration);before=float(self.totalRepairCreditValue)
        super().process(t)
        if int(self.scopeGeneration)!=g:self.scopeRepairCreditEarned=0.0
        else:
            d=max(0.0,float(self.totalRepairCreditValue)-before)
            if d>EPS:self.scopeRepairCreditEarned+=d
    def _min_legal_expand_risk(self):
        if self.scopeSide is None:return math.inf
        side=self.scopeSide;used=self._used_prices(side);vals=[]
        for p in self._live_price_levels(side):
            p=float(v2.kprice(p))
            if p in used:continue
            q=1.0/p;vals.append(max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q)))
        return min(vals) if vals else math.inf
    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_REPAIR':return v82.SafeParallelCapacitySim._candidate_from_levels_v8(self,side,role,require_pair)
        need=self._min_legal_expand_risk()
        if not math.isfinite(need) or self.scopeRepairCreditEarned+EPS<need:
            self.creditMaturityRouting['PRE_CREDIT_MATURITY_EXECUTION_PRIORITY']+=1
            return v82.SafeParallelCapacitySim._candidate_from_levels_v8(self,side,role,require_pair)
        self.creditMaturityRouting['POST_CREDIT_MATURITY_PAIR_AWARE']+=1
        return super()._candidate_from_levels_v8(side,role,require_pair)
    def run_v86(self,winner):
        r=super().run_v85(winner);r['scopeRepairCreditEarned']=float(self.scopeRepairCreditEarned);r['creditMaturityRouting']=dict(self.creditMaturityRouting);r['minLegalExpandRiskAtEnd']=self._min_legal_expand_risk();return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='v86_credit_mature_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=v85.AdaptiveRepairRoutingSim(tape,4)
            try:r0=ctl.run_v85(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'V85_FIRST_PROGRESS_CONTROL','winnerPostHocOnly':cr['winner'],**r0});sim=CreditMatureRepairRoutingSim(tape,4)
            try:r=sim.run_v86(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V86_CREDIT_MATURE_ROUTING','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v85Pnl':r0['pnlDiagnosticOnly'],'v86Pnl':r['pnlDiagnosticOnly'],'v85Floor':r0['floor'],'v86Floor':r['floor'],'v85Fills':r0['fillEvents'],'v86Fills':r['fillEvents'],'v86Submits':r['submits'],'routing':r['creditMaturityRouting'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V85_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V86_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V86_CREDIT_MATURE_REPAIR_ROUTING_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessSplitPass':correct,'fillRetention50pctResearchGate':livepass},'boundary':['V8.5 safety/correctness frozen','switch to pair-aware Repair only when confirmed Repair-earned credit >= current minimum legal Expand worst-case risk','birth credit excluded','pending Repair excluded','no hard safety added','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'routing':n[m]['creditMaturityRouting']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
