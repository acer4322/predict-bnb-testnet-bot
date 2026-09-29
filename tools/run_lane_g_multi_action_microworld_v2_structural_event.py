from __future__ import annotations
import argparse,json,math,os,sys
from pathlib import Path
from copy import deepcopy
import numpy as np,joblib
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED));import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import train_lane_g_r264_execution_world_v1 as wm

ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']
TARGETS=['dFloor','dBest','dGap','repairPaid','newExposureQty','newExposureRisk','oldDebtRemaining','physicalFillQty','unsafeProb']
FEATURES=['scopeUp','debt','siblingQty','siblingPrice','ordinaryPrice','ordinaryQty','compositePrice','compositeQty','unreservedDebt','venueQty','initialFloor','initialBest','initialGap',
          'pSiblingFill','pSiblingCancel','qFracSibling','pOrdinaryFill','pOrdinaryCancel','qFracOrdinary','pCompositeFill','pCompositeCancel','qFracComposite','actKeep','actOrdinary','actR303']
EPS=1e-9

def payoff(u,d,c):
    best=max(u,d)-c;floor=min(u,d)-c;return floor,best,best-floor

def apply(u,d,c,side,p,q):
    if side=='UP':u+=q
    else:d+=q
    c+=p*q;return u,d,c

def scenario(initial,spec,action,sfill,bfill):
    u,d,c=initial[:3];debt=float(spec['debt']);repair_paid=0.;new_q=0.;new_risk=0.;fillq=0.;unsafe=0.;rem=debt
    if sfill:
        q=float(spec['siblingRemaining']);u,d,c=apply(u,d,c,spec['repairSide'],float(spec['siblingPrice']),q);rq=min(rem,q);rem-=rq;repair_paid+=rq;fillq+=q
    if bfill and action=='ORDINARY_REEXPAND':
        q=float(spec['ordinary']['qty']);p=float(spec['ordinary']['price']);u,d,c=apply(u,d,c,spec['ordinary']['side'],p,q);new_q+=q;new_risk+=q*p;fillq+=q
    elif bfill and action=='R303_CONTINGENT_COMPOSITE':
        q=float(spec['composite']['qty']);p=float(spec['composite']['price']);u,d,c=apply(u,d,c,spec['composite']['side'],p,q);rq=min(rem,q);oq=max(0.,q-rq);rem-=rq;repair_paid+=rq;new_q+=oq;new_risk+=oq*p;fillq+=q;unsafe=float(new_risk>float(spec['composite']['risk'])+1e-8)
    f,b,g=payoff(u,d,c);return np.asarray([f-initial[3],b-initial[4],g-initial[5],repair_paid,new_q,new_risk,rem,fillq,unsafe],float)

def lam5(p):
    p=min(max(float(p),0.0),0.999999);return -math.log(max(1e-9,1.0-p))/5.0

def expected_one_event(initial,spec,action,h):
    # Structural-event kernel: only the first competing fill/cancel event is applied.
    sq=deepcopy(spec);sq['siblingRemaining']=float(spec['siblingRemaining'])*float(h['qFracSibling'])
    no=scenario(initial,sq,action,False,False)
    rates=[('SF',lam5(h['pSiblingFill'])),('SC',lam5(h['pSiblingCancel']))]
    if action!='KEEP_REPAIR':
        if action=='ORDINARY_REEXPAND':sq['ordinary']['qty']=float(spec['ordinary']['qty'])*float(h['qFracOrdinary']);bf=h['pOrdinaryFill'];bc=h['pOrdinaryCancel']
        else:sq['composite']['qty']=float(spec['composite']['qty'])*float(h['qFracComposite']);bf=h['pCompositeFill'];bc=h['pCompositeCancel']
        rates += [('BF',lam5(bf)),('BC',lam5(bc))]
    den=sum(r for _,r in rates)
    if den<=EPS:return no
    out=np.zeros(len(TARGETS),float)
    for typ,r in rates:
        w=r/den
        if typ=='SF':v=scenario(initial,sq,action,True,False)
        elif typ=='BF':v=scenario(initial,sq,action,False,True)
        else:v=no
        out+=w*v
    return out

def logit(p):
    p=np.clip(np.asarray(p,float),1e-6,1-1e-6);return np.log(p/(1-p)).reshape(-1,1)
def calibrate(model,p):return np.asarray(model.predict_proba(logit(p))[:,1],float)

def build_bank(rows,world,fill_cal,cancel_cal):
    mids=[m for _,m in sorted({(int(r['windowEndMs']),int(r['marketId'])) for r in rows})];train70=set(mids[:70]);z=[r for r in rows if int(r['marketId']) in train70 and str(r.get('route'))=='PASSIVE']
    X=wm.X(z,True);pf_raw=world['models'][('anyFill5s','stateAction')].predict_proba(X)[:,1];pc_raw=world['models'][('cancelReq5s','stateAction')].predict_proba(X)[:,1];fq=np.maximum(0.,world['models'][('fillQty5s','stateAction')].predict(X))
    pf=calibrate(fill_cal,pf_raw);pc=calibrate(cancel_cal,pc_raw)
    bank={}
    for i,r in enumerate(z):
        qty=max(EPS,float(r.get('qty') or 0.0));qfrac=min(1.0,max(0.05,float(fq[i])/(max(0.02,float(pf[i]))*qty)))
        role=str(r.get('role'));grp='EXPAND' if role=='SATELLITE_EXPAND' else 'REPAIR' if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} else None
        if grp is None:continue
        m=int(r['marketId']);bank.setdefault(m,{'REPAIR':[],'EXPAND':[]})[grp].append({'pFill':float(pf[i]),'pCancel':float(pc[i]),'qFrac':qfrac,'role':role,'price':float(r.get('price') or 0.),'qty':qty})
    bank={m:v for m,v in bank.items() if v['REPAIR'] and v['EXPAND']}
    return bank

def quantiles(vals):
    if not vals:return None
    a=np.asarray(vals,float);return {'p10':float(np.quantile(a,.1)),'p50':float(np.quantile(a,.5)),'p90':float(np.quantile(a,.9)),'mean':float(a.mean())}

def make_data(doc,n,seed,bank):
    rng=np.random.default_rng(seed);amids=np.array(sorted(doc['markets']),dtype=object);bmids=np.array(sorted(bank),int);X=[];Y=[];G=[];S=[];haz=[]
    for sid in range(n):
        amid=str(rng.choice(amids));m=doc['markets'][amid];base=m['initialPayoff'];sp0=m['spec'];scale=float(rng.uniform(.70,1.30))
        u=float(base['upQty'])*scale;d=float(base['downQty'])*scale;c=float(base['cost'])*scale;f,b,g=payoff(u,d,c);debt=float(sp0['debt'])*scale;sib=float(sp0['siblingRemaining'])*scale;cp=float(sp0['composite']['price']);venue=1.0/cp;unres=max(0.,debt-sib);cq=unres+venue
        spec={'debt':debt,'repairSide':sp0['repairSide'],'siblingRemaining':sib,'siblingPrice':float(sp0['siblingPrice']),'ordinary':dict(sp0['ordinary']),'composite':dict(sp0['composite'])};spec['composite']['qty']=cq;spec['composite']['unreservedDebt']=unres;spec['composite']['venue']=venue
        bm=int(rng.choice(bmids));rbank=bank[bm]['REPAIR'];ebank=bank[bm]['EXPAND'];hs=rbank[int(rng.integers(len(rbank)))];hc=rbank[int(rng.integers(len(rbank)))];ho=ebank[int(rng.integers(len(ebank)))]
        h={'pSiblingFill':hs['pFill'],'pSiblingCancel':hs['pCancel'],'qFracSibling':hs['qFrac'],'pOrdinaryFill':ho['pFill'],'pOrdinaryCancel':ho['pCancel'],'qFracOrdinary':ho['qFrac'],'pCompositeFill':hc['pFill'],'pCompositeCancel':hc['pCancel'],'qFracComposite':hc['qFrac']}
        common=[1. if sp0['scopeSide']=='UP' else 0.,debt,sib,float(sp0['siblingPrice']),float(sp0['ordinary']['price']),float(sp0['ordinary']['qty']),cp,cq,unres,venue,f,b,g,h['pSiblingFill'],h['pSiblingCancel'],h['qFracSibling'],h['pOrdinaryFill'],h['pOrdinaryCancel'],h['qFracOrdinary'],h['pCompositeFill'],h['pCompositeCancel'],h['qFracComposite']]
        initial=(u,d,c,f,b,g)
        for ai,action in enumerate(ACTIONS):
            feats=common+[1. if ai==0 else 0.,1. if ai==1 else 0.,1. if ai==2 else 0.];X.append(feats);Y.append(expected_one_event(initial,spec,action,h));G.append(int(amid));S.append(sid)
        haz.append((h,bm))
    return np.asarray(X,np.float32),np.asarray(Y,np.float32),np.asarray(G,np.int64),np.asarray(S,np.int64),haz

def front(f,b):
    out=[]
    for i in range(3):
        dom=False
        for j in range(3):
            if i==j:continue
            if f[j]>=f[i]-1e-9 and b[j]>=b[i]-1e-9 and (f[j]>f[i]+1e-9 or b[j]>b[i]+1e-9):dom=True;break
        if not dom:out.append(i)
    return set(out)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--anchors',required=True);ap.add_argument('--training-rows',required=True);ap.add_argument('--world-model',required=True);ap.add_argument('--fill-calibrator',required=True);ap.add_argument('--cancel-calibrator',required=True);ap.add_argument('--states',type=int,default=5000);ap.add_argument('--trees',type=int,default=96);ap.add_argument('--seed',type=int,default=26090702);ap.add_argument('--output',required=True);ap.add_argument('--save-corpus',action='store_true');a=ap.parse_args()
    doc=json.loads(Path(a.anchors).read_text(encoding='utf-8'));rows=[json.loads(x) for x in Path(a.training_rows).read_text(encoding='utf-8').splitlines() if x.strip()];world=joblib.load(a.world_model);fc=joblib.load(a.fill_calibrator)['calibrators']['anyFill5s'];cc=joblib.load(a.cancel_calibrator)['calibrators']['cancelReq5s'];bank=build_bank(rows,world,fc,cc)
    X,Y,G,S,haz=make_data(doc,a.states,a.seed,bank);mids=sorted(set(G.tolist()));pred=np.zeros_like(Y,float);base=np.zeros_like(Y,float);act=np.argmax(X[:,-3:],axis=1);folds=[]
    for mid in mids:
        tr=G!=mid;te=G==mid;model=ExtraTreesRegressor(n_estimators=a.trees,min_samples_leaf=2,max_features=1.0,n_jobs=-1,random_state=a.seed+mid%10000).fit(X[tr],Y[tr]);pred[te]=model.predict(X[te])
        for ai in range(3):
            tm=tr&(act==ai);em=te&(act==ai);base[em]=Y[tm].mean(axis=0) if tm.any() else Y[tr].mean(axis=0)
        folds.append({'heldOutAnchor':mid,'rows':int(te.sum())})
    metrics={}
    for j,k in enumerate(TARGETS):
        am=float(mean_absolute_error(Y[:,j],pred[:,j]));bm=float(mean_absolute_error(Y[:,j],base[:,j]));metrics[k]={'actionOnlyMAE':bm,'stateActionMAE':am,'improvement':bm-am}
    tf=Y[:,0].reshape(a.states,3);tb=Y[:,1].reshape(a.states,3);pf=pred[:,0].reshape(a.states,3);pb=pred[:,1].reshape(a.states,3);facc=float(np.mean(np.argmax(tf,1)==np.argmax(pf,1)));bacc=float(np.mean(np.argmax(tb,1)==np.argmax(pb,1)));js=[]
    for i in range(a.states):
        t=front(tf[i],tb[i]);p=front(pf[i],pb[i]);js.append(len(t&p)/len(t|p) if t|p else 1.)
    hp=[x[0] for x in haz];hstats={k:quantiles([h[k] for h in hp]) for k in hp[0]}
    summary={'states':a.states,'rows':len(X),'anchors':mids,'executionBankMarkets':len(bank),'executionBankProfiles':sum(len(v['REPAIR'])+len(v['EXPAND']) for v in bank.values()),'hazardStats':hstats,'features':FEATURES,'targets':TARGETS,'metrics':metrics,'choice':{'floorArgmax':facc,'bestArgmax':bacc,'paretoJaccardMean':float(np.mean(js))},'folds':folds}
    gates={'bankAtLeast20Markets':len(bank)>=20,'allFinite':bool(np.isfinite(X).all() and np.isfinite(Y).all() and np.isfinite(pred).all()),'stateActionBeatsActionOnlyPrimary':all(metrics[k]['improvement']>0 for k in ['dFloor','dBest','dGap','repairPaid','newExposureQty','newExposureRisk','oldDebtRemaining']),'floorArgmaxAbove80pct':facc>=.80,'bestArgmaxAbove80pct':bacc>=.80,'paretoJaccardAbove80pct':float(np.mean(js))>=.80}
    out={'version':'LANE_G_MULTI_ACTION_MICROWORLD_V2_STRUCTURAL_EVENT_MARKET_HAZARD_20260907','researchOnly':True,'runtimeAuthority':False,'curriculumOnly':True,'kernel':'FIRST_COMPETING_STRUCTURAL_EVENT_ONLY','summary':summary,'gates':gates,'trainingPass':all(gates.values()),'boundary':['geometry from five consumed H100 exact-fork anchors','execution hazard bank from first70 consumed H100 R2.64 PASSIVE carriers only','fill and cancel probabilities use forward-validated fixed Platt calibration','Repair sibling and R303 sample Repair-role hazard profiles; Ordinary samples Expand-role profile from same execution-regime market','one structural event per step; Manager must re-decide after the event','5s models parameterize cause-specific exponential rates only; 5s is not a strategy timer','conditional fill size uses predicted E[fillQty]/P(fill), capped to submitted qty','cancel-request hazard is an execution proxy, not terminal-confirmation truth','vector targets only; no scalar reward/policy authority','no fresh/no dream fill/no 8781']}
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    if a.save_corpus:np.savez_compressed(op.parent/'corpus.npz',X=X,Y=Y,G=G,S=S)
    full=ExtraTreesRegressor(n_estimators=a.trees,min_samples_leaf=2,max_features=1.0,n_jobs=-1,random_state=a.seed).fit(X,Y);joblib.dump({'version':out['version'],'features':FEATURES,'targets':TARGETS,'model':full},op.parent/'action_value_model.joblib')
    print(json.dumps({'ok':True,'trainingPass':out['trainingPass'],'gates':gates,'choice':summary['choice'],'bankMarkets':len(bank),'bankProfiles':summary['executionBankProfiles'],'metrics':metrics},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
