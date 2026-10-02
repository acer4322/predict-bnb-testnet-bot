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
v2=ms4.v2;EPS=1e-9

class PersistentRank1RepairSim(ms4.QueueAwareRepairRoutingSim):
    """D11: persistent rank-1 pure-Repair carrier until first confirmed fill in a scope.

    No new risk authority. At most one rank-1 Repair carrier is live/pending at a time.
    It may be re-opened after terminal zero-fill or stale reanchor while the same scope debt
    remains venue-min feasible. First confirmed rank-1 Repair fill retires this execution lane
    for that scope generation; remaining debt continues through frozen MS4-R1 lanes.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.rank1Keys=set();self.rank1KeyGen={};self.rank1DoneGen=set();self.rank1CountByGen=Counter()
        self.rank1Events=[];self.rank1Blocks=Counter();self.rank1FillSeen=set()

    def _current_rank1_live(self,gen:int):
        for _,key,o,role in self._live_role_rows():
            if key in self.rank1Keys and int(self.rank1KeyGen.get(key,-1))==gen:
                return True
        return False

    def _detect_rank1_fills(self,t:int):
        for key in list(self.rank1Keys):
            if key in self.rank1FillSeen:continue
            auth=float(self.keyRepairQuotaAuthorized.get(key,0.0));rem=float(self.keyRepairQuotaRemaining.get(key,auth))
            filled=max(0.0,auth-rem)
            if filled>EPS:
                self.rank1FillSeen.add(key);gen=int(self.rank1KeyGen.get(key,self.key_scope_gen.get(key,-1)))
                self.rank1DoneGen.add(gen)
                ev={'t':int(t),'event':'PERSISTENT_RANK1_FIRST_FILL','generation':gen,'key':key,'repairFilled':filled}
                self.rank1Events.append(ev);self.slot_history.append(ev)

    def process(self,t):
        super().process(t);self._detect_rank1_fills(t)

    def _try_rank1(self,t:int):
        if self.scopeSide is None:return False
        gen=int(self.scopeGeneration)
        if gen in self.rank1DoneGen:return False
        if self._current_rank1_live(gen):
            self.rank1Blocks['LANE_ALREADY_LIVE']+=1;return False
        repair_side=self._repair_side()
        if repair_side not in ('UP','DOWN'):return False
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=repair_side))>=self.max_slots:
            self.rank1Blocks['CAPACITY_FULL']+=1;return False
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(repair_side)]
        used=self._used_prices(repair_side);levels=[p for p in levels if v2.kprice(p) not in used]
        if not levels:self.rank1Blocks['NO_UNUSED_LIVE_PRICE']+=1;return False
        p=float(levels[0]);q=1.0/p
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(repair_side));available=max(0.0,debt-reserved)
        if available+EPS<q:
            self.rank1Blocks['DEBT_BELOW_VENUE_MIN']+=1;return False
        sp=self._repair_split(repair_side,p,q)
        if sp is None:
            self.rank1Blocks['SPLIT_REJECT']+=1;return False
        if float(sp.get('overflowQty') or 0.0)>EPS:
            self.rank1Blocks['OVERFLOW_NOT_ALLOWED']+=1;return False
        before_n=self.n
        if not self._submit_role_v8(t,repair_side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
            self.rank1Blocks['SUBMIT_REJECT']+=1;return False
        key=f'{repair_side}_{before_n}';self.rank1Keys.add(key);self.rank1KeyGen[key]=gen;self.rank1CountByGen[gen]+=1
        ev={'t':int(t),'event':'PERSISTENT_RANK1_REPAIR_SUBMIT','generation':gen,'attempt':int(self.rank1CountByGen[gen]),'key':key,'side':repair_side,'price':p,'qty':q,'debt':debt,'reservedBefore':reserved}
        self.rank1Events.append(ev);self.slot_history.append(ev);return True

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        if self.scopeSide is not None and self._try_rank1(t):return
        return super()._open_one_option(t,qv,end)

    def run_d11(self,winner):
        r=super().run_v88(winner);self._detect_rank1_fills(int(self.meta['lastReceivedMs']))
        filled_qty=0.0
        for key in self.rank1Keys:
            a=float(self.keyRepairQuotaAuthorized.get(key,0.0));rm=float(self.keyRepairQuotaRemaining.get(key,a));filled_qty+=max(0.0,a-rm)
        r['persistentRank1Submits']=len([e for e in self.rank1Events if e['event']=='PERSISTENT_RANK1_REPAIR_SUBMIT'])
        r['persistentRank1FilledKeys']=len(self.rank1FillSeen)
        r['persistentRank1RepairQty']=filled_qty
        r['persistentRank1DoneGenerations']=len(self.rank1DoneGen)
        r['persistentRank1Blocks']=dict(self.rank1Blocks);r['persistentRank1Events']=self.rank1Events[:500]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d11_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            sim=PersistentRank1RepairSim(tape,4)
            try:r=sim.run_d11(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_D11_PERSISTENT_RANK1_REPAIR','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d11Sub':r['submits'],'r1Fill':r0['fillEvents'],'d11Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d11Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d11Floor':r['floor'],'rank1Sub':r['persistentRank1Submits'],'rank1FilledKeys':r['persistentRank1FilledKeys'],'rank1Qty':r['persistentRank1RepairQty'],'doneGen':r['persistentRank1DoneGenerations'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_D11_')};cmp=[]
        for m in mids:
            a0,b=c[m],n[m];cmp.append({'marketId':m,'submitRetention':b['submits']/a0['submits'] if a0['submits'] else None,'fillRetention':b['fillEvents']/a0['fillEvents'] if a0['fillEvents'] else None,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],'rank1Submits':b['persistentRank1Submits'],'rank1FilledKeys':b['persistentRank1FilledKeys']})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);anti=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_D11_PERSISTENT_RANK1_REPAIR_DEV','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['MS4-R1 frozen except persistent rank1 pure-Repair execution carrier','at most one rank1 carrier live/pending per scope','terminal zero-fill/stale terminal may reopen at current rank1','first confirmed rank1 Repair fill retires execution lane for that scope','no overflow allowed in rank1 carrier','no new risk authority','<=180s unchanged','realistic HFT risk queue','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
