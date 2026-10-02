from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_6_parallel_passive_coverage.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r26',_STAGED);r26=importlib.util.module_from_spec(sp);sp.loader.exec_module(r26)
else:
    import tools.run_eth_ms4_r2_6_parallel_passive_coverage as r26
r22=r26.r22;r1=r26.r1;v2=r26.v2;EPS=1e-9

class DistinctPriceFanoutMemorySim(r26.ParallelPassiveCoverageSim):
    """MS4-R2.7: within one (scope generation, confirmed Repair-progress epoch),
    a parallel passive fanout price may be attempted at most once. A confirmed Repair progress
    moves to a new epoch automatically and reopens the price set. Existing 4-slot/debt limits stay frozen.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.triedFanoutPrices=defaultdict(set);self.r27=Counter();self.r27events=[]
    def _epoch(self):return (int(self.scopeGeneration),int(self.scopeRepairProgressClocks))
    def _parallel_repair_fill(self,t:int,side:str):
        made=0
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        ep=self._epoch();tried=self.triedFanoutPrices[ep]
        while len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
            used=self._used_prices(side);chosen=None
            for raw in self._live_price_levels(side):
                p=float(v2.kprice(raw))
                if p in used or p in tried or p<=EPS:
                    if p in tried:self.r27['REPEAT_PRICE_SKIPPED']+=1
                    continue
                q=1.0/p
                if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
                sp=self._repair_split(side,p,q)
                if sp is None:continue
                if float(sp.get('overflowQty') or 0.0)>EPS:self.r26['FANOUT_OVERFLOW_NOT_ALLOWED']+=1;continue
                chosen=(p,q,sp);break
            if chosen is None:self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;break
            p,q,sp=chosen;before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):self.r26['FANOUT_SUBMIT_BLOCKED']+=1;break
            key=f'{side}_{before_n}';tried.add(float(v2.kprice(p)));self.fanoutKeys.add(key);made+=1;self.r26['FANOUT_SUBMIT']+=1;self.r27['UNIQUE_FANOUT_SUBMIT']+=1
            ev={'t':int(t),'event':'PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)}
            self.r26events.append(ev);self.r27events.append(ev);self.slot_history.append(ev)
        return made
    def run_r27(self,w):
        r=super().run_r26(w);r['r27Stats']=dict(self.r27);r['r27Events']=self.r27events[:1000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r27_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r26.ParallelPassiveCoverageSim(tape,4)
            try:r0=ctl.run_r26(cr['winner'])
            finally:ctl.close()
            sim=DistinctPriceFanoutMemorySim(tape,4)
            try:r=sim.run_r27(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R26_PARALLEL_PASSIVE_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R27_DISTINCT_PRICE_FANOUT_MEMORY','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'ctlSub':r0['submits'],'candSub':r['submits'],'ctlF':r0['fillEvents'],'candF':r['fillEvents'],'ctlP':r0['pnlDiagnosticOnly'],'candP':r['pnlDiagnosticOnly'],'ctlFloor':r0['floor'],'candFloor':r['floor'],'activeSubs':r['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'fanoutSub':r['parallelPassiveFanoutSubmits'],'fanoutFill':r['parallelPassiveFanoutFilledKeys'],'repeatSkip':r['r27Stats'].get('REPEAT_PRICE_SKIPPED',0),'maxDistinct':r['maxSimultaneousDistinctPrices'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='R26_PARALLEL_PASSIVE_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R27_DISTINCT_PRICE_FANOUT_MEMORY'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fanoutSubmits':n[m]['parallelPassiveFanoutSubmits'],'fanoutFilledKeys':n[m]['parallelPassiveFanoutFilledKeys'],'repeatPriceSkipped':n[m]['r27Stats'].get('REPEAT_PRICE_SKIPPED',0)})
        out={'version':'MS4_R2_7_DISTINCT_PRICE_FANOUT_MEMORY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapse50pctPass':all(n[m]['fillEvents']>=.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['control is R2.6 parallel passive coverage','same generation+confirmed Repair-progress epoch never reuses a fanout price after it has been submitted','confirmed Repair progress opens a new epoch automatically','existing max_slots=4 and authoritative Repair reservation unchanged','no Target numeric rule/no fixed time/tick/PnL threshold','Active residual actuator unchanged','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
