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
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class PartialRepairBridgeShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.r225=Counter();self.shadowEvents=[];self._seen=set()

    def _demonstrated_live_repair_carriers(self,expand_side,pE):
        out=[]
        repair_side='DOWN' if expand_side=='UP' else 'UP'
        for sid,key,o,role in self._live_role_rows(side=repair_side):
            if role not in REPAIR_ROLES:continue
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            auth=float(self.keyRepairQuotaAuthorized.get(key,0.0));rem=max(0.0,float(self.keyRepairQuotaRemaining.get(key,auth)))
            paid=max(0.0,auth-rem)
            if paid<=EPS or rem<=EPS:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st in v2.TERMINAL_STATUSES:continue
            pR=float(o.get('price') or 0.0)
            if not (EPS<pR<1.0-EPS):continue
            pair=float(pR)+float(pE);gain=rem*(1.0-pR)
            out.append({'slotId':int(sid),'key':key,'role':role,'status':st,'repairPrice':pR,'authorizedRepairQty':auth,
                        'confirmedRepairPaidQty':paid,'remainingRepairQuota':rem,'remainingFloorGainCapacity':gain,
                        'pairSumWithExpand':pair,'pairCompatible':pair<=1.0+EPS})
        return out

    def _shadow(self,t,qv,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None or self._has_stale_scope_reservation():return
        side=str(self.scopeSide)
        # Record context without mutating CAP1 role-decision counters.
        signal=str(self._direction(qv));primary=(signal==side)
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota())
        fallback=reserved>=debt-EPS
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
        credit=float(self._available_expand_risk_credit())
        deficit=max(0.0,risk-credit)
        if deficit<=EPS:return
        if not (primary or fallback):
            self.r225['CREDIT_DEFICIT_OUTSIDE_CURRENT_EXPAND_CONTEXT']+=1;return
        carriers=self._demonstrated_live_repair_carriers(side,p)
        key=(int(t),int(self.scopeGeneration),side,round(float(p),8),round(float(q),8),bool(primary),bool(fallback))
        if key in self._seen:return
        self._seen.add(key);self.r225['CREDIT_BLOCKED_EXPAND_CONTEXT']+=1
        compatible=[x for x in carriers if x['pairCompatible']]
        covering=[x for x in compatible if float(x['remainingFloorGainCapacity'])+EPS>=deficit]
        if carriers:self.r225['HAS_DEMONSTRATED_LIVE_REPAIR_CARRIER']+=1
        if compatible:self.r225['HAS_FAVORABLE_DEMONSTRATED_CARRIER']+=1
        if covering:
            self.r225['PARTIAL_REPAIR_BRIDGE_ELIGIBLE']+=1
            best=max(covering,key=lambda x:float(x['remainingFloorGainCapacity'])-deficit)
            ev={'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':side,'signalSide':signal,
                'primaryExpandContext':primary,'fallbackExpandContext':fallback,'expandPrice':float(p),'expandQty':float(q),
                'expandRisk':risk,'ordinaryAvailableCredit':credit,'borrowDeficit':deficit,'carrier':best,
                'allCompatibleCarriers':compatible[:8]}
            self.shadowEvents.append(ev)
        elif compatible:
            self.r225['FAVORABLE_PARTIAL_CARRIER_CAPACITY_INSUFFICIENT']+=1
        elif carriers:
            self.r225['DEMONSTRATED_CARRIER_PAIR_UNFAVORABLE']+=1
        else:
            self.r225['NO_DEMONSTRATED_LIVE_REPAIR_CARRIER']+=1

    def _open_one_option(self,t,qv,end):
        self._shadow(t,qv,end)
        return super()._open_one_option(t,qv,end)

    def run_shadow(self,w):
        r=super().run_cap(w);r['r225Stats']=dict(self.r225);r['r225Events']=self.shadowEvents[:3000]
        r['partialRepairBridgeEligible']=int(self.r225.get('PARTIAL_REPAIR_BRIDGE_ELIGIBLE',0));return r


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r225_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=PartialRepairBridgeShadow(tape,4)
            try:s=sim.run_shadow(cr['winner'])
            finally:sim.close()
            same=(int(b['fillEvents'])==int(s['fillEvents']) and int(b['submits'])==int(s['submits']) and abs(float(b['pnlDiagnosticOnly'])-float(s['pnlDiagnosticOnly']))<=1e-12 and abs(float(b['floor'])-float(s['floor']))<=1e-12)
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'control':{'fills':b['fillEvents'],'submits':b['submits'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                         'shadow':{'fills':s['fillEvents'],'submits':s['submits'],'pnl':s['pnlDiagnosticOnly'],'floor':s['floor'],'best':s['best'],'stats':s['r225Stats'],'eligible':s['partialRepairBridgeEligible'],'events':s['r225Events']},'behaviorMetricsExactMatch':same})
            print(json.dumps({'marketId':mid,'exact':same,'fills':s['fillEvents'],'pnl':s['pnlDiagnosticOnly'],'floor':s['floor'],'eligible':s['partialRepairBridgeEligible'],'stats':s['r225Stats']},ensure_ascii=False),flush=True)
        agg=Counter();
        for r in rows:agg.update(r['shadow']['stats'])
        out={'version':'MS4_R2_25_PARTIAL_REPAIR_BRIDGE_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,
             'aggregate':dict(agg),'allBehaviorMetricsExactMatch':all(r['behaviorMetricsExactMatch'] for r in rows),'rows':rows,
             'boundary':['shadow only; CAP1 behavior unchanged','bridge source is unrealized remaining Repair quota of one same-scope live carrier that already has confirmed physical Repair allocation','one carrier alone must cover exact monetary-credit deficit','repairPrice+expandPrice<=1 structural requirement','pending never-filled Repair gives zero bridge authority','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'allBehaviorMetricsExactMatch':out['allBehaviorMetricsExactMatch'],'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
