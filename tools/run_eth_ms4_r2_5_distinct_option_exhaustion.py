from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_2_failure_evidence_active_drain.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r22',_STAGED);r22=importlib.util.module_from_spec(sp);sp.loader.exec_module(r22)
else:
    import tools.run_eth_ms4_r2_2_failure_evidence_active_drain as r22
r1=r22.r1;EPS=1e-9

class DistinctOptionExhaustionSim(r22.FailureEvidenceActiveDrainSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots,['SATELLITE_REPAIR']);self.r25=Counter();self.r25events=[]
    def _failed_prices(self,gen,clock,side):
        out=set()
        for e in self.drainEvents:
            if e.get('event')!='PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL':continue
            if int(e.get('generation',-1))!=int(gen) or int(e.get('repairProgressClock',-1))!=int(clock) or str(e.get('side'))!=str(side):continue
            if e.get('sourcePrice') is not None:out.add(float(r1.v2.kprice(float(e['sourcePrice']))))
        return out
    def _required_distinct(self,side):
        levels={float(r1.v2.kprice(float(p))) for p in self._live_price_levels(side) if float(p)>EPS}
        return max(1,min(int(self.max_slots),len(levels)))
    def _try_active_drain(self,t:int):
        while self.pendingFailure:
            ev=self.pendingFailure[0];gen=int(ev['generation']);side=str(ev['side']);clock=int(ev['repairProgressClock']);epoch=(gen,clock)
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():self.pendingFailure.popleft();self.drainStats['STALE_EVIDENCE_DROP']+=1;continue
            if epoch in self.usedEpochs:self.pendingFailure.popleft();self.drainStats['EPOCH_ALREADY_DRAINED']+=1;continue
            failed=self._failed_prices(gen,clock,side);req=self._required_distinct(side)
            if len(failed)<req:
                self.r25['DISTINCT_PASSIVE_OPTIONS_NOT_EXHAUSTED']+=1
                self.r25events.append({'t':int(t),'event':'DISTINCT_PASSIVE_OPTIONS_NOT_EXHAUSTED','generation':gen,'repairProgressClock':clock,'side':side,'failedDistinctPrices':sorted(failed),'failedDistinctCount':len(failed),'requiredDistinctCount':req})
                return False
            self.r25['DISTINCT_PASSIVE_OPTIONS_EXHAUSTED']+=1
            return super()._try_active_drain(t)
        return False
    def run_r25(self,w):
        r=super().run_r22(w);r['r25Stats']=dict(self.r25);r['r25Events']=self.r25events[:800];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r25_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r22.FailureEvidenceActiveDrainSim(tape,4,['SATELLITE_REPAIR'])
            try:r0=ctl.run_r22(cr['winner'])
            finally:ctl.close()
            sim=DistinctOptionExhaustionSim(tape,4)
            try:r=sim.run_r25(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R22_SATELLITE_ONLY_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R25_DISTINCT_OPTION_EXHAUSTION','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'ctlF':r0['fillEvents'],'candF':r['fillEvents'],'ctlP':r0['pnlDiagnosticOnly'],'candP':r['pnlDiagnosticOnly'],'ctlFloor':r0['floor'],'candFloor':r['floor'],'activeSubs':r['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'r25':r['r25Stats'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='R22_SATELLITE_ONLY_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R25_DISTINCT_OPTION_EXHAUSTION'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeSubmits':n[m]['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'r25Stats':n[m]['r25Stats']})
        out={'version':'MS4_R2_5_DISTINCT_PASSIVE_OPTION_EXHAUSTION_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapse50pctPass':all(n[m]['fillEvents']>=.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['control is R2.2 SATELLITE-only failure-evidence Active drain','Active requires genuine terminal zero-fill evidence at distinct passive Repair prices within same generation+Repair-progress epoch','required distinct failures equals currently observable distinct passive option capacity capped by configured max_slots=4','confirmed Repair progress changes epoch automatically','no Target runtime numeric threshold','no fixed seconds/ticks/PnL threshold','no new responsibility/risk/quantity authority','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
