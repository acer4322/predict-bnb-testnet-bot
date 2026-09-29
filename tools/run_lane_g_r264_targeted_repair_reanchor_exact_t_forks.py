from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand.py').exists():
    sys.path.insert(0,str(STAGED));import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9

class ExactTRepairReanchorFork(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,target_key,target_t,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.target_key=str(target_key);self.target_t=int(target_t);self.bound=False;self.done=False;self.events=[];self.suppressed=0
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));role=self.key_role.get(key) if key is not None else None
        if (not self.done and int(t)==self.target_t and str(key)==self.target_key and role=='SATELLITE_REPAIR' and reason=='SATELLITE_FRONTIER_REANCHOR'):
            o=self.orders.get(key)
            if o is not None and not o.get('cancelRequested'):
                if not self.bound:
                    self.bound=True;self.events.append({'t':int(t),'event':'EXACT_T_FORK_BOUND','key':self.target_key,'slotId':int(sid),'side':str(o.get('side')),'price':float(o.get('price') or 0.0),'generation':int(self.key_scope_gen.get(key,-1))})
                self.suppressed+=1;self.events.append({'t':int(t),'event':'EXACT_T_REANCHOR_SUPPRESSED','key':self.target_key,'ageMs':int(t)-int(o.get('placed') or t),'price':float(o.get('price') or 0.0)});return False
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
                            self.bound=False;self.events.append({'t':int(t),'event':'EXACT_T_PRIORITY_LOSS_CANCEL','key':key,'ownPrice':own,'bestBid':bid,'ageMs':int(t)-int(o.get('placed') or t)})
        return super()._reanchor_stale(t)
    def _refresh_slots(self,t):
        start=len(self.slot_history);super()._refresh_slots(t)
        for e in self.slot_history[start:]:
            if str(e.get('key'))!=self.target_key:continue
            if e.get('event')=='SLOT_FILL' and float(e.get('fillInc') or 0)>EPS:self.events.append({'t':int(t),'event':'EXACT_T_MANAGED_FILL','key':self.target_key,'fillInc':float(e.get('fillInc') or 0.0)})
            if self.bound and e.get('event')=='SLOT_RELEASE':
                self.done=True;self.bound=False;self.events.append({'t':int(t),'event':'EXACT_T_MANAGED_TERMINAL','key':self.target_key,'status':str(e.get('status') or ''),'cum':float(e.get('cum') or 0.0),'cancelRequested':bool(e.get('cancelRequested'))})
    def run_target(self,winner):
        r=super().run_r264(winner);r['targetForkEvents']=self.events[:300];r['targetForkSuppressed']=int(self.suppressed);r['targetForkExercised']=any(e.get('event')=='EXACT_T_FORK_BOUND' for e in self.events);return r

def fill_path(r):
    out=[]
    for e in r.get('splitEvents',[]) or []:
        if e.get('event')!='ROLE_FILL_SPLIT':continue
        q=float(e.get('fillInc') or 0.0)
        if q<=EPS:continue
        out.append((int(e.get('t') or 0),str(e.get('side')),float(e.get('price') or 0.0),q))
    out.sort(key=lambda x:x[0]);return out

def payoff_state(path,t):
    up=dn=cost=0.0
    for tt,side,p,q in path:
        if tt>int(t):break
        if side=='UP':up+=q
        else:dn+=q
        cost+=q*p
    pu=up-cost;pd=dn-cost;return {'upPayoff':pu,'downPayoff':pd,'best':max(pu,pd),'floor':min(pu,pd),'gap':abs(pu-pd),'upQty':up,'downQty':dn,'cost':cost}

def delta_state(a,b):return {k:b[k]-a[k] for k in a}

def fork_value(br,cr,t):
    a=fill_path(br);b=fill_path(cr);s0=payoff_state(a,t);s1=payoff_state(b,t);out={'triggerT':int(t),'triggerEqual':all(abs(s0[k]-s1[k])<=1e-9 for k in s0),'structural':{}}
    clocks=sorted({x[0] for x in a+b if x[0]>int(t)})
    for tt in clocks:
        x=payoff_state(a,tt);y=payoff_state(b,tt)
        if any(abs(x[k]-y[k])>1e-9 for k in x):out['structural']={'firstPhysicalDivergenceT':tt,'firstPhysicalDivergenceDelayMs':tt-int(t),'firstPhysicalDivergenceDelta':delta_state(x,y)};break
    return out

def compact(r):return {'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty') or 0.0),'correct':bool(r.get('r264CorrectnessPass')),'quotaExcess':float(r.get('repairQuotaExcessMax') or 0.0),'unauthOverflow':float(r.get('unauthorizedOverflowQty') or 0.0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--targets',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();targets=json.loads(Path(a.targets).read_text(encoding='utf-8'))['targets'];by={}
    for x in targets:by.setdefault(int(x['marketId']),[]).append(x)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_exact_t_qfork_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];baseline={}
        for m in sorted(by):
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tmp/f'{m}.json.xz',1,4)
            try:br=b.run_r264(co[m]['winner'])
            finally:b.close()
            baseline[m]=(br,compact(br))
            for tg in by[m]:
                c=ExactTRepairReanchorFork(tmp/f'{m}.json.xz',tg['key'],tg['t'],1,4)
                try:cr=c.run_target(co[m]['winner'])
                finally:c.close()
                bc=baseline[m][1];cc=compact(cr);bound=next((e for e in cr.get('targetForkEvents',[]) if e.get('event')=='EXACT_T_FORK_BOUND'),None);fv=fork_value(br,cr,int(bound['t'])) if bound else None
                row={'marketId':m,'targetKey':str(tg['key']),'baselineCancelT':int(tg['t']),'boundT':int(bound['t']) if bound else None,'triggerTimeExact':bool(bound and int(bound['t'])==int(tg['t'])),'exercised':bool(cr.get('targetForkExercised')),'suppressed':int(cr.get('targetForkSuppressed') or 0),'terminalDelta':{'pnl':cc['pnl']-bc['pnl'],'best':cc['best']-bc['best'],'floor':cc['floor']-bc['floor'],'gap':(cc['best']-cc['floor'])-(bc['best']-bc['floor']),'fills':cc['fills']-bc['fills'],'submits':cc['submits']-bc['submits'],'activeFillQty':cc['activeFillQty']-bc['activeFillQty']},'forkValue':fv,'events':cr.get('targetForkEvents',[]),'correct':cc['correct'] and cc['quotaExcess']<=EPS and cc['unauthOverflow']<=EPS};rows.append(row);print(json.dumps({'marketId':m,'key':tg['key'],'targetT':tg['t'],'boundT':row['boundT'],'exact':row['triggerTimeExact'],'exercised':row['exercised'],'correct':row['correct'],'pnlDelta':row['terminalDelta']['pnl']},ensure_ascii=False),flush=True)
        gates={'allExactTriggerTimes':all(x['triggerTimeExact'] for x in rows),'allTargetsExercised':all(x['exercised'] and x['suppressed']>0 for x in rows),'allTriggerStatesEqual':all((x.get('forkValue') or {}).get('triggerEqual',False) for x in rows),'correctnessPass':all(x['correct'] for x in rows)}
        out={'version':'LANE_G_R264_TARGETED_REPAIR_REANCHOR_EXACT_T_FORKS_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'gates':gates,'exactForkPass':all(gates.values()),'boundary':['fork binds exact (market,key,decision t), not key alone','only exact target SATELLITE_FRONTIER_REANCHOR is suppressed','after bound KEEP persists until structural priority loss or native terminal','no fixed policy window','baseline and candidate identical before exact target t','consumed only/no fresh/no winner decision input/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'gates':gates,'exactForkPass':out['exactForkPass'],'rows':len(rows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
