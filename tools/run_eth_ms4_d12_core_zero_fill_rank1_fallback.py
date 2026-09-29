from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter,deque
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4_r1_frozen',_STAGED);ms4=importlib.util.module_from_spec(sp);sp.loader.exec_module(ms4)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as ms4
v2=ms4.v2;EPS=1e-9;TERMINAL={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}

class CoreZeroFillRank1FallbackSim(ms4.QueueAwareRepairRoutingSim):
    """D12: economic Core first; only terminal zero-fill Core earns one rank1 passive Repair fallback.

    The fallback reuses the same existing Repair responsibility, is venue-min and pure Repair,
    and cannot carry Overflow. It does not coexist with the failed Core reservation because the
    Core must be terminal before fallback. All MS4-R1 correctness and risk rules remain frozen.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.handledCore=set();self.pendingFallback=deque();self.fallbackKeys=set();self.fallbackEvents=[];self.fallbackBlocks=Counter()

    def _refresh_slots(self,t:int):
        # Capture failed Core before parent releases its slot/reservation.
        for sid,key in list(self.slot_key.items()):
            if key in self.handledCore:continue
            if self.key_role.get(key)!='ECONOMIC_CORE':continue
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status in TERMINAL:
                self.handledCore.add(key)
                if cum<=EPS:
                    ev={'t':int(t),'event':'CORE_ZERO_FILL_FALLBACK_ARMED','sourceKey':key,'generation':int(self.key_scope_gen.get(key,-1)),'side':str(o['side']),'sourcePrice':float(o['price'])}
                    self.pendingFallback.append(ev);self.fallbackEvents.append(ev);self.slot_history.append(ev)
        super()._refresh_slots(t)

    def _try_fallback(self,t:int):
        while self.pendingFallback:
            ev=self.pendingFallback[0];gen=int(ev['generation']);side=str(ev['side'])
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
                self.pendingFallback.popleft();self.fallbackBlocks['STALE_SCOPE_DROP']+=1;continue
            if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:
                self.fallbackBlocks['CAPACITY_WAIT']+=1;return False
            levels=[float(v2.kprice(p)) for p in self._live_price_levels(side)]
            used=self._used_prices(side);levels=[p for p in levels if v2.kprice(p) not in used]
            if not levels:self.fallbackBlocks['NO_UNUSED_LIVE_PRICE']+=1;return False
            p=float(levels[0]);q=1.0/p
            debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));available=max(0.0,debt-reserved)
            if available+EPS<q:
                self.fallbackBlocks['DEBT_OR_RESERVATION_BELOW_VENUE_MIN']+=1;return False
            sp=self._repair_split(side,p,q)
            if sp is None:self.fallbackBlocks['SPLIT_WAIT']+=1;return False
            if float(sp.get('overflowQty') or 0.0)>EPS:
                self.fallbackBlocks['OVERFLOW_NOT_ALLOWED']+=1;return False
            before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
                self.fallbackBlocks['SUBMIT_WAIT']+=1;return False
            key=f'{side}_{before_n}';self.fallbackKeys.add(key);self.pendingFallback.popleft()
            out={'t':int(t),'event':'CORE_ZERO_FILL_RANK1_FALLBACK_SUBMIT','sourceKey':ev['sourceKey'],'key':key,'generation':gen,'side':side,'price':p,'qty':q,'debt':debt,'reservedBefore':reserved}
            self.fallbackEvents.append(out);self.slot_history.append(out);return True
        return False

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        if self._try_fallback(t):return
        return super()._open_one_option(t,qv,end)

    def run_d12(self,winner):
        r=super().run_v88(winner)
        fqty=0.0;ffill=0
        for key in self.fallbackKeys:
            a=float(self.keyRepairQuotaAuthorized.get(key,0.0));rm=float(self.keyRepairQuotaRemaining.get(key,a));x=max(0.0,a-rm)
            if x>EPS:ffill+=1;fqty+=x
        r['coreZeroFillFallbackArmed']=len([e for e in self.fallbackEvents if e['event']=='CORE_ZERO_FILL_FALLBACK_ARMED'])
        r['coreZeroFillFallbackSubmits']=len([e for e in self.fallbackEvents if e['event']=='CORE_ZERO_FILL_RANK1_FALLBACK_SUBMIT'])
        r['coreZeroFillFallbackFilledKeys']=ffill;r['coreZeroFillFallbackRepairQty']=fqty;r['coreZeroFillFallbackBlocks']=dict(self.fallbackBlocks);r['coreZeroFillFallbackEvents']=self.fallbackEvents[:500]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d12_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            sim=CoreZeroFillRank1FallbackSim(tape,4)
            try:r=sim.run_d12(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_D12_CORE_ZERO_FILL_RANK1_FALLBACK','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d12Sub':r['submits'],'r1Fill':r0['fillEvents'],'d12Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d12Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d12Floor':r['floor'],'armed':r['coreZeroFillFallbackArmed'],'fbSub':r['coreZeroFillFallbackSubmits'],'fbFill':r['coreZeroFillFallbackFilledKeys'],'fbQty':r['coreZeroFillFallbackRepairQty'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_D12_')};cmp=[]
        for m in mids:
            a0,b=c[m],n[m];cmp.append({'marketId':m,'submitRetention':b['submits']/a0['submits'] if a0['submits'] else None,'fillRetention':b['fillEvents']/a0['fillEvents'] if a0['fillEvents'] else None,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],'fallbackArmed':b['coreZeroFillFallbackArmed'],'fallbackSubmits':b['coreZeroFillFallbackSubmits'],'fallbackFilledKeys':b['coreZeroFillFallbackFilledKeys']})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);anti=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_D12_CORE_ZERO_FILL_RANK1_FALLBACK_DEV','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['MS4-R1 economic Core always gets first execution attempt','only terminal zero-fill ECONOMIC_CORE arms one rank1 passive fallback','fallback is same Repair responsibility and pure Repair only','failed Core reservation is terminal before fallback reservation','no overflow in fallback','no new risk authority','<=180s unchanged','realistic HFT risk queue','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
