from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_6_parallel_passive_coverage.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r26',_STAGED);r26=importlib.util.module_from_spec(sp);sp.loader.exec_module(r26)
else:
    import tools.run_eth_ms4_r2_6_parallel_passive_coverage as r26
r22=r26.r22;r1=r26.r1;v2=r26.v2;EPS=1e-9

class FanoutRoleCapacitySim(r26.ParallelPassiveCoverageSim):
    def __init__(self,tape,fanout_limit:int,max_slots=4):
        super().__init__(tape,max_slots);self.fanoutLimit=int(fanout_limit);self.capacityBlocks=0
    def _live_fanout_count(self):
        n=0
        for k in list(self.fanoutKeys):
            o=self.orders.get(k)
            if not o:continue
            try:s=self.snap(o);status=str(s.get('status') or '').upper()
            except Exception:status=''
            if status not in v2.TERMINAL_STATUSES:n+=1
        return n
    def _parallel_repair_fill(self,t:int,side:str):
        made=0
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        while len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
            if self._live_fanout_count()>=self.fanoutLimit:
                self.capacityBlocks+=1;break
            used=self._used_prices(side);chosen=None
            for raw in self._live_price_levels(side):
                p=float(v2.kprice(raw))
                if p in used or p<=EPS:continue
                q=1.0/p
                if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
                sp=self._repair_split(side,p,q)
                if sp is None:continue
                if float(sp.get('overflowQty') or 0.0)>EPS:self.r26['FANOUT_OVERFLOW_NOT_ALLOWED']+=1;continue
                chosen=(p,q,sp);break
            if chosen is None:self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;break
            p,q,sp=chosen;before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):self.r26['FANOUT_SUBMIT_BLOCKED']+=1;break
            key=f'{side}_{before_n}';self.fanoutKeys.add(key);made+=1;self.r26['FANOUT_SUBMIT']+=1
            ev={'t':int(t),'event':'PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key),'fanoutLimit':self.fanoutLimit}
            self.r26events.append(ev);self.slot_history.append(ev)
        return made
    def run_cap(self,w):
        r=super().run_r26(w);r['fanoutLimit']=self.fanoutLimit;r['fanoutCapacityBlocks']=int(self.capacityBlocks);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r28_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r26.ParallelPassiveCoverageSim(tape,4)
            try:r0=ctl.run_r26(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'R26_FULL_FANOUT_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            for lim in (1,2):
                sim=FanoutRoleCapacitySim(tape,lim,4)
                try:r=sim.run_cap(cr['winner'])
                finally:sim.close()
                rows.append({'marketId':mid,'cell':f'MS4_R28_FANOUT_CAP{lim}','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'marketId':mid,'control':{'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'fan':r0['parallelPassiveFanoutSubmits']},'cap1':next({'fills':x['fillEvents'],'pnl':x['pnlDiagnosticOnly'],'floor':x['floor'],'fan':x['parallelPassiveFanoutSubmits']} for x in rows if x['marketId']==mid and x['cell']=='MS4_R28_FANOUT_CAP1'),'cap2':next({'fills':x['fillEvents'],'pnl':x['pnlDiagnosticOnly'],'floor':x['floor'],'fan':x['parallelPassiveFanoutSubmits']} for x in rows if x['marketId']==mid and x['cell']=='MS4_R28_FANOUT_CAP2')},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_8_FANOUT_ROLE_CAPACITY_ABLATION_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'boundary':['max_slots remains 4 for all cells','only concurrent R2.6 parallel-fanout occupancy is ablated: max 1 vs max 2 vs existing full fanout','unused slots remain available to frozen MS4/R2.2 Core/Expand/continuation behavior','Repair accounting/Overflow/Active authority unchanged','no Target numeric runtime rule','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(rows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
