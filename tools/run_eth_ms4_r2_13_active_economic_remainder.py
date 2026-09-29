from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1;v2=r1.v2;EPS=1e-9

class ActiveEconomicRemainderSim(r28.FanoutRoleCapacitySim):
    """R2.13: never suppress Active. After a successful Active Repair submit, use remaining
    authoritative unreserved Repair debt for at most one extra pair-compatible passive economic
    remainder carrier if a physical slot and distinct live price are available.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.econRemainderKeys=set();self.r213=Counter();self.r213events=[]
    def _live_econ_remainder_count(self):
        n=0
        for k in list(self.econRemainderKeys):
            o=self.orders.get(k)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:n+=1
        return n
    def _try_economic_remainder(self,t,side):
        if self.scopeSide is None or side!=self._repair_side():return False
        if self._live_econ_remainder_count()>=1:self.r213['ECON_REMAINDER_ALREADY_LIVE']+=1;return False
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:self.r213['NO_PASSIVE_SLOT']+=1;return False
        used=self._used_prices(side);chosen=None
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            if not self._pair_ok(side,p):continue
            sp=self._repair_split(side,p,q)
            if sp is None:continue
            if float(sp.get('overflowQty') or 0.0)>EPS:continue
            chosen=(p,q,sp);break
        if chosen is None:self.r213['NO_PAIR_COMPATIBLE_REMAINDER']+=1;return False
        p,q,sp=chosen;before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):self.r213['ECON_REMAINDER_SUBMIT_BLOCKED']+=1;return False
        key=f'{side}_{before_n}';self.econRemainderKeys.add(key);self.r213['ECON_REMAINDER_SUBMIT']+=1
        e={'t':int(t),'event':'ACTIVE_ECONOMIC_PASSIVE_REMAINDER_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)};self.r213events.append(e);self.slot_history.append(e);return True
    def _try_active_drain(self,t:int):
        before_active=set(self.activeKeys);ok=super()._try_active_drain(t)
        if ok:
            new=[k for k in self.activeKeys if k not in before_active]
            if new:
                side=str(self.orders[new[0]]['side']);self._try_economic_remainder(t,side)
        return ok
    def run_r213(self,w):
        r=super().run_cap(w);filled=0;qty=0.0
        for k in self.econRemainderKeys:
            o=self.orders.get(k)
            if o and float(o.get('cum') or 0)>EPS:filled+=1;qty+=float(o.get('cum') or 0)
        r['r213Stats']=dict(self.r213);r['economicRemainderSubmits']=int(self.r213.get('ECON_REMAINDER_SUBMIT',0));r['economicRemainderFilledKeys']=int(filled);r['economicRemainderFillQty']=float(qty);r['r213Events']=self.r213events[:1200];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r213_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:r0=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=ActiveEconomicRemainderSim(tape,4)
            try:r=sim.run_r213(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R213_ACTIVE_ECONOMIC_REMAINDER','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'cap1':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'active':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'r213':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'econSub':r['economicRemainderSubmits'],'econFill':r['economicRemainderFilledKeys'],'econQty':r['economicRemainderFillQty'],'stats':r['r213Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R213_ACTIVE_ECONOMIC_REMAINDER'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'activeDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'econRemainderFills':c['economicRemainderFilledKeys']})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids);anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_13_ACTIVE_ECONOMIC_REMAINDER_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['R2.8 CAP1 behavior and Active authority are never suppressed','after successful Active submit, at most one additional pair-compatible pure-Repair passive remainder may be submitted from truly unreserved debt','max_slots remains 4','economic remainder uses existing pair-compatible Core price semantics only as optional additive opportunity, never as universal gate','Repair/Overflow reservations remain authoritative','no new exposure/risk credit','no Target runtime inputs','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
