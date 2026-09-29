from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_cycle_reentry_objective_local_v2 as v2
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

ROLE_INDEX={'NONE':0,'INIT':1,'REPAIR':2,'EXPAND':3}

def role_for(sim,side):
    ws=v2.weak_side(sim.inv)
    if ws is None:return 'INIT'
    return 'REPAIR' if side==ws else 'EXPAND'

class EpisodeSim(v2.TransitionSim):
    def __init__(self,tape,mode,models,traj=None):
        super().__init__(tape,mode,models,traj)
        self.episode_seq=0
        self.episode={s:None for s in ('UP','DOWN')}
    def process(self,t):
        pre={s:float(self.inv[s]) for s in ('UP','DOWN')}
        super().process(t)
        for s in ('UP','DOWN'):
            q=max(0.0,float(self.inv[s])-pre[s])
            ep=self.episode[s]
            if ep is not None and q>v1.EPS:
                ep['filled']=float(ep['filled'])+q
                ep['remaining']=max(0.0,float(ep['requested'])-float(ep['filled']))
                ep['lastFillT']=int(t)
                if ep['remaining']<=v1.EPS:ep['completed']=1
    def register_accept(self,t,side,qty):
        r=role_for(self,side)
        cur=self.episode.get(side)
        same_unresolved=bool(cur is not None and float(cur['remaining'])>v1.EPS and cur['role']==r)
        if same_unresolved:
            cur['requested']=float(cur['requested'])+float(qty)
            cur['remaining']=float(cur['remaining'])+float(qty)
            cur['acceptCount']=int(cur['acceptCount'])+1
            cur['lastAcceptT']=int(t)
            cur['continued']=1
        else:
            self.episode_seq+=1
            ws=v2.weak_side(self.inv)
            self.episode[side]={'id':self.episode_seq,'side':side,'role':r,'birthT':int(t),'lastAcceptT':int(t),'lastFillT':None,
                'requested':float(qty),'filled':0.0,'remaining':float(qty),'acceptCount':1,'continued':0,'completed':0,
                'birthWasWeak':int(ws is not None and side==ws),'birthAbsNet':abs(float(self.inv['UP'])-float(self.inv['DOWN']))}
        other='DOWN' if side=='UP' else 'UP'
        # opposite episode identity is retained as historical state, not deleted.
        return self.episode[side]
    def episode_features(self,t,proposed_side):
        cur=self.episode.get(proposed_side)
        opp='DOWN' if proposed_side=='UP' else 'UP'
        oep=self.episode.get(opp)
        def enc(ep):
            if ep is None:return [1,0,0,0,0,0,0,0,0,0,0,0,0,0]
            req=max(v1.EPS,float(ep['requested'])); rem=float(ep['remaining']); fill=float(ep['filled'])
            age=max(0,int(t)-int(ep['birthT']))/1000.0
            since_fill=(max(0,int(t)-int(ep['lastFillT']))/1000.0) if ep['lastFillT'] is not None else age
            role=[1.0 if ep['role']=='INIT' else 0.0,1.0 if ep['role']=='REPAIR' else 0.0,1.0 if ep['role']=='EXPAND' else 0.0]
            return [0,*role,req,fill,rem,fill/req,rem/req,age,since_fill,float(ep['acceptCount']),float(ep['birthWasWeak']),float(ep['continued'])]
        a=enc(cur);b=enc(oep)
        same_last=float(self.lastAcceptedSide==proposed_side)
        cur_unresolved=float(cur is not None and float(cur['remaining'])>v1.EPS)
        opp_unresolved=float(oep is not None and float(oep['remaining'])>v1.EPS)
        proposed_role=role_for(self,proposed_side)
        prole=[1.0 if proposed_role=='INIT' else 0.0,1.0 if proposed_role=='REPAIR' else 0.0,1.0 if proposed_role=='EXPAND' else 0.0]
        same_role=float(cur is not None and cur['role']==proposed_role)
        return np.asarray(a+b+[same_last,cur_unresolved,opp_unresolved,*prole,same_role],np.float32)
    def recovery_features_episode(self,t,x,qv,proposed_side):
        base=super().recovery_features(t,x,qv,proposed_side)
        return np.concatenate([base,self.episode_features(t,proposed_side)]).astype(np.float32)

def collect(tmp,train,traj,base_models):
    X=[];yA=[];yS=[];M=[];rows_pm=[]
    for i,cr in enumerate(train,1):
        sim=EpisodeSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',base_models,traj.get(str(cr['marketId']),[]))
        rows=pos=0
        try:
            ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first)
            end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
            for u in ups:
                t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);sim.advance_target(t);qv=v1.quotes(sim.book)
                if not qv:continue
                if sim.firstValid is None:sim.firstValid=t
                if (end-t)/1000.<=180:continue
                sim.seed_if_needed(t,qv)
                if not sim.seeded:continue
                x=sim.features(t,qv,ca,end);xx=x.reshape(1,-1);pa=float(base_models['action'].predict_proba(xx)[0,1])
                if pa<base_models['actionTh']:continue
                ps=float(base_models['side'].predict_proba(xx)[0,1]);base_side='UP' if ps>=base_models['sideTh'] else 'DOWN'
                qty=max(.01,float(np.expm1(np.clip(base_models['qty'].predict(xx)[0],0,5))));p=float(qv[base_side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
                if sim.boundaryActive and sim.reserved_authoritative(base_side)<=v1.EPS:
                    rf=sim.recovery_features_episode(t,x,qv,base_side);oa,oside,_=sim.oracle_action_authoritative(qv)
                    X.append(rf);yA.append(int(oa));yS.append(1 if oside=='UP' else 0);M.append(int(cr['marketId']));rows+=1;pos+=int(oa)
                accepted=bool(sim.submit(t,base_side,p,qty))
                if accepted:
                    sim.register_accept(t,base_side,qty);sim.finish_reentry(base_side)
        finally:sim.close()
        rows_pm.append({'marketId':int(cr['marketId']),'rows':rows,'positives':pos})
        if i%10==0:print(json.dumps({'progress':i,'rows':len(X),'positives':int(sum(yA))}),flush=True)
    return np.asarray(X,np.float32),np.asarray(yA,int),np.asarray(yS,int),np.asarray(M,int),rows_pm

def fit(X,yA,yS,M):
    ums=sorted(set(int(x) for x in M));tr=set(ums[:30]);va=set(ums[30:40]);it=np.where(np.isin(M,list(tr)))[0];iv=np.where(np.isin(M,list(va)))[0]
    action=HistGradientBoostingClassifier(max_iter=260,learning_rate=.04,max_leaf_nodes=23,min_samples_leaf=25,l2_regularization=4.,class_weight='balanced',random_state=21).fit(X[it],yA[it])
    pa=action.predict_proba(X[iv])[:,1]
    posa=it[yA[it]==1];posv=iv[yA[iv]==1]
    side=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=15,l2_regularization=3.,class_weight='balanced',random_state=22).fit(X[posa],yS[posa])
    ps=side.predict_proba(X[posv])[:,1] if len(posv) else np.array([])
    return {'trainRows':int(len(it)),'validationRows':int(len(iv)),'validationActionRate':float(yA[iv].mean()),
      'actionAuc':float(roc_auc_score(yA[iv],pa)) if len(set(yA[iv]))>1 else None,'actionAP':float(average_precision_score(yA[iv],pa)) if yA[iv].sum() else None,
      'validationSideN':int(len(posv)),'sideAuc':float(roc_auc_score(yS[posv],ps)) if len(posv) and len(set(yS[posv]))>1 else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_ep_ledger_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        base_models,off1,off2=lp.train_models(tmp,cohort,traj);train=[r for r in cohort if r['split']=='TRAIN40']
        X,yA,yS,M,pm=collect(tmp,train,traj,base_models);met=fit(X,yA,yS,M)
        ref=0.5068;delta=(met['actionAuc']-ref) if met['actionAuc'] is not None else None
        keep=bool(met['actionAuc'] is not None and (delta>=.05 or met['actionAuc']>=.60))
        out={'version':'ETH_RESPONSIBILITY_EPISODE_LEDGER_V1','researchOnly':True,'liveMutation':False,'rows':int(len(X)),'positives':int(yA.sum()),'markets':int(len(set(M))),
             'chronologyValidation':met,'referenceV2ActionAuc':ref,'actionAucDelta':delta,'decision':'KEEP_REPRESENTATION_SIGNAL' if keep else 'TESTED_REJECTED','perMarket':pm,
             'boundary':['TRAIN40 only','Target strict-past labels only','representation-only; no closed-loop authority','no Fresh101 fitting/scoring']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out|{'perMarket':'omitted'}),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
