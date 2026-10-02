from __future__ import annotations
import argparse,collections,json,math,os,tempfile,zipfile
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264

EPS=r264.EPS;v2=r264.v2;H=(3,5)
ROLES=('PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND')
ROUTES=('PASSIVE','ACTIVE')

def safe(x,d=0.0):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except:return d

def opp(s):return 'DOWN' if s=='UP' else 'UP'

class TraceSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape):
        super().__init__(tape,1,4);self.training_rows=[];self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _state_row(self,t,side,role,price,qty,route,key,source):
        qv=v2.base.quotes(self.book) or {};u=float(self.inv['UP']);d=float(self.inv['DOWN']);gross=u+d;dom='UP' if u>d+EPS else 'DOWN' if d>u+EPS else 'FLAT'
        ob=self._obligation_current();outstanding=float(ob.get('outstanding') or 0.0) if ob else 0.0;repaid=float(ob.get('repaidQty') or 0.0) if ob else 0.0;born=float(ob.get('bornQty') or 0.0) if ob else 0.0
        gen=int(self.scopeGeneration);repair_rows=self._live_dedicated_repair_rows(gen);represented=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in repair_rows)
        live=self._live_role_rows();pending=sum(1 for _,k,o,_ in live if o.get('cancelRequested'));repair_live=sum(1 for *_,r in live if r in {'ECONOMIC_CORE','SATELLITE_REPAIR'});expand_live=sum(1 for *_,r in live if r=='SATELLITE_EXPAND')
        bid=safe((qv.get(side) or {}).get('bid'));ask=safe((qv.get(side) or {}).get('ask'));mid=(bid+ask)/2 if ask>0 else bid
        try:risk_debt=float(self._risk_debt_outstanding())
        except:risk_debt=0.0
        try:avail=float(self._available_expand_risk_credit())
        except:avail=0.0
        try:scope_debt=float(self._scope_debt_qty())
        except:scope_debt=abs(u-d)
        return {'t':int(t),'key':str(key),'side':str(side),'role':str(role),'route':str(route),'source':str(source),'price':float(price),'qty':float(qty),
                'secondsLeft':max(0.0,(self._end_ms-int(t))/1000.0),'floor':min(u,d)-float(self.cost),'best':max(u,d)-float(self.cost),'absNet':abs(u-d),'coverage':2*min(u,d)/gross if gross>EPS else 0.0,'gross':gross,
                'scopeDebtQty':scope_debt,'obligationOutstanding':outstanding,'obligationRepaid':repaid,'obligationProgress':repaid/born if born>EPS else 0.0,'representedRepairQuota':represented,'representationMargin':represented-outstanding,
                'riskDebtOutstanding':risk_debt,'scopeRiskCreditTotal':float(getattr(self,'scopeRiskCreditTotal',0.0)),'scopeRiskCreditConsumed':float(getattr(self,'scopeRiskCreditConsumed',0.0)),'availableExpandCredit':avail,'repairProgressClocks':int(getattr(self,'scopeRepairProgressClocks',0)),
                'liveSlots':len(self.slot_key),'activeKeys':len(getattr(self,'activeKeys',set())),'repairLiveSlots':repair_live,'expandLiveSlots':expand_live,'pendingCancelCount':pending,
                'bookImbalance':safe(qv.get('imb')),'spread':safe(qv.get('spread')),'sideBid':bid,'sideAsk':ask,'sideMid':mid,'priceToBid':float(price)-bid,'askToPrice':ask-float(price),'pairLegal':1.0 if self._pair_ok(side,float(price)) else 0.0,
                'isRepairRole':1.0 if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} else 0.0,'isExpandRole':1.0 if role=='SATELLITE_EXPAND' else 0.0,'sideUp':1.0 if side=='UP' else 0.0,'sideIsDominant':1.0 if dom==side else 0.0,'sideIsWeak':1.0 if dom in {'UP','DOWN'} and dom!=side else 0.0}
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=self.n;ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok:self.training_rows.append(self._state_row(t,side,role,p,q,'PASSIVE',f'{side}_{before}','NATIVE_PASSIVE'))
        return ok
    def _submit_active(self,t,side,role,q,score,diag):
        before=self.n;ok=super()._submit_active(t,side,role,q,score,diag)
        if ok:
            key=f'{side}_{before}';o=self.orders.get(key) or {};self.training_rows.append(self._state_row(t,side,role,float(o.get('price') or (diag or {}).get('activePrice') or 0.0),q,'ACTIVE',key,'NATIVE_ACTIVE'))
        return ok
    def finalize_labels(self):
        fills=collections.defaultdict(list)
        for x in self.splitEvents:
            if x.get('event')=='ROLE_FILL_SPLIT':fills[str(x.get('key'))].append(x)
        cancels=collections.defaultdict(list)
        for x in self.slot_history:
            if x.get('event')=='SLOT_CANCEL_REQUEST':cancels[str(x.get('key'))].append(x)
        for r in self.training_rows:
            t0=int(r['t']);key=r['key']
            for h in H:
                t1=t0+h*1000;fs=[x for x in fills.get(key,[]) if t0<int(x.get('t') or 0)<=t1]
                r[f'fillQty{h}s']=sum(float(x.get('fillInc') or 0.0) for x in fs);r[f'anyFill{h}s']=1 if r[f'fillQty{h}s']>EPS else 0
                r[f'repairPayQty{h}s']=sum(float(x.get('repairAllocated') or 0.0) for x in fs);r[f'overflowQty{h}s']=sum(float(x.get('overflowRealized') or 0.0) for x in fs)
                r[f'cancelReq{h}s']=1 if any(t0<int(x.get('t') or 0)<=t1 for x in cancels.get(key,[])) else 0
            o=self.orders.get(key) or {};r['finalCum']=float(o.get('cum') or 0.0);r['eventualFill']=1 if r['finalCum']>EPS else 0
        return self.training_rows

def role_code(r):return [1.0 if r['role']==x else 0.0 for x in ROLES]+[1.0 if r['route']==x else 0.0 for x in ROUTES]+[float(r['sideUp'])]
STATE=['secondsLeft','floor','best','absNet','coverage','gross','scopeDebtQty','obligationOutstanding','obligationRepaid','obligationProgress','representedRepairQuota','representationMargin','riskDebtOutstanding','scopeRiskCreditTotal','scopeRiskCreditConsumed','availableExpandCredit','repairProgressClocks','liveSlots','activeKeys','repairLiveSlots','expandLiveSlots','pendingCancelCount','bookImbalance','spread','sideBid','sideAsk','sideMid','sideIsDominant','sideIsWeak']
ACTION=['price','qty','priceToBid','askToPrice','pairLegal','isRepairRole','isExpandRole']
def X(rows,with_action):
    a=np.asarray([[safe(r[k]) for k in STATE] for r in rows],float)
    if not with_action:return a
    b=np.asarray([[safe(r[k]) for k in ACTION]+role_code(r) for r in rows],float);return np.c_[a,b]
def auc(y,p):
    y=np.asarray(y,int);return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def fit_cls(xtr,xva,ytr,yva,seed):
    if len(set(ytr))<2:return {'n':len(yva),'baseRate':float(np.mean(yva)) if yva else None,'auc':None,'ba':None},None
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(xtr,ytr);p=m.predict_proba(xva)[:,1]
    return {'n':len(yva),'positiveSupport':int(sum(yva)),'baseRate':float(np.mean(yva)),'auc':auc(yva,p),'ba':float(balanced_accuracy_score(yva,(p>=.5).astype(int))) if len(set(yva))>1 else None},m
def fit_reg(xtr,xva,ytr,yva,seed):
    m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(xtr,ytr);p=np.maximum(0.0,m.predict(xva));return {'n':len(yva),'mae':float(mean_absolute_error(yva,p)),'actualMean':float(np.mean(yva)),'predMean':float(np.mean(p))},m

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='');ap.add_argument('--train-frac',type=float,default=.70);ap.add_argument('--output',required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='lane_g_r264_world_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort=sorted(json.loads(z.read('cohort.json'))['rows'],key=lambda r:(int(r.get('windowEndMs') or 0),int(r['marketId'])))
            if a.market_ids:
                wanted={int(x) for x in a.market_ids.split(',') if x.strip()};cohort=[x for x in cohort if int(x['marketId']) in wanted]
            for cr in cohort:z.extract(f"tapes/{int(cr['marketId'])}.json.xz",root)
        rows=[];market_diag=[]
        for i,cr in enumerate(cohort,1):
            mid=int(cr['marketId']);sim=TraceSim(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_r264('__UNSCORED__');rr=sim.finalize_labels()
            finally:sim.close()
            for q in rr:q['marketId']=mid;q['windowEndMs']=int(cr.get('windowEndMs') or 0)
            rows+=rr;market_diag.append({'marketId':mid,'rows':len(rr),'fills':int(r['fillEvents']),'correct':bool(r.get('r264CorrectnessPass'))});print(json.dumps({'progress':i,'of':len(cohort),**market_diag[-1]},ensure_ascii=False),flush=True)
        mids=[int(x['marketId']) for x in cohort]
        if len(mids)<3:
            out={'version':'LANE_G_R264_EXECUTION_WORLD_V1','researchOnly':True,'runtimeAuthority':False,'rows':len(rows),'marketDiagnostics':market_diag,'smokeOnly':True,'guards':['no winner labels','consumed only','realistic HFT','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');return
        cut=max(1,min(len(mids)-1,int(round(len(mids)*a.train_frac))));trset=set(mids[:cut]);tr=[r for r in rows if r['marketId'] in trset];va=[r for r in rows if r['marketId'] not in trset]
        xs0,xv0=X(tr,False),X(va,False);xs1,xv1=X(tr,True),X(va,True);report={'version':'LANE_G_R264_EXECUTION_WORLD_V1','researchOnly':True,'runtimeAuthority':False,'markets':len(mids),'trainMarkets':len(trset),'validationMarkets':len(mids)-len(trset),'trainRows':len(tr),'validationRows':len(va),'stateFeatures':STATE,'actionFeatures':ACTION+['roleOneHot','routeOneHot','sideUp'],'horizons':{},'marketDiagnostics':market_diag};models={}
        for h in H:
            hr={}
            for label in [f'anyFill{h}s',f'cancelReq{h}s']:
                yt=[int(r[label]) for r in tr];yv=[int(r[label]) for r in va];s0,m0=fit_cls(xs0,xv0,yt,yv,100+h);s1,m1=fit_cls(xs1,xv1,yt,yv,200+h);hr[label]={'stateOnly':s0,'stateAction':s1,'aucDeltaAction':None if s0['auc'] is None or s1['auc'] is None else s1['auc']-s0['auc']};models[(label,'stateAction')]=m1
            for label in [f'fillQty{h}s',f'repairPayQty{h}s',f'overflowQty{h}s']:
                yt=np.asarray([safe(r[label]) for r in tr]);yv=np.asarray([safe(r[label]) for r in va]);s0,m0=fit_reg(xs0,xv0,yt,yv,300+h);s1,m1=fit_reg(xs1,xv1,yt,yv,400+h);hr[label]={'stateOnly':s0,'stateAction':s1,'maeImprovementAction':s0['mae']-s1['mae']};models[(label,'stateAction')]=m1
            report['horizons'][str(h)]=hr
        inc=[]
        for hr in report['horizons'].values():
            for v in hr.values():
                if v.get('aucDeltaAction') is not None:inc.append(v['aucDeltaAction'])
                if 'maeImprovementAction' in v:inc.append(v['maeImprovementAction'])
        report['gates']={'allBaselineCorrect':all(x['correct'] for x in market_diag),'rowsNonzero':len(rows)>0,'actionContextAnyPositive':any(x>0 for x in inc),'actionContextPositiveMajority':sum(x>0 for x in inc)>=max(1,len(inc)//2)}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(report,indent=2),encoding='utf-8');joblib.dump({'stateFeatures':STATE,'actionFeatures':ACTION,'models':models},op.parent/'world_models.joblib')
        with (op.parent/'training_rows.jsonl').open('w',encoding='utf-8') as f:
            for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
        print(json.dumps({'ok':True,'rows':len(rows),'gates':report['gates'],'horizons':report['horizons']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
