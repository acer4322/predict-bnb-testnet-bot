from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4_r1_frozen',_STAGED);ms4=importlib.util.module_from_spec(sp);sp.loader.exec_module(ms4)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as ms4
v82=ms4.v82;v2=ms4.v2;EPS=1e-9

class Rank1RepairTrancheSim(ms4.QueueAwareRepairRoutingSim):
    """D10: decouple Repair economic anchor from physical execution quote.

    For each responsibility scope generation, authorize at most one rank-1 venue-min
    SATELLITE_REPAIR tranche before the ordinary pair-compatible ECONOMIC_CORE path.
    The tranche must be pure Repair: no overflow is allowed. All V8.2 reservation,
    Repair-first allocation, overflow, <=180s, and MS4-R1 queue routing remain frozen.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.rank1RepairGeneration=set()
        self.rank1RepairEvents=[]
        self.rank1RepairBlocks=Counter()
        self.rank1RepairKeys=set()

    def _try_rank1_repair_tranche(self,t:int):
        if self.scopeSide is None:return False
        gen=int(self.scopeGeneration)
        if gen in self.rank1RepairGeneration:return False
        repair_side=self._repair_side()
        if repair_side not in ('UP','DOWN'):return False
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=repair_side))>=self.max_slots:
            self.rank1RepairBlocks['CAPACITY_FULL']+=1;return False
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(repair_side)]
        used=self._used_prices(repair_side)
        levels=[p for p in levels if v2.kprice(p) not in used]
        if not levels:
            self.rank1RepairBlocks['NO_LIVE_PRICE']+=1;return False
        p=float(levels[0]);q=1.0/p
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(repair_side));available=max(0.0,debt-reserved)
        if available+EPS<q:
            self.rank1RepairBlocks['DEBT_BELOW_VENUE_MIN']+=1;return False
        sp=self._repair_split(repair_side,p,q)
        if sp is None:
            self.rank1RepairBlocks['SPLIT_REJECT']+=1;return False
        if float(sp.get('overflowQty') or 0.0)>EPS:
            self.rank1RepairBlocks['OVERFLOW_NOT_ALLOWED']+=1;return False
        before_n=self.n
        if not self._submit_role_v8(t,repair_side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
            self.rank1RepairBlocks['SUBMIT_REJECT']+=1;return False
        key=f'{repair_side}_{before_n}'
        self.rank1RepairGeneration.add(gen);self.rank1RepairKeys.add(key)
        ev={'t':int(t),'event':'RANK1_REPAIR_TRANCHE_SUBMIT','generation':gen,'key':key,'side':repair_side,'price':p,'qty':q,'debt':debt,'reservedBefore':reserved}
        self.rank1RepairEvents.append(ev);self.slot_history.append(ev)
        return True

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        # The execution tranche is a role-separated child of existing Repair debt,
        # not new exposure. It gets first opportunity in a fresh scope generation.
        if self.scopeSide is not None and self._try_rank1_repair_tranche(t):return
        return super()._open_one_option(t,qv,end)

    def run_d10(self,winner):
        r=super().run_v88(winner)
        fills=0;qty=0.0
        for e in r.get('splitEvents') or []:
            if e.get('event')=='ROLE_FILL_SPLIT' and e.get('key') in self.rank1RepairKeys:
                fills+=1;qty+=float(e.get('repairAllocated') or 0.0)
        r['rank1RepairSubmits']=len(self.rank1RepairEvents)
        r['rank1RepairFillEvents']=fills
        r['rank1RepairFilledQty']=qty
        r['rank1RepairBlocks']=dict(self.rank1RepairBlocks)
        r['rank1RepairEvents']=self.rank1RepairEvents[:300]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d10_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            sim=Rank1RepairTrancheSim(tape,4)
            try:r=sim.run_d10(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            rows.append({'marketId':mid,'cell':'MS4_D10_RANK1_REPAIR_TRANCHE','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d10Sub':r['submits'],'r1Fill':r0['fillEvents'],'d10Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d10Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d10Floor':r['floor'],'rank1Sub':r['rank1RepairSubmits'],'rank1Fill':r['rank1RepairFillEvents'],'rank1Qty':r['rank1RepairFilledQty'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_D10_')}
        cmp=[]
        for m in mids:
            a0,b=c[m],n[m];cmp.append({'marketId':m,'submitRetention':b['submits']/a0['submits'] if a0['submits'] else None,'fillRetention':b['fillEvents']/a0['fillEvents'] if a0['fillEvents'] else None,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],'rank1Submits':b['rank1RepairSubmits'],'rank1Fills':b['rank1RepairFillEvents']})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        anticollapse=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_D10_RANK1_REPAIR_TRANCHE_DEV','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anticollapse},'boundary':['MS4-R1 frozen except role-separated one-rank1 Repair tranche per scope generation','rank1 tranche must be pure Repair and venue-min legal','remaining debt stays with existing economic Core / queue-aware routing','no new risk authority','no overflow in rank1 tranche','<=180s unchanged','realistic HFT risk queue','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
