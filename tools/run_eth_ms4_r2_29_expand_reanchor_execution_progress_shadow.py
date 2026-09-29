from __future__ import annotations
import argparse, json, os, shutil, tempfile, zipfile, sys, importlib.util, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED); r28=importlib.util.module_from_spec(sp); sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2; EPS=1e-9

class ExpandReanchorExecutionProgressShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots)
        self.submitT={}
        self.audit=[]

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before_n=self.n
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok:
            self.submitT[f'{side}_{before_n}']=int(t)
        return ok

    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid)); o=self.orders.get(key) if key else None
        capture=None
        if o is not None and reason=='SATELLITE_FRONTIER_REANCHOR' and self.key_role.get(key)=='SATELLITE_EXPAND':
            side=str(o['side']); p=float(o['price']); levels=[float(v2.kprice(x)) for x in self._live_price_levels(side)]
            nearest=min((abs(p-x) for x in levels),default=None); best=levels[0] if levels else None
            try:snap=dict(self.snap(o))
            except Exception:snap={}
            opp='DOWN' if side=='UP' else 'UP'; core=None
            for _,ck,co,role in self._live_role_rows(role='ECONOMIC_CORE'):
                if int(self.key_scope_gen.get(ck,-1))==int(self.scopeGeneration) and str(co.get('side'))==opp:
                    core=(ck,co)
            capture={
                'marketT':int(t),'key':key,'side':side,'generation':int(self.key_scope_gen.get(key,-1)),
                'expandPrice':p,'submitT':self.submitT.get(key),'ageMs':int(t)-int(self.submitT.get(key,t)),
                'qty':float(o.get('qty') or 0.0),'cumAtCancel':float(o.get('cum') or 0.0),
                'remainingAtCancel':float(self._remaining(key)),'frontierBest':best,
                'signedBestMinusOrder':(float(best)-p) if best is not None else None,
                'nearestLevelDistance':nearest,
                'frontierLevelCount':len(levels),
                'scopeDebtQty':float(self._scope_debt_qty()) if self.scopeSide is not None else 0.0,
                'availableExpandCredit':float(self._available_expand_risk_credit()) if self.scopeSide is not None else 0.0,
                'physicalFloor':float(self._physical_floor()),
                'snapshotStatus':snap.get('status'),'snapshotLeavesQty':snap.get('leavesQty'),
                'snapshotCumExecQty':snap.get('cumExecQty'),'snapshotExchangeTs':snap.get('exchangeTs'),'snapshotLocalTs':snap.get('localTs'),
                'coreKey':core[0] if core else None,'corePrice':float(core[1]['price']) if core else None,
                'corePairSum':(p+float(core[1]['price'])) if core else None,
            }
        ok=super()._request_cancel(t,sid,reason)
        if ok and capture is not None:
            self.audit.append(capture)
        return ok

    def run_shadow(self,winner):
        r=super().run_cap(winner)
        releases={}
        for e in self.slot_history:
            if e.get('event')=='SLOT_RELEASE': releases[e.get('key')]=e
        for a in self.audit:
            o=self.orders.get(a['key'])
            try:s=dict(self.snap(o)) if o else {}
            except Exception:s={}
            final_cum=float(o.get('cum') or 0.0) if o else 0.0
            rel=releases.get(a['key'])
            a['finalStatus']=(rel or {}).get('status') or s.get('status')
            a['finalCum']=final_cum
            a['postCancelFill']=bool(final_cum>float(a['cumAtCancel'])+EPS)
            a['terminalLatencyMs']=(int(rel['t'])-int(a['marketT'])) if rel and rel.get('t') is not None else None
        r['r229Audit']=self.audit
        return r

def med(xs):
    xs=[float(x) for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def summarize(events):
    out={'events':len(events),'postCancelFill':sum(bool(x.get('postCancelFill')) for x in events),'markets':len(set(x.get('marketId') for x in events if x.get('marketId') is not None))}
    for name,flag in [('FILLED_AFTER_CANCEL',True),('TRUE_CANCEL',False)]:
        z=[x for x in events if bool(x.get('postCancelFill'))==flag]
        out[name]={
            'n':len(z),'medianAgeMs':med([x.get('ageMs') for x in z]),
            'medianNearestLevelDistance':med([x.get('nearestLevelDistance') for x in z]),
            'medianSignedBestMinusOrder':med([x.get('signedBestMinusOrder') for x in z]),
            'medianScopeDebtQty':med([x.get('scopeDebtQty') for x in z]),
            'medianAvailableExpandCredit':med([x.get('availableExpandCredit') for x in z]),
            'medianPhysicalFloor':med([x.get('physicalFloor') for x in z]),
            'medianCorePairSum':med([x.get('corePairSum') for x in z]),
            'medianTerminalLatencyMs':med([x.get('terminalLatencyMs') for x in z]),
        }
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='ms4_r229_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; rows=[]; all_audit=[]; exact=True
        for mid in mids:
            cr=co[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=ExpandReanchorExecutionProgressShadow(tape,4)
            try:c=sim.run_shadow(cr['winner'])
            finally:sim.close()
            same=(b['submits']==c['submits'] and b['fillEvents']==c['fillEvents'] and abs(b['pnlDiagnosticOnly']-c['pnlDiagnosticOnly'])<=1e-12 and abs(b['floor']-c['floor'])<=1e-12 and abs(b['best']-c['best'])<=1e-12)
            exact=exact and same
            for e in c['r229Audit']: e['marketId']=mid
            all_audit.extend(c['r229Audit'])
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'exactBehaviorMatch':same,'submits':c['submits'],'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'audit':c['r229Audit']})
            print(json.dumps({'marketId':mid,'exact':same,'events':len(c['r229Audit']),'postCancelFill':sum(x['postCancelFill'] for x in c['r229Audit'])},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_29_EXPAND_REANCHOR_EXECUTION_PROGRESS_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'allBehaviorMetricsExactMatch':exact,'rows':rows,'aggregate':summarize(all_audit),'boundary':['shadow only','CAP1 exact behavior','post-cancel fill/terminal status evaluation only','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'exact':exact,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
