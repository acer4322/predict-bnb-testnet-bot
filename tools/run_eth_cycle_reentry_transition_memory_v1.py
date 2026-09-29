from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics,math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

CAUSES=('FILL_PROGRESS','UP_CARRIER_RELEASE','DOWN_CARRIER_RELEASE','WEAK_SIDE_FLIP')

def weak_side(inv):
    u=float(inv['UP']);d=float(inv['DOWN'])
    if u<d-v1.EPS:return 'UP'
    if d<u-v1.EPS:return 'DOWN'
    return None

def metrics(sim):
    u=float(sim.inv['UP']);d=float(sim.inv['DOWN']);g=u+d;base=min(u,d)
    return {'up':u,'down':d,'cost':float(sim.cost),'absNet':abs(u-d),'pairCoverage':2*base/g if g>v1.EPS else 0.0,
            'floor':base-float(sim.cost),'weakSide':weak_side(sim.inv),'reservedUp':float(sim.reserved_authoritative('UP')),
            'reservedDown':float(sim.reserved_authoritative('DOWN'))}

class TransitionSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,traj=None):
        super().__init__(tape,mode,models)
        self.traj=sorted(traj or [],key=lambda r:int(r['t']));self.ti=0;self.target={'UP':0.0,'DOWN':0.0}
        self.boundaryActive=False;self.boundaryStartT=None;self.boundaryLastT=None;self.boundaryAnchor=None;self.boundaryCauses=set()
        self.lastAcceptedSide=None;self.boundaryCount=0;self.reentryCount=0;self.recoveryHoldCount=0;self.recoverySideOverrideCount=0
    def oracle_action_authoritative(self,qv):
        ds=[]
        for side in ('UP','DOWN'):
            d=max(0.0,float(self.target[side])-float(self.inv[side])-float(self.reserved_authoritative(side)))
            ds.append((d,side))
        for d,side in sorted(ds,reverse=True):
            if d<=.25:continue
            p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9;qty=d
            if qty<legal:
                if qty<.5*legal:continue
                qty=legal
            qty=min(qty,12.0)
            if self.econ_ok(side,p,qty):return 1,side,qty
        return 0,'UP',0.0
    def process(self,t):
        pre=metrics(self);super().process(t);post=metrics(self)
        du=post['up']-pre['up'];dd=post['down']-pre['down']
        causes=[]
        if du>v1.EPS or dd>v1.EPS:causes.append('FILL_PROGRESS')
        if pre['reservedUp']>v1.EPS and post['reservedUp']<=v1.EPS:causes.append('UP_CARRIER_RELEASE')
        if pre['reservedDown']>v1.EPS and post['reservedDown']<=v1.EPS:causes.append('DOWN_CARRIER_RELEASE')
        if pre['weakSide']!=post['weakSide'] and pre['weakSide'] is not None and post['weakSide'] is not None:causes.append('WEAK_SIDE_FLIP')
        if causes:
            if not self.boundaryActive:
                self.boundaryActive=True;self.boundaryStartT=int(t);self.boundaryCount+=1;self.boundaryAnchor=None
            self.boundaryLastT=int(t);self.boundaryCauses.update(causes)
    def recovery_features(self,t,x):
        x=np.asarray(x,np.float32)
        if self.boundaryAnchor is None:self.boundaryAnchor=x.copy()
        dx=x-self.boundaryAnchor
        age=float(max(0,int(t)-int(self.boundaryLastT or t)))
        cause=[1.0 if c in self.boundaryCauses else 0.0 for c in CAUSES]
        prev=[1.0 if self.lastAcceptedSide=='UP' else 0.0,1.0 if self.lastAcceptedSide=='DOWN' else 0.0]
        return np.concatenate([x,dx,np.asarray([age],np.float32),np.asarray(cause+prev,np.float32)]).astype(np.float32)
    def finish_reentry(self,side):
        if self.boundaryActive:self.reentryCount+=1
        self.boundaryActive=False;self.boundaryStartT=None;self.boundaryLastT=None;self.boundaryAnchor=None;self.boundaryCauses=set();self.lastAcceptedSide=side


def fit_recovery(X,yA,yS,markets):
    X=np.asarray(X,np.float32);yA=np.asarray(yA,int);yS=np.asarray(yS,int);markets=np.asarray(markets,int)
    ums=sorted(set(int(x) for x in markets));tr=set(ums[:30]);va=set(ums[30:40]);it=np.where(np.isin(markets,list(tr)))[0];iv=np.where(np.isin(markets,list(va)))[0]
    if len(it)==0 or len(iv)==0 or len(set(yA[it]))<2:raise RuntimeError('insufficient recovery action classes')
    action=HistGradientBoostingClassifier(max_iter=260,learning_rate=.04,max_leaf_nodes=23,min_samples_leaf=25,l2_regularization=4.,class_weight='balanced',random_state=11).fit(X[it],yA[it])
    pv=action.predict_proba(X[iv])[:,1];rate=float(yA[iv].mean());ath=float(np.quantile(pv,1-max(.001,min(.999,rate))))
    posa=it[yA[it]==1];posv=iv[yA[iv]==1]
    if len(posa)<20 or len(set(yS[posa]))<2:raise RuntimeError('insufficient recovery side classes')
    side=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=15,l2_regularization=3.,class_weight='balanced',random_state=12).fit(X[posa],yS[posa])
    psv=side.predict_proba(X[posv])[:,1] if len(posv) else np.asarray([]);sr=float(yS[posv].mean()) if len(posv) else .5;sth=float(np.quantile(psv,1-max(.001,min(.999,sr)))) if len(psv) else .5
    met={'trainMarkets':len(tr),'validationMarkets':len(va),'trainRows':int(len(it)),'validationRows':int(len(iv)),'validationActionRate':rate,
         'actionThreshold':ath,'actionAuc':float(roc_auc_score(yA[iv],pv)) if len(set(yA[iv]))>1 else None,
         'actionAP':float(average_precision_score(yA[iv],pv)) if yA[iv].sum()>0 else None,'validationSideN':int(len(posv)),
         'sideThreshold':sth,'sideAuc':float(roc_auc_score(yS[posv],psv)) if len(posv) and len(set(yS[posv]))>1 else None}
    return {'action':action,'side':side,'actionTh':ath,'sideTh':sth},met


def collect_recovery_training(tmp,train,traj,base_models):
    X=[];yA=[];yS=[];M=[];stats=[]
    for i,cr in enumerate(train,1):
        sim=TransitionSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',base_models,traj.get(str(cr['marketId']),[]))
        rows=0;pos=0
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
                # Only learn at a true re-entry opportunity that could be accepted structurally.
                if sim.boundaryActive and sim.reserved_authoritative(base_side)<=v1.EPS:
                    rf=sim.recovery_features(t,x);oa,oside,_=sim.oracle_action_authoritative(qv)
                    X.append(rf);yA.append(int(oa));yS.append(1 if oside=='UP' else 0);M.append(int(cr['marketId']));rows+=1;pos+=int(oa)
                accepted=bool(sim.submit(t,base_side,p,qty))
                if accepted:sim.finish_reentry(base_side)
        finally:sim.close()
        stats.append({'marketId':int(cr['marketId']),'rows':rows,'positives':pos})
        if i%10==0:print(json.dumps({'recoveryCollectProgress':i,'rows':len(X),'positives':int(sum(yA))}),flush=True)
    return X,yA,yS,M,stats


def run_policy(sim,base_models,rec_models,winner,policy):
    ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first)
    end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
    for u in ups:
        t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);qv=v1.quotes(sim.book)
        if not qv:continue
        if sim.firstValid is None:sim.firstValid=t
        if (end-t)/1000.<=180:continue
        sim.seed_if_needed(t,qv)
        if not sim.seeded:continue
        x=sim.features(t,qv,ca,end);xx=x.reshape(1,-1);pa=float(base_models['action'].predict_proba(xx)[0,1])
        if pa<base_models['actionTh']:continue
        ps=float(base_models['side'].predict_proba(xx)[0,1]);side='UP' if ps>=base_models['sideTh'] else 'DOWN'
        if sim.boundaryActive:
            rf=sim.recovery_features(t,x).reshape(1,-1);pra=float(rec_models['action'].predict_proba(rf)[0,1])
            if pra<rec_models['actionTh']:
                sim.recoveryHoldCount+=1;continue
            if policy=='GATE_SIDE':
                prs=float(rec_models['side'].predict_proba(rf)[0,1]);rside='UP' if prs>=rec_models['sideTh'] else 'DOWN'
                if rside!=side:sim.recoverySideOverrideCount+=1
                side=rside
        p=float(qv[side]['bid']);qty=max(.01,float(np.expm1(np.clip(base_models['qty'].predict(xx)[0],0,5))));qty=max(qty,1/p);qty=min(qty,12.)
        accepted=bool(sim.submit(t,side,p,qty))
        if accepted:sim.finish_reentry(side)
    end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2)
    win=str(winner).upper();opp='UP' if win=='DOWN' else 'DOWN';pnl=float(sim.inv[win]-sim.cost);op=float(sim.inv[opp]-sim.cost);gross=sum(sim.inv.values())
    return {'pnl':pnl,'oppositePnl':op,'buyNotional':float(sim.cost),'pairCoverage':2*min(sim.inv.values())/gross if gross>v1.EPS else 0.0,
            'floor':min(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,
            'up':sim.inv['UP'],'down':sim.inv['DOWN'],'boundaries':sim.boundaryCount,'reentries':sim.reentryCount,
            'recoveryHolds':sim.recoveryHoldCount,'sideOverrides':sim.recoverySideOverrideCount}


def agg(rows):
    buy=sum(r['buyNotional'] for r in rows);p=sum(r['pnl'] for r in rows);active=[r for r in rows if r['buyNotional']>v1.EPS]
    return {'markets':len(rows),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,
            'winRate':sum(r['pnl']>0 for r in rows)/len(rows) if rows else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rows),
            'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),
            'meanAbsNet':statistics.mean(r['absNet'] for r in rows),'meanSubmits':statistics.mean(r['submits'] for r in rows),'meanFills':statistics.mean(r['fills'] for r in rows),
            'maxWin':max(r['pnl'] for r in rows),'maxLoss':min(r['pnl'] for r in rows),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rows),
            'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rows),'meanBoundaries':statistics.mean(r['boundaries'] for r in rows),
            'meanReentries':statistics.mean(r['reentries'] for r in rows),'meanRecoveryHolds':statistics.mean(r['recoveryHolds'] for r in rows),
            'meanSideOverrides':statistics.mean(r['sideOverrides'] for r in rows)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_cycle_reentry_tm_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        base_models,off1,off2=lp.train_models(tmp,cohort,traj);train=[r for r in cohort if r['split']=='TRAIN40'];test=[r for r in cohort if r['split']!='TRAIN40']
        X,yA,yS,M,collectStats=collect_recovery_training(tmp,train,traj,base_models);rec_models,recOffline=fit_recovery(X,yA,yS,M)
        rows=[]
        for policy in ('GATE_ONLY','GATE_SIDE'):
            pr=[]
            for i,cr in enumerate(test,1):
                sim=TransitionSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',base_models,None)
                try:r=run_policy(sim,base_models,rec_models,cr['winner'],policy)
                finally:sim.close()
                r.update({'marketId':int(cr['marketId']),'policy':policy,'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});pr.append(r);rows.append(r)
                if i%20==0:print(json.dumps({'policy':policy,'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in pr),'winsSoFar':sum(x['pnl']>0 for x in pr)}),flush=True)
        summaries=[]
        for policy in ('GATE_ONLY','GATE_SIDE'):
            s=agg([r for r in rows if r['policy']==policy]);s['policy']=policy;summaries.append(s)
        out={'version':'ETH_CYCLE_REENTRY_TRANSITION_MEMORY_V1','researchOnly':True,'liveMutation':False,
             'boundary':['Base = frozen two-round DAgger + BOOK_IMBALANCE bootstrap + Local Pending Reservation','Transition Memory trained only on original TRAIN40 student-controlled re-entry opportunities and strict-past Target Maker objective labels','Recovery authority active only after fill progress / carrier release / weak-side flip until first accepted responsibility','GATE_ONLY may veto premature re-entry but preserves base side','GATE_SIDE may veto and correct re-entry side','Fresh101 is already-consumed development evidence; no promotion from this cohort','No Target trajectory/objective/winner enters runtime decisions; <=180s no new exposure'],
             'round1Offline':off1,'round2Offline':off2,'recoveryTraining':{'rows':len(X),'positives':int(sum(yA)),'markets':len(set(M)),'offline':recOffline,'perMarket':collectStats},
             'summaries':summaries,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'recoveryTraining':out['recoveryTraining']|{'perMarket':'omitted'},'summaries':summaries},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
