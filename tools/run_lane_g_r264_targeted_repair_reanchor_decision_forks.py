from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9

class TargetedRepairReanchorFork(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,target_key,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.target_key=str(target_key);self.bound=False;self.done=False;self.events=[];self.suppressed=0

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));role=self.key_role.get(key) if key is not None else None
        if (not self.done and str(key)==self.target_key and role=='SATELLITE_REPAIR' and reason=='SATELLITE_FRONTIER_REANCHOR'):
            o=self.orders.get(key)
            if o is not None and not o.get('cancelRequested'):
                if not self.bound:
                    self.bound=True
                    self.events.append({'t':int(t),'event':'TARGET_FORK_BOUND','key':self.target_key,'slotId':int(sid),'side':str(o.get('side')),'price':float(o.get('price') or 0.0),'generation':int(self.key_scope_gen.get(key,-1))})
                self.suppressed+=1
                self.events.append({'t':int(t),'event':'TARGET_REANCHOR_SUPPRESSED','key':self.target_key,'ageMs':int(t)-int(o.get('placed') or t),'price':float(o.get('price') or 0.0)})
                return False
        return super()._request_cancel(t,sid,reason)

    def _reanchor_stale(self,t):
        if self.bound and not self.done:
            key=self.target_key;o=self.orders.get(key)
            if o is not None and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
                side=str(o.get('side'));qv=v2.base.quotes(self.book)
                if qv and qv.get(side,{}).get('bid') is not None:
                    bid=float(qv[side]['bid']);own=float(o['price'])
                    if bid>own+EPS:
                        sid=next((s for s,k in self.slot_key.items() if str(k)==key),None)
                        if sid is not None and super()._request_cancel(t,int(sid),'QOV_PRIORITY_LOSS'):
                            self.bound=False
                            self.events.append({'t':int(t),'event':'TARGET_PRIORITY_LOSS_CANCEL','key':key,'ownPrice':own,'bestBid':bid,'ageMs':int(t)-int(o.get('placed') or t)})
        return super()._reanchor_stale(t)

    def _refresh_slots(self,t):
        start=len(self.slot_history);super()._refresh_slots(t)
        for e in self.slot_history[start:]:
            if str(e.get('key'))!=self.target_key:continue
            if e.get('event')=='SLOT_FILL' and float(e.get('fillInc') or 0)>EPS:
                self.events.append({'t':int(t),'event':'TARGET_MANAGED_FILL','key':self.target_key,'fillInc':float(e.get('fillInc') or 0.0)})
            if e.get('event')=='SLOT_RELEASE':
                self.done=True;self.bound=False
                self.events.append({'t':int(t),'event':'TARGET_MANAGED_TERMINAL','key':self.target_key,'status':str(e.get('status') or ''),'cum':float(e.get('cum') or 0.0),'cancelRequested':bool(e.get('cancelRequested'))})

    def run_target(self,winner):
        r=super().run_r264(winner)
        r['targetForkEvents']=self.events[:200]
        r['targetForkSuppressed']=int(self.suppressed)
        r['targetForkExercised']=any(e.get('event')=='TARGET_FORK_BOUND' for e in self.events)
        return r

def fill_path(r):
    out=[]
    for e in r.get('splitEvents',[]) or []:
        if e.get('event')!='ROLE_FILL_SPLIT':continue
        q=float(e.get('fillInc') or 0.0)
        if q<=EPS:continue
        out.append((int(e.get('t') or 0),str(e.get('side')),float(e.get('price') or 0.0),q,str(e.get('role')),str(e.get('key'))))
    out.sort(key=lambda x:x[0]);return out

def payoff_state(path,t):
    up=dn=cost=0.0
    for tt,side,p,q,role,key in path:
        if tt>int(t):break
        if side=='UP':up+=q
        else:dn+=q
        cost+=q*p
    pu=up-cost;pd=dn-cost
    return {'upPayoff':pu,'downPayoff':pd,'best':max(pu,pd),'floor':min(pu,pd),'gap':abs(pu-pd),'upQty':up,'downQty':dn,'cost':cost}

def delta_state(a,b):return {k:b[k]-a[k] for k in a}

def fork_value(br,cr,trigger_t):
    a=fill_path(br);b=fill_path(cr);s0=payoff_state(a,trigger_t);s1=payoff_state(b,trigger_t)
    out={'triggerT':int(trigger_t),'triggerEqual':all(abs(s0[k]-s1[k])<=1e-9 for k in s0),'fixed':{},'structural':{}}
    for h in (500,1000,2000,5000,10000):
        out['fixed'][str(h)]=delta_state(payoff_state(a,trigger_t+h),payoff_state(b,trigger_t+h))
    # First physical state divergence across the union of subsequent fill clocks.
    clocks=sorted({x[0] for x in a+b if x[0]>trigger_t})
    for tt in clocks:
        x=payoff_state(a,tt);y=payoff_state(b,tt)
        if any(abs(x[k]-y[k])>1e-9 for k in x):
            out['structural']['firstPhysicalDivergenceT']=tt
            out['structural']['firstPhysicalDivergenceDelayMs']=tt-trigger_t
            out['structural']['firstPhysicalDivergenceDelta']=delta_state(x,y)
            break
    # Next fill clock in either branch, even when it does not yet create divergence.
    if clocks:
        tt=clocks[0];out['structural']['nextFillT']=tt;out['structural']['nextFillDelayMs']=tt-trigger_t
        out['structural']['nextFillDelta']=delta_state(payoff_state(a,tt),payoff_state(b,tt))
    return out

def compact(r):
    return {'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),
            'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty') or 0.0),'correct':bool(r.get('r264CorrectnessPass')),
            'quotaExcess':float(r.get('repairQuotaExcessMax') or 0.0),'unauthOverflow':float(r.get('unauthorizedOverflowQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--targets',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    targets=json.loads(Path(a.targets).read_text(encoding='utf-8'))['targets']
    by={}
    for x in targets:by.setdefault(int(x['marketId']),[]).append(x)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_target_forks_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];baseline_cache={}
        for m in sorted(by):
            tape=tmp/f'{m}.json.xz';w=co[m]['winner']
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            baseline_cache[m]=br;bc=compact(br)
            for target in by[m]:
                c=TargetedRepairReanchorFork(tape,target['key'],1,4)
                try:cr=c.run_target(w)
                finally:c.close()
                cc=compact(cr);bound=next((e for e in cr.get('targetForkEvents',[]) if e.get('event')=='TARGET_FORK_BOUND'),None)
                fv=fork_value(br,cr,int(bound['t'])) if bound else None
                term=next((e for e in cr.get('targetForkEvents',[]) if e.get('event')=='TARGET_MANAGED_TERMINAL'),None)
                pl=next((e for e in cr.get('targetForkEvents',[]) if e.get('event')=='TARGET_PRIORITY_LOSS_CANCEL'),None)
                row={'marketId':m,'targetKey':target['key'],'baselineCancelT':int(target['t']),'baselinePrice':float(target['price']),'winnerPostHocOnly':w,
                     'exercised':bool(cr.get('targetForkExercised')),'suppressed':int(cr.get('targetForkSuppressed') or 0),
                     'priorityLossDelayMs':(int(pl['t'])-int(bound['t'])) if pl and bound else None,
                     'managedTerminalDelayMs':(int(term['t'])-int(bound['t'])) if term and bound else None,'managedTerminalStatus':term.get('status') if term else None,
                     'terminalDelta':{'pnl':cc['pnl']-bc['pnl'],'best':cc['best']-bc['best'],'floor':cc['floor']-bc['floor'],'gap':(cc['best']-cc['floor'])-(bc['best']-bc['floor']),
                                      'fills':cc['fills']-bc['fills'],'submits':cc['submits']-bc['submits'],'activeFillQty':cc['activeFillQty']-bc['activeFillQty']},
                     'forkValue':fv,'correct':cc['correct'] and cc['quotaExcess']<=EPS and cc['unauthOverflow']<=EPS}
                rows.append(row)
                print(json.dumps({'marketId':m,'targetKey':target['key'],'exercised':row['exercised'],'suppressed':row['suppressed'],'correct':row['correct'],
                                  'pnlDelta':row['terminalDelta']['pnl'],'firstEffectMs':(fv or {}).get('structural',{}).get('firstPhysicalDivergenceDelayMs')},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R264_TARGETED_REPAIR_REANCHOR_DECISION_FORKS_V1','researchOnly':True,'runtimeAuthority':False,
             'targetCount':len(targets),'marketCount':len(by),'rows':rows,
             'gates':{'correctnessPass':all(x['correct'] for x in rows),'allTargetsExercised':all(x['exercised'] and x['suppressed']>0 for x in rows),
                      'allTriggerStatesEqual':all((x.get('forkValue') or {}).get('triggerEqual',False) for x in rows)},
             'boundary':['each candidate changes exactly one baseline SATELLITE_REPAIR SATELLITE_FRONTIER_REANCHOR decision','target list frozen from baseline before outcomes','all earlier decisions remain baseline-identical','candidate keeps target queue only until structural bestBid>ownPrice priority loss or native terminal','fixed local horizons 0.5/1/2/5/10s plus first physical divergence','winner posthoc only and absent from decision features','consumed full24 only/no fresh/no Target runtime input/no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'rows':len(rows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
