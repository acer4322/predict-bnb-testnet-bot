from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
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
TARGETS={
 (1945898,'DOWN_7'):0.23570682191371795,
 (1946448,'UP_7'):0.46259757821623904,
 (1946792,'DOWN_4'):-0.6813964480239072,
 (1946792,'DOWN_5'):0.0,
 (1946792,'DOWN_20'):-0.24619893847266283,
 (1946792,'DOWN_22'):0.0,
}

class LifecycleStateAnatomySim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,market_id,max_slots=4):
        super().__init__(tape,1,max_slots);self.marketId=int(market_id);self.audit=[]
    def _scope_start_t(self):
        g=int(self.scopeGeneration);cand=[]
        for e in self.slot_history:
            if e.get('event') in {'RESPONSIBILITY_SCOPE_BIRTH','RESPONSIBILITY_SCOPE_FLIP'} and int(e.get('generation') or -1)==g:
                cand.append(int(e.get('t') or 0))
        return cand[-1] if cand else None
    def _submit_t(self,key):
        for e in reversed(self.slot_history):
            if e.get('event')=='ROLE_SLOT_SUBMIT' and e.get('key')==key:return int(e.get('t') or 0)
        return None
    def _recent(self,t,window_ms):
        z=[e for e in self.splitEvents if e.get('event')=='ROLE_FILL_SPLIT' and 0<=int(t)-int(e.get('t') or t)<=int(window_ms)]
        out={}
        for name,roles in [('EXPAND',{'SATELLITE_EXPAND'}),('REPAIR',{'ECONOMIC_CORE','SATELLITE_REPAIR'}),('ALL',None)]:
            a=[e for e in z if roles is None or e.get('role') in roles]
            out[f'{name.lower()}FillEvents{window_ms//1000}s']=len(a)
            out[f'{name.lower()}FillQty{window_ms//1000}s']=float(sum(float(e.get('fillInc') or 0.0) for e in a))
            if name=='REPAIR':out[f'repairAllocatedQty{window_ms//1000}s']=float(sum(float(e.get('repairAllocated') or 0.0) for e in a))
        return out
    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid));o=self.orders.get(key) if key else None;cap=None
        target=(self.marketId,str(key)) in TARGETS
        if target and o is not None and reason=='SATELLITE_FRONTIER_REANCHOR' and self.key_role.get(key)=='SATELLITE_EXPAND':
            side=str(o['side']);opp='DOWN' if side=='UP' else 'UP';p=float(o['price']);levels=[float(v2.kprice(x)) for x in self._live_price_levels(side)]
            nearest=min((abs(p-x) for x in levels),default=None)
            if nearest is not None and abs(float(nearest)-0.01)<=1e-9:
                qv=v2.base.quotes(self.book);up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost);gross=up+dn;debt=float(self._scope_debt_qty()) if self.scopeSide is not None else 0.0
                st=self._scope_start_t();subt=self._submit_t(key)
                live=Counter(role for _,_,_,role in self._live_role_rows())
                last=next((e for e in reversed(self.splitEvents) if e.get('event')=='ROLE_FILL_SPLIT'),None)
                cap={'t':int(t),'marketId':self.marketId,'key':str(key),'side':side,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
                     'scopeAgeMs':(int(t)-st if st is not None else None),'targetOrderAgeMs':(int(t)-subt if subt is not None else None),
                     'expandPrice':p,'frontierBest':levels[0] if levels else None,'nearestLevelDistance':nearest,
                     'upInv':up,'downInv':dn,'gross':gross,'absNet':abs(up-dn),'cost':cost,
                     'expandSidePnl':float(self.inv[side])-cost,'oppositeSidePnl':float(self.inv[opp])-cost,
                     'physicalFloor':float(self._physical_floor()),'bestPnl':float(max(self.inv.values())-cost),
                     'scopeDebtQty':debt,'debtGrossRatio':(debt/gross if gross>EPS else None),
                     'scopeRepairProgressClocks':int(self.scopeRepairProgressClocks),'totalRepairProgressClocks':int(self.totalRepairProgressClocks),
                     'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),
                     'scopeRiskCreditReserved':float(self._reserved_current_expand_risk()),'availableExpandCredit':float(self._available_expand_risk_credit()),
                     'roleSubmitsSoFar':dict(self.role_submits),'roleFillsSoFar':dict(self.role_fills),'liveRoleCounts':dict(live),
                     'lastFillRole':last.get('role') if last else None,'lastFillSide':last.get('side') if last else None,
                     'lastFillAgeMs':(int(t)-int(last.get('t') or t) if last else None),'lastFillQty':float(last.get('fillInc') or 0.0) if last else None,
                     'singleCarrierPnlDeltaEvaluationOnly':float(TARGETS[(self.marketId,str(key))])}
                for w in (5000,15000,30000):cap.update(self._recent(t,w))
                if qv:
                    cap['bookImbalance']=float(qv.get('imb') or 0.0);cap['upMid']=(float(qv['UP']['bid'])+float(qv['UP']['ask']))/2.0
                cap['effectClassEvaluationOnly']='BENEFICIAL' if cap['singleCarrierPnlDeltaEvaluationOnly']>1e-9 else 'HARMFUL' if cap['singleCarrierPnlDeltaEvaluationOnly']<-1e-9 else 'ZERO'
        ok=super()._request_cancel(t,sid,reason)
        if ok and cap is not None:self.audit.append(cap)
        return ok
    def run_anatomy(self,w):
        r=super().run_cap(w);r['r232Audit']=self.audit;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='ms4_r232_'))
    mids=sorted(set(m for m,_ in TARGETS))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];all_a=[];exact=True
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=LifecycleStateAnatomySim(tape,mid,4)
            try:c=sim.run_anatomy(cr['winner'])
            finally:sim.close()
            same=(b['submits']==c['submits'] and b['fillEvents']==c['fillEvents'] and abs(b['pnlDiagnosticOnly']-c['pnlDiagnosticOnly'])<=1e-12 and abs(b['floor']-c['floor'])<=1e-12 and abs(b['best']-c['best'])<=1e-12);exact=exact and same
            all_a.extend(c['r232Audit']);rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'exactBehaviorMatch':same,'audit':c['r232Audit']})
            print(json.dumps({'marketId':mid,'exact':same,'targetsCaptured':len(c['r232Audit'])},ensure_ascii=False),flush=True)
        expected=len(TARGETS);out={'version':'MS4_R2_32_ONE_TICK_EXPAND_LIFECYCLE_STATE_ANATOMY_V1','researchOnly':True,'runtimeAuthority':False,'allBehaviorMetricsExactMatch':exact,'expectedTargets':expected,'capturedTargets':len(all_a),'rows':rows,'audit':all_a,'boundary':['instrumentation only','CAP1 exact behavior','single-carrier PnL delta evaluation-only from preregistered R2.31','no Target/winner/future runtime input','no threshold sweep','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'exact':exact,'captured':len(all_a),'audit':all_a},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
