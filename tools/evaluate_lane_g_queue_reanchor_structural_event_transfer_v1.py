from __future__ import annotations
import argparse,json,math,os,sys,tempfile,zipfile,shutil
from pathlib import Path
import numpy as np,joblib
from sklearn.metrics import roc_auc_score,brier_score_loss,mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_r264_targeted_repair_reanchor_decision_forks.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_r264_targeted_repair_reanchor_decision_forks as qf;import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_r264_targeted_repair_reanchor_decision_forks as qf
    from tools import train_lane_g_r264_execution_world_v1 as wm

EPS=1e-9

def row_for(sim,t,key,source):
    o=sim.orders.get(str(key));
    if not o:return None
    try:s=sim.snap(o);rem=float(s.get('leavesQty')) if s.get('leavesQty') is not None else sim._remaining(str(key))
    except Exception:rem=sim._remaining(str(key))
    return wm.TraceSim._state_row(sim,int(t),str(o['side']),str(sim.key_role.get(str(key)) or 'SATELLITE_REPAIR'),float(o['price']),max(0.0,float(rem)),'PASSIVE',str(key),source)

def label(sim,row):
    key=str(row['key']);t0=int(row['t'])
    fills=sorted([x for x in sim.splitEvents if x.get('event')=='ROLE_FILL_SPLIT' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0))
    terms=sorted([x for x in sim.slot_history if x.get('event')=='SLOT_RELEASE' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0))
    ft=int(fills[0]['t']) if fills else None;tt=int(terms[0]['t']) if terms else None
    if ft is None and tt is None:return {'eventObserved':False}
    et=min(x for x in [ft,tt] if x is not None);ff=bool(ft is not None and (tt is None or ft<=tt));fq=rq=0.0
    if ff:
        same=[x for x in fills if int(x.get('t') or 0)==ft];fq=sum(float(x.get('fillInc') or 0.0) for x in same);rq=sum(float(x.get('repairAllocated') or 0.0) for x in same)
    return {'eventObserved':True,'fillFirst':1 if ff else 0,'eventLagSec':(et-t0)/1000.0,'firstFillQty':fq,'firstRepairPayQty':rq,'eventT':et,'fillT':ft,'terminalT':tt}

def predict(model,row):
    x=wm.X([row],True);m=model['models'];return {'pFillFirst':float(m['fillFirst'].predict_proba(x)[0,1]),'predEventLagSec':max(0.0,float(np.expm1(m['eventLagLog'].predict(x)[0]))),'predFirstFillQty':max(0.0,float(m['firstFillQty'].predict(x)[0])),'predFirstRepairPayQty':max(0.0,float(m['firstRepairPayQty'].predict(x)[0]))}

class NativeCapture(qf.r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,target_key,target_t,*a,**kw):super().__init__(tape,*a,**kw);self.tk=str(target_key);self.tt=int(target_t);self.capture=None;self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));match=(int(t)==self.tt and str(key)==self.tk and reason=='SATELLITE_FRONTIER_REANCHOR' and self.capture is None)
        ok=super()._request_cancel(t,sid,reason)
        if match and ok:self.capture=row_for(self,t,self.tk,'NATIVE_REANCHOR_POST_CANCEL_REQUEST')
        return ok

class KeepCapture(qf.TargetedRepairReanchorFork):
    def __init__(self,tape,target_key,target_t,*a,**kw):super().__init__(tape,target_key,*a,**kw);self.tt=int(target_t);self.capture=None;self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));before=self.suppressed;ok=super()._request_cancel(t,sid,reason)
        if self.capture is None and int(t)==self.tt and str(key)==self.target_key and reason=='SATELLITE_FRONTIER_REANCHOR' and self.suppressed>before:self.capture=row_for(self,t,self.target_key,'KEEP_REPAIR_POST_SUPPRESSION')
        return ok

def auc(y,p):return float(roc_auc_score(y,p)) if len(set(y))>1 else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--targets',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    targets=json.loads(Path(a.targets).read_text(encoding='utf-8'))['targets'];model=joblib.load(a.model);by={}
    for x in targets:by.setdefault(int(x['marketId']),[]).append(x)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_queue_struct_transfer_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for i,tg in enumerate(targets,1):
            m=int(tg['marketId']);w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=NativeCapture(tape,tg['key'],tg['t'],1,4)
            try:br=b.run_r264(w);brow=b.capture;bl=label(b,brow) if brow else {'eventObserved':False}
            finally:b.close()
            k=KeepCapture(tape,tg['key'],tg['t'],1,4)
            try:kr=k.run_target(w);krow=k.capture;kl=label(k,krow) if krow else {'eventObserved':False}
            finally:k.close()
            rec={'marketId':m,'key':tg['key'],'t':int(tg['t']),'native':{'captured':brow is not None,'row':brow,'actual':bl,'pred':predict(model,brow) if brow else None,'correct':bool(br.get('r264CorrectnessPass'))},'keep':{'captured':krow is not None,'row':krow,'actual':kl,'pred':predict(model,krow) if krow else None,'correct':bool(kr.get('r264CorrectnessPass')),'exercised':bool(kr.get('targetForkExercised'))}}
            rows.append(rec);print(json.dumps({'progress':i,'of':len(targets),'marketId':m,'key':tg['key'],'nativeActual':bl,'keepActual':kl,'nativePred':rec['native']['pred'],'keepPred':rec['keep']['pred']},ensure_ascii=False),flush=True)
        flat=[]
        for r in rows:
            for act in ['native','keep']:
                z=r[act]
                if z['captured'] and z['actual'].get('eventObserved') and z['pred'] is not None:flat.append((r,act,z))
        y=[int(z['actual']['fillFirst']) for _,_,z in flat];p=[float(z['pred']['pFillFirst']) for _,_,z in flat]
        metrics={'states':len(flat),'fillPositive':sum(y),'fillFirstAUC':auc(y,p),'fillFirstBrier':float(brier_score_loss(y,p)) if y else None,'eventLagMAE':float(mean_absolute_error([z['actual']['eventLagSec'] for _,_,z in flat],[z['pred']['predEventLagSec'] for _,_,z in flat])) if flat else None}
        f=[z for _,_,z in flat if int(z['actual']['fillFirst'])==1]
        metrics['firstFillQtyMAE']=float(mean_absolute_error([z['actual']['firstFillQty'] for z in f],[z['pred']['predFirstFillQty'] for z in f])) if f else None
        metrics['firstRepairPayQtyMAE']=float(mean_absolute_error([z['actual']['firstRepairPayQty'] for z in f],[z['pred']['predFirstRepairPayQty'] for z in f])) if f else None
        fill_pairs=fill_ok=lag_pairs=lag_ok=0
        for r in rows:
            n=r['native'];k=r['keep']
            if not(n['captured'] and k['captured'] and n['actual'].get('eventObserved') and k['actual'].get('eventObserved')):continue
            yn=int(n['actual']['fillFirst']);yk=int(k['actual']['fillFirst'])
            if yn!=yk:
                fill_pairs+=1;pn=float(n['pred']['pFillFirst']);pk=float(k['pred']['pFillFirst']);fill_ok+=int((pk>pn) if yk>yn else (pn>pk))
            ln=float(n['actual']['eventLagSec']);lk=float(k['actual']['eventLagSec'])
            if abs(ln-lk)>1e-9:
                lag_pairs+=1;pn=float(n['pred']['predEventLagSec']);pk=float(k['pred']['predEventLagSec']);lag_ok+=int((pk<pn) if lk<ln else (pn<pk))
        metrics.update({'pairedFillRankingAccuracy':fill_ok/fill_pairs if fill_pairs else None,'pairedFillEligible':fill_pairs,'pairedLagDirectionAccuracy':lag_ok/lag_pairs if lag_pairs else None,'pairedLagEligible':lag_pairs})
        smoke=len(targets)<=3
        gates={'allNativeCaptured':all(r['native']['captured'] for r in rows),'allKeepCaptured':all(r['keep']['captured'] for r in rows),'allKeepExercised':all(r['keep']['exercised'] for r in rows),'allEventsObserved':len(flat)==2*len(rows),'allCorrect':all(r['native']['correct'] and r['keep']['correct'] for r in rows),'predictionsFinite':all(math.isfinite(float(v)) for _,_,z in flat for v in z['pred'].values())}
        if not smoke:
            gates['fillFirstAUCAboveRandom']=metrics['fillFirstAUC'] is not None and metrics['fillFirstAUC']>.5
            gates['pairedFillRankingAboveHalf']=metrics['pairedFillRankingAccuracy'] is None or metrics['pairedFillRankingAccuracy']>.5
        out={'version':'LANE_G_QUEUE_REANCHOR_STRUCTURAL_EVENT_TRANSFER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'smokeOnly':smoke,'targetCount':len(targets),'marketCount':len(by),'rows':rows,'metrics':metrics,'gates':gates,'transferPass':all(gates.values()),'boundary':['exact KEEP vs NATIVE_REANCHOR target decisions frozen from prior 67-fork cohort','structural event label matches H100 carrier model: first ROLE_FILL_SPLIT vs SLOT_RELEASE','native state sampled immediately after cancel request; KEEP state immediately after exact suppression','no terminal PnL used','H100 structural-event training markets are disjoint from queue67 markets','consumed only/no fresh/no winner future/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'metrics':metrics,'gates':gates,'transferPass':out['transferPass']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
