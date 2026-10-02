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
v2=r28.v2;EPS=1e-9

class RecoverableSafeSurplusShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.r217=Counter();self.r217events=[];self._seen=set()
    def _expand_candidate(self,side):
        used=self._used_prices(side)
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS: continue
            q=1.0/p
            if math.isfinite(q) and q>EPS and q<=12.0+EPS:return p,q
        return None
    def _recoverability(self,side,p,q):
        before=float(self._physical_floor());up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)
        if side=='UP':up+=q
        else:dn+=q
        cost+=q*p
        after_expand=float(min(up,dn)-cost)
        repair='DOWN' if side=='UP' else 'UP'
        owned=[]
        # Credit only actual current-generation live Repair reservations at their owned prices.
        for _,key,o,role in self._live_role_rows(side=repair):
            if role not in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:continue
            if int(self.key_scope_gen.get(key,self.scopeGeneration))!=int(self.scopeGeneration):continue
            rem=float(self._remaining(key))
            if rem<=EPS:continue
            rp=float(o['price']);owned.append((key,rem,rp,role))
            if repair=='UP':up+=rem
            else:dn+=rem
            cost+=rem*rp
        after_owned=float(min(up,dn)-cost)
        if after_owned>=before-EPS:
            return {'recoverable':True,'reason':'OWNED_REPAIR_RESTORES_PREEXPAND_FLOOR','floorBefore':before,'floorAfterExpand':after_expand,'floorAfterOwned':after_owned,'ownedRepairQty':sum(x[1] for x in owned),'futureRepairPrice':None,'futureRepairQty':0.0,'forwardPairSum':None}
        strong=dn if repair=='UP' else up;weak=up if repair=='UP' else dn
        room=max(0.0,strong-weak);deficit=max(0.0,before-after_owned)
        used=self._used_prices(repair)
        rp=None
        for raw in self._live_price_levels(repair):
            px=float(v2.kprice(raw))
            if px in used or px<=EPS or px>=1.0-EPS:continue
            rp=px;break
        if rp is None:
            return {'recoverable':False,'reason':'NO_EXECUTION_PRIORITY_REPAIR_PRICE','floorBefore':before,'floorAfterExpand':after_expand,'floorAfterOwned':after_owned,'ownedRepairQty':sum(x[1] for x in owned),'futureRepairPrice':None,'futureRepairQty':None,'forwardPairSum':None,'repairRoom':room,'repairDeficit':deficit}
        qmin=1.0/rp
        need=deficit/(1.0-rp) if deficit>EPS else 0.0
        rq=max(qmin,need)
        if not math.isfinite(rq) or rq<=EPS or rq>12.0+EPS or rq>room+EPS:
            return {'recoverable':False,'reason':'EXECUTION_PRIORITY_REPAIR_CANNOT_RESTORE_PREEXPAND_FLOOR','floorBefore':before,'floorAfterExpand':after_expand,'floorAfterOwned':after_owned,'ownedRepairQty':sum(x[1] for x in owned),'futureRepairPrice':rp,'futureRepairQty':rq,'forwardPairSum':float(p+rp),'repairRoom':room,'repairDeficit':deficit}
        return {'recoverable':True,'reason':'OWNED_PLUS_ONE_PASSIVE_REPAIR_RESTORES_PREEXPAND_FLOOR','floorBefore':before,'floorAfterExpand':after_expand,'floorAfterOwned':after_owned,'ownedRepairQty':sum(x[1] for x in owned),'futureRepairPrice':rp,'futureRepairQty':rq,'forwardPairSum':float(p+rp),'repairRoom':room,'repairDeficit':deficit}
    def _shadow_check(self,t):
        if self.scopeSide is None:return
        side=str(self.scopeSide);cand=self._expand_candidate(side)
        if cand is None:return
        p,q=cand;before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after);credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return
        key=(int(t),int(self.scopeGeneration),side,round(p,8),round(q,8))
        if key in self._seen:return
        self._seen.add(key);self.r217['CREDIT_BLOCKED_EXPAND_OPPORTUNITY']+=1
        rec=self._recoverability(side,p,q)
        if rec['recoverable']:
            self.r217['RECOVERABLE_DESPITE_CREDIT_BLOCK']+=1
            if rec.get('forwardPairSum') is not None and float(rec['forwardPairSum'])<1.0-EPS:self.r217['RECOVERABLE_FAVORABLE_FORWARD_PAIR']+=1
        ev={'t':int(t),'generation':int(self.scopeGeneration),'side':side,'expandPrice':p,'expandQty':q,'riskCost':risk,'availableRealizedCredit':credit,**rec}
        self.r217events.append(ev)
    def _open_one_option(self,t,qv,end):
        self._shadow_check(t)
        return super()._open_one_option(t,qv,end)
    def run_shadow(self,w):
        r=super().run_cap(w);r['r217ShadowStats']=dict(self.r217);r['r217ShadowEvents']=self.r217events[:3000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r217_shadow_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];sim=RecoverableSafeSurplusShadow(tmp/'tapes'/f'{mid}.json.xz',4)
            try:r=sim.run_shadow(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'marketId':mid,'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'shadow':r['r217ShadowStats']},ensure_ascii=False),flush=True)
        agg=Counter()
        for r in rows:agg.update(r.get('r217ShadowStats') or {})
        events=[e for r in rows for e in (r.get('r217ShadowEvents') or [])]
        fps=[float(e['forwardPairSum']) for e in events if e.get('recoverable') and e.get('forwardPairSum') is not None]
        out={'version':'MS4_R2_17_RECOVERABLE_SAFE_SURPLUS_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'aggregate':dict(agg),'recoverableForwardPairSumMean':sum(fps)/len(fps) if fps else None,'recoverableForwardPairSumLt1Share':sum(x<1.0-EPS for x in fps)/len(fps) if fps else None,'boundary':['shadow only; CAP1 behavior unchanged','observes venue-min Expand opportunities blocked only by realized monetary-credit budget','recoverable means existing live current-generation Repair reservations plus at most one currently visible legal passive Repair tranche can restore pre-Expand physical Floor','forward pairSum is descriptive/ranking evidence only; pairSum<=1 is NOT an admission rule','no Target/winner/future runtime input','<=180s behavior unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'meanForwardPair':out['recoverableForwardPairSumMean'],'lt1Share':out['recoverableForwardPairSumLt1Share']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
