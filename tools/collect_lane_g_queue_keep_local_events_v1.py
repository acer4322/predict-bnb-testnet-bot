from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_r264_targeted_repair_reanchor_decision_forks.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_r264_targeted_repair_reanchor_decision_forks as qf;import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_r264_targeted_repair_reanchor_decision_forks as qf
    from tools import train_lane_g_r264_execution_world_v1 as wm

class KeepCollector(qf.TargetedRepairReanchorFork):
    def __init__(self,tape,target_key,*a,**kw):
        super().__init__(tape,target_key,*a,**kw);self.capture=None;self.captureT=None;self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _row(self,t,key):
        o=self.orders.get(str(key))
        if not o:return None
        try:s=self.snap(o);rem=float(s.get('leavesQty')) if s.get('leavesQty') is not None else self._remaining(str(key))
        except Exception:rem=self._remaining(str(key))
        return wm.TraceSim._state_row(self,int(t),str(o['side']),str(self.key_role.get(str(key)) or 'SATELLITE_REPAIR'),float(o['price']),max(0.0,rem),'PASSIVE',str(key),'QUEUE_KEEP_POST_SUPPRESSION')
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));before=self.suppressed;ok=super()._request_cancel(t,sid,reason)
        if self.capture is None and str(key)==self.target_key and reason=='SATELLITE_FRONTIER_REANCHOR' and self.suppressed>before:
            self.captureT=int(t);self.capture=self._row(t,self.target_key)
        return ok

def label(sim,row):
    if row is None:return {'eventObserved':False}
    key=str(row['key']);t0=int(row['t'])
    fills=sorted([x for x in sim.splitEvents if x.get('event')=='ROLE_FILL_SPLIT' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0))
    terms=sorted([x for x in sim.slot_history if x.get('event')=='SLOT_RELEASE' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0))
    ft=int(fills[0]['t']) if fills else None;tt=int(terms[0]['t']) if terms else None
    if ft is None and tt is None:return {'eventObserved':False,'censorLagSec':max(0.0,(sim._end_ms-t0)/1000.0)}
    et=min(x for x in [ft,tt] if x is not None);ff=bool(ft is not None and (tt is None or ft<=tt));fq=rq=oq=0.0
    if ff:
        same=[x for x in fills if int(x.get('t') or 0)==ft];fq=sum(float(x.get('fillInc') or 0.0) for x in same);rq=sum(float(x.get('repairAllocated') or 0.0) for x in same);oq=sum(float(x.get('overflowRealized') or 0.0) for x in same)
    return {'eventObserved':True,'fillFirst':1 if ff else 0,'terminalFirst':0 if ff else 1,'eventLagSec':max(0.0,(et-t0)/1000.0),'firstFillQty':fq,'firstRepairPayQty':rq,'firstOverflowQty':oq,'eventT':et,'fillT':ft,'terminalT':tt}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--targets',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    targets=json.loads(Path(a.targets).read_text(encoding='utf-8'))['targets'];by={}
    for x in targets:by.setdefault(int(x['marketId']),[]).append(x)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_keep_local_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for i,tg in enumerate(targets,1):
            m=int(tg['marketId']);sim=KeepCollector(tmp/f'{m}.json.xz',tg['key'],1,4)
            try:r=sim.run_target(co[m]['winner']);state=sim.capture;lab=label(sim,state)
            finally:sim.close()
            rec={'marketId':m,'key':str(tg['key']),'baselineCancelT':int(tg['t']),'captureT':sim.captureT,'captureMatchesBaselineT':sim.captureT==int(tg['t']),'state':state,'actual':lab,'exercised':bool(r.get('targetForkExercised')),'suppressed':int(r.get('targetForkSuppressed') or 0),'correct':bool(r.get('r264CorrectnessPass')) and float(r.get('repairQuotaExcessMax') or 0.0)<=1e-9 and float(r.get('unauthorizedOverflowQty') or 0.0)<=1e-9};rows.append(rec)
            print(json.dumps({'progress':i,'of':len(targets),'marketId':m,'key':tg['key'],'captureT':sim.captureT,'baselineT':int(tg['t']),'actual':lab,'correct':rec['correct']},ensure_ascii=False),flush=True)
        gates={'allCaptured':all(x['state'] is not None for x in rows),'allCaptureTimesMatchBaseline':all(x['captureMatchesBaselineT'] for x in rows),'allExercised':all(x['exercised'] and x['suppressed']>0 for x in rows),'allCorrect':all(x['correct'] for x in rows),'allEventsObserved':all(x['actual'].get('eventObserved') for x in rows)}
        out={'version':'LANE_G_QUEUE_KEEP_LOCAL_EVENTS_V1_20260907','researchOnly':True,'runtimeAuthority':False,'targetCount':len(targets),'marketCount':len(by),'rows':rows,'gates':gates,'capturePass':all(gates.values()),'boundary':['KEEP branch only from prior exact KEEP-vs-REANCHOR fork semantics','state captured at first exact SATELLITE_FRONTIER_REANCHOR suppression','label is first target-carrier ROLE_FILL_SPLIT vs SLOT_RELEASE after suppression','no terminal PnL/action winner target','consumed24 only/no fresh/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'gates':gates,'capturePass':out['capturePass'],'fillFirst':sum(int(x['actual'].get('fillFirst',0)) for x in rows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
