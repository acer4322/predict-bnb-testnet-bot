from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9

class OneShotRepairQueueOptionForkSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.qov_key=None;self.qov_bound=False;self.qov_done=False;self.qov_stats=Counter();self.qov_events=[]

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));role=self.key_role.get(key) if key is not None else None
        if (not self.qov_done and reason=='SATELLITE_FRONTIER_REANCHOR' and key and role=='SATELLITE_REPAIR'):
            if self.qov_key is None:
                self.qov_key=str(key);self.qov_bound=True;self.qov_stats['BOUND']+=1
                o=self.orders.get(key)
                self.qov_events.append({'t':int(t),'event':'QOV_ONE_SHOT_BOUND','key':str(key),'slotId':int(sid),'side':str(o.get('side')) if o else None,'price':float(o.get('price')) if o else None,'generation':int(self.key_scope_gen.get(key,-1))})
            if str(key)==str(self.qov_key) and self.qov_bound:
                o=self.orders.get(key)
                if o is not None and not o.get('cancelRequested'):
                    self.qov_stats['REANCHOR_CANCEL_SUPPRESSED']+=1
                    self.qov_events.append({'t':int(t),'event':'QOV_REANCHOR_CANCEL_SUPPRESSED','key':str(key),'reason':reason,'price':float(o.get('price') or 0.0),'ageMs':int(t)-int(o.get('placed') or t)})
                    return False
        return super()._request_cancel(t,sid,reason)

    def _reanchor_stale(self,t):
        if self.qov_bound and self.qov_key is not None:
            key=str(self.qov_key);o=self.orders.get(key)
            if o is not None and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
                side=str(o.get('side'));qv=v2.base.quotes(self.book)
                if qv and qv.get(side,{}).get('bid') is not None:
                    bid=float(qv[side]['bid']);own=float(o['price'])
                    if bid>own+EPS:
                        sid=next((s for s,k in self.slot_key.items() if str(k)==key),None)
                        if sid is not None:
                            ok=super()._request_cancel(t,int(sid),'QOV_PRIORITY_LOSS')
                            if ok:
                                self.qov_stats['PRIORITY_LOSS_CANCEL']+=1;self.qov_bound=False
                                self.qov_events.append({'t':int(t),'event':'QOV_PRIORITY_LOSS_CANCEL','key':key,'side':side,'ownPrice':own,'bestBid':bid,'ageMs':int(t)-int(o.get('placed') or t)})
        return super()._reanchor_stale(t)

    def _refresh_slots(self,t):
        start=len(self.slot_history);super()._refresh_slots(t)
        if self.qov_key is None:return
        for e in self.slot_history[start:]:
            if e.get('event')=='SLOT_FILL' and str(e.get('key'))==str(self.qov_key) and float(e.get('fillInc') or 0)>EPS:
                self.qov_stats['MANAGED_FILL_EVENT']+=1
                self.qov_events.append({'t':int(t),'event':'QOV_MANAGED_FILL','key':str(self.qov_key),'fillInc':float(e.get('fillInc') or 0.0)})
            if e.get('event')=='SLOT_RELEASE' and str(e.get('key'))==str(self.qov_key):
                self.qov_stats['MANAGED_TERMINAL']+=1;self.qov_done=True;self.qov_bound=False
                self.qov_events.append({'t':int(t),'event':'QOV_MANAGED_TERMINAL','key':str(self.qov_key),'status':str(e.get('status') or ''),'cum':float(e.get('cum') or 0.0),'cancelRequested':bool(e.get('cancelRequested'))})

    def run_qov(self,winner):
        r=super().run_r264(winner)
        r.update({'laneGQovStats':dict(self.qov_stats),'laneGQovEvents':self.qov_events[:500],'laneGQovKey':self.qov_key,'laneGQovDone':self.qov_done})
        return r


def _fill_path(r):
    out=[]
    for e in r.get('splitEvents',[]) or []:
        if e.get('event')!='ROLE_FILL_SPLIT':continue
        q=float(e.get('fillInc') or 0.0)
        if q<=EPS:continue
        out.append((int(e.get('t') or 0),str(e.get('side')),float(e.get('price') or 0.0),q))
    out.sort(key=lambda x:x[0]);return out

def _payoff_state(path,t):
    up=dn=cost=0.0
    for tt,side,p,q in path:
        if tt>int(t):break
        if side=='UP':up+=q
        else:dn+=q
        cost+=q*p
    pu=up-cost;pd=dn-cost
    return {'upPayoff':pu,'downPayoff':pd,'best':max(pu,pd),'floor':min(pu,pd),'gap':abs(pu-pd),'upQty':up,'downQty':dn,'cost':cost}

def _local_fork(br,cr):
    ev=next((e for e in (cr.get('laneGQovEvents') or []) if e.get('event')=='QOV_ONE_SHOT_BOUND'),None)
    if not ev:return None
    t=int(ev['t']);a=_fill_path(br);b=_fill_path(cr);s0=_payoff_state(a,t);s1=_payoff_state(b,t)
    out={'triggerT':t,'triggerEqual':all(abs(s0[k]-s1[k])<=1e-9 for k in s0),'horizons':{}}
    for h in (500,1000,2000,5000,10000):
        x=_payoff_state(a,t+h);y=_payoff_state(b,t+h)
        out['horizons'][str(h)]={k:y[k]-x[k] for k in x}
    return out

def compact(r):
    return {'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty') or 0.0),'correct':bool(r.get('r264CorrectnessPass')),'quotaExcess':float(r.get('repairQuotaExcessMax') or 0.0),'unauthOverflow':float(r.get('unauthorizedOverflowQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_qov_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            tape=tmp/f'{m}.json.xz';w=co[m]['winner']
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            c=OneShotRepairQueueOptionForkSim(tape,1,4)
            try:cr=c.run_qov(w)
            finally:c.close()
            bc=compact(br);cc=compact(cr)
            row={'marketId':m,'winnerPostHocOnly':w,'baseline':bc,'candidate':cc,'qovStats':cr.get('laneGQovStats',{}),'qovEvents':cr.get('laneGQovEvents',[]),'localFork':_local_fork(br,cr)};rows.append(row)
            z={'marketId':m,'bound':int(row['qovStats'].get('BOUND',0)),'suppressed':int(row['qovStats'].get('REANCHOR_CANCEL_SUPPRESSED',0)),'priorityLossCancel':int(row['qovStats'].get('PRIORITY_LOSS_CANCEL',0)),'managedFillEvents':int(row['qovStats'].get('MANAGED_FILL_EVENT',0)),'pnlDelta':cc['pnl']-bc['pnl'],'bestDelta':cc['best']-bc['best'],'floorDelta':cc['floor']-bc['floor'],'gapDelta':(cc['best']-cc['floor'])-(bc['best']-bc['floor']),'fillDelta':cc['fills']-bc['fills'],'submitDelta':cc['submits']-bc['submits'],'activeFillDelta':cc['activeFillQty']-bc['activeFillQty'],'correct':cc['correct'] and cc['quotaExcess']<=EPS and cc['unauthOverflow']<=EPS};cmp.append(z);print(json.dumps(z,ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R264_ONE_SHOT_REPAIR_QUEUE_OPTION_FORK_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(x['correct'] for x in cmp),'allMarketsExercised':all(x['bound']>0 and x['suppressed']>0 for x in cmp)},'boundary':['same frozen R2.64 control','one-shot only: first SATELLITE_REPAIR frontier-reanchor carrier per market','candidate suppresses only SATELLITE_FRONTIER_REANCHOR for managed key','other cancel reasons remain native','managed key is cancelled only on structural queue priority loss bestBid>ownPrice or other native terminal path','Passive/Active/risk/re-expand/max4/accounting unchanged','winner posthoc only','consumed structural cohort selected by marketId before outcome inspection','no fresh/no Target runtime input/no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
