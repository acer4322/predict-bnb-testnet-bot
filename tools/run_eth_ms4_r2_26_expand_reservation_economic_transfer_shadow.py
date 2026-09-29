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

class ExpandReservationEconomicTransferShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.r226=Counter();self.shadowEvents=[];self._seen=set()

    def _shadow(self,t,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None or self._has_stale_scope_reservation():return
        side=str(self.scopeSide);repair_side='DOWN' if side=='UP' else 'UP'
        core=self._core_for_side(repair_side)
        if core is None or core[2] is None:return
        cp=float(core[2]['price'])
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(side)]
        if not levels:return
        used_other=set()
        live_expand=[]
        for sid,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND',side=side):
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumQty') or o.get('cum') or 0.0)
            except Exception:st='';cum=float(o.get('cum') or 0.0)
            if st in v2.TERMINAL_STATUSES:continue
            p=float(o.get('price') or 0.0);rem=max(0.0,float(self._remaining(key)));risk=rem*p
            live_expand.append({'slotId':int(sid),'key':key,'price':p,'qty':float(o.get('qty') or 0.0),'remaining':rem,'reservedRisk':risk,'status':st,'cum':cum,'cancelRequested':bool(o.get('cancelRequested'))})
        if not live_expand:return
        # Transfer is relevant only when ordinary free credit cannot fund another venue-min Expand.
        baseline_p=levels[0];baseline_q=1.0/baseline_p;baseline_risk=baseline_p*baseline_q
        avail=float(self._available_expand_risk_credit())
        if avail+EPS>=baseline_risk:return
        for old in live_expand:
            if old['cancelRequested'] or old['cum']>EPS:continue
            oldpair=cp+float(old['price'])
            if oldpair<=1.0+EPS:continue
            used={float(v2.kprice(o['price'])) for _,k,o,r in self._live_role_rows(side=side) if k!=old['key']}
            compatible=[p for p in levels if p not in used and cp+p<=1.0+EPS]
            if not compatible:continue
            newp=max(compatible);newq=1.0/newp;newrisk=newp*newq
            if newrisk>float(old['reservedRisk'])+EPS:continue
            sig=(int(t),int(self.scopeGeneration),old['key'],round(cp,8),round(float(old['price']),8),round(newp,8))
            if sig in self._seen:continue
            self._seen.add(sig);self.r226['ECONOMIC_TRANSFER_ELIGIBLE']+=1
            self.r226['ELIGIBLE_OLD_PAIR_DAMAGE_MICRO']+=int(round((oldpair-1.0)*1_000_000))
            self.r226['ELIGIBLE_NEW_PAIR_EDGE_MICRO']+=int(round((1.0-(cp+newp))*1_000_000))
            ev={'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':side,'coreRepairPrice':cp,
                'ordinaryAvailableCredit':avail,'oldExpand':old,'oldPairSum':oldpair,
                'replacementExpandPrice':newp,'replacementQty':newq,'replacementRisk':newrisk,
                'replacementPairSum':cp+newp,'releasedRisk':float(old['reservedRisk'])}
            self.shadowEvents.append(ev)

    def _open_one_option(self,t,qv,end):
        self._shadow(t,end)
        return super()._open_one_option(t,qv,end)

    def run_shadow(self,w):
        r=super().run_cap(w);r['r226Stats']=dict(self.r226);r['r226Events']=self.shadowEvents[:3000]
        r['economicTransferEligible']=int(self.r226.get('ECONOMIC_TRANSFER_ELIGIBLE',0));return r


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r226_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=ExpandReservationEconomicTransferShadow(tape,4)
            try:s=sim.run_shadow(cr['winner'])
            finally:sim.close()
            same=(int(b['fillEvents'])==int(s['fillEvents']) and int(b['submits'])==int(s['submits']) and abs(float(b['pnlDiagnosticOnly'])-float(s['pnlDiagnosticOnly']))<=1e-12 and abs(float(b['floor'])-float(s['floor']))<=1e-12)
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'control':{'fills':b['fillEvents'],'submits':b['submits'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                         'shadow':{'fills':s['fillEvents'],'submits':s['submits'],'pnl':s['pnlDiagnosticOnly'],'floor':s['floor'],'best':s['best'],'eligible':s['economicTransferEligible'],'stats':s['r226Stats'],'events':s['r226Events']},'behaviorMetricsExactMatch':same})
            print(json.dumps({'marketId':mid,'exact':same,'eligible':s['economicTransferEligible'],'stats':s['r226Stats']},ensure_ascii=False),flush=True)
        agg=Counter();
        for r in rows:agg.update(r['shadow']['stats'])
        out={'version':'MS4_R2_26_EXPAND_RESERVATION_ECONOMIC_TRANSFER_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,
             'aggregate':dict(agg),'allBehaviorMetricsExactMatch':all(r['behaviorMetricsExactMatch'] for r in rows),'rows':rows,
             'boundary':['shadow only; CAP1 behavior unchanged','candidate transfer releases one completely-unfilled same-scope live SATELLITE_EXPAND reservation and reuses no more monetary risk than it releases','old live Expand must be pair-damaging against current live ECONOMIC_CORE and replacement current live price must be pair-nondamaging','no cancel/replace occurs in shadow','no new risk capacity','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'allBehaviorMetricsExactMatch':out['allBehaviorMetricsExactMatch'],'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
