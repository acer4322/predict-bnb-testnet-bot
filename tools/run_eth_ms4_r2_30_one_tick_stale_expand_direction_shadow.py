from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2; EPS=1e-9

class OneTickDirectionShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots); self.audit=[]; self.submitT={}
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=self.n; ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok:self.submitT[f'{side}_{before}']=int(t)
        return ok
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));o=self.orders.get(key) if key else None;cap=None
        if o is not None and reason=='SATELLITE_FRONTIER_REANCHOR' and self.key_role.get(key)=='SATELLITE_EXPAND':
            side=str(o['side']);p=float(o['price']);levels=[float(v2.kprice(x)) for x in self._live_price_levels(side)]
            nearest=min((abs(p-x) for x in levels),default=None)
            if nearest is not None and abs(nearest-0.01)<=1e-9:
                qv=v2.base.quotes(self.book)
                if qv:
                    imb=float(qv.get('imb') or 0.0);depth='UP' if imb>=0 else 'DOWN'
                    mid=(float(qv['UP']['bid'])+float(qv['UP']['ask']))/2.0
                    price='UP' if mid>0.5+EPS else 'DOWN' if mid<0.5-EPS else 'NEUTRAL'
                    cap={'t':int(t),'key':key,'side':side,'expandPrice':p,'frontierBest':levels[0] if levels else None,'nearestLevelDistance':nearest,
                         'bookImbalance':imb,'depthSide':depth,'upMid':mid,'priceSide':price,
                         'consensusSide':depth if price!='NEUTRAL' and depth==price else None,
                         'depthAligned':depth==side,'priceAligned':price==side,'consensusAligned':(price!='NEUTRAL' and depth==price==side)}
        ok=super()._request_cancel(t,sid,reason)
        if ok and cap is not None:self.audit.append(cap)
        return ok
    def run_shadow(self,winner):
        r=super().run_cap(winner); win=str(winner).upper()
        for a in self.audit:a['winnerAligned']=a['side']==win
        r['r230Audit']=self.audit;return r

def agg(events):
    out={'eligibleEvents':len(events),'markets':len(set(e['marketId'] for e in events)),'winnerAligned':sum(e['winnerAligned'] for e in events)}
    out['baseWinnerAlignmentRate']=out['winnerAligned']/out['eligibleEvents'] if out['eligibleEvents'] else None
    for name in ['depthAligned','priceAligned','consensusAligned']:
        z=[e for e in events if e[name]]; out[name]={'events':len(z),'markets':len(set(e['marketId'] for e in z)),'winnerAligned':sum(e['winnerAligned'] for e in z),'precision':(sum(e['winnerAligned'] for e in z)/len(z) if z else None)}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r230_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];all_events=[];exact=True
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=OneTickDirectionShadow(tape,4)
            try:c=sim.run_shadow(cr['winner'])
            finally:sim.close()
            same=(b['submits']==c['submits'] and b['fillEvents']==c['fillEvents'] and abs(b['pnlDiagnosticOnly']-c['pnlDiagnosticOnly'])<=1e-12 and abs(b['floor']-c['floor'])<=1e-12 and abs(b['best']-c['best'])<=1e-12);exact=exact and same
            for e in c['r230Audit']:e['marketId']=mid
            all_events.extend(c['r230Audit']);rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'exactBehaviorMatch':same,'audit':c['r230Audit']})
            print(json.dumps({'marketId':mid,'exact':same,'eligible':len(c['r230Audit']),'winnerAligned':sum(e['winnerAligned'] for e in c['r230Audit'])},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_30_ONE_TICK_STALE_EXPAND_DIRECTION_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'allBehaviorMetricsExactMatch':exact,'rows':rows,'aggregate':agg(all_events),'boundary':['shadow only','CAP1 exact behavior','winner post-hoc scoring only','no threshold sweep','no Target/future runtime input','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'exact':exact,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
