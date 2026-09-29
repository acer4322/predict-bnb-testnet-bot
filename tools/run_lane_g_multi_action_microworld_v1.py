from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error

ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']
TARGETS=['dFloor','dBest','dGap','repairPaid','newExposureQty','newExposureRisk','oldDebtRemaining','physicalFillQty','unsafeProb']
FEATURES=['scopeUp','debt','siblingQty','siblingPrice','ordinaryPrice','ordinaryQty','compositePrice','compositeQty','unreservedDebt','venueQty','initialFloor','initialBest','initialGap','pSiblingFill','pOrdinaryFill','pCompositeFill','pBranchWinsRace','actKeep','actOrdinary','actR303']
EPS=1e-9

def payoff(u,d,c):
    best=max(u,d)-c;floor=min(u,d)-c
    return floor,best,best-floor

def apply(u,d,c,side,p,q):
    if side=='UP':u+=q
    else:d+=q
    c+=p*q
    return u,d,c

def scenario(initial,spec,action,sfill,bfill,branch_first):
    u,d,c=initial[:3];debt=float(spec['debt']);repair_paid=0.0;new_q=0.0;new_risk=0.0;fillq=0.0;unsafe=0.0
    sq=float(spec['siblingRemaining']) if sfill else 0.0
    if action=='KEEP_REPAIR':
        if sq>0:
            u,d,c=apply(u,d,c,spec['repairSide'],float(spec['siblingPrice']),sq);repair_paid=min(debt,sq);fillq+=sq
        rem=max(0.0,debt-repair_paid)
    elif action=='ORDINARY_REEXPAND':
        bq=float(spec['ordinary']['qty']) if bfill else 0.0
        # Physical payoff is order independent; responsibility accounting is explicit.
        if sq>0:
            u,d,c=apply(u,d,c,spec['repairSide'],float(spec['siblingPrice']),sq);repair_paid=min(debt,sq);fillq+=sq
        if bq>0:
            u,d,c=apply(u,d,c,spec['ordinary']['side'],float(spec['ordinary']['price']),bq);new_q+=bq;new_risk+=bq*float(spec['ordinary']['price']);fillq+=bq
        rem=max(0.0,debt-repair_paid)
    else:
        bq=float(spec['composite']['qty']) if bfill else 0.0
        seq=[]
        if sfill:seq.append(('S',str(spec['repairSide']),float(spec['siblingPrice']),sq))
        if bfill:seq.append(('B',str(spec['composite']['side']),float(spec['composite']['price']),bq))
        if len(seq)==2 and branch_first:seq=[seq[1],seq[0]]
        rem=debt
        for _,side,p,q in seq:
            u,d,c=apply(u,d,c,side,p,q);fillq+=q
            rq=min(rem,q);oq=max(0.0,q-rq);rem=max(0.0,rem-rq);repair_paid+=rq;new_q+=oq;new_risk+=oq*p
        unsafe=float(new_risk>float(spec['composite']['risk'])+1e-8)
    f,b,g=payoff(u,d,c);return np.array([f-initial[3],b-initial[4],g-initial[5],repair_paid,new_q,new_risk,rem,fillq,unsafe],dtype=np.float64)

def expected(initial,spec,action,ps,pb,prace):
    # Binary full-fill vs terminal/cancel curriculum. No market-probability claim.
    out=np.zeros(len(TARGETS),dtype=np.float64)
    for sf,p_sf in [(False,1-ps),(True,ps)]:
        if action=='KEEP_REPAIR':
            out+=p_sf*scenario(initial,spec,action,sf,False,False);continue
        for bf,p_bf in [(False,1-pb),(True,pb)]:
            base=p_sf*p_bf
            if sf and bf:
                out+=base*(1-prace)*scenario(initial,spec,action,sf,bf,False)
                out+=base*prace*scenario(initial,spec,action,sf,bf,True)
            else:out+=base*scenario(initial,spec,action,sf,bf,False)
    return out

def make_data(doc,n,seed):
    rng=np.random.default_rng(seed);mids=np.array(sorted(doc['markets']),dtype=object);X=[];Y=[];G=[];S=[]
    for sid in range(n):
        mid=str(rng.choice(mids));m=doc['markets'][mid];base=m['initialPayoff'];sp0=m['spec'];scale=float(rng.uniform(.70,1.30))
        u=float(base['upQty'])*scale;d=float(base['downQty'])*scale;c=float(base['cost'])*scale;f,b,g=payoff(u,d,c)
        debt=float(sp0['debt'])*scale;sib=float(sp0['siblingRemaining'])*scale;cp=float(sp0['composite']['price']);venue=1.0/cp;unres=max(0.0,debt-sib);cq=unres+venue
        spec={'debt':debt,'repairSide':sp0['repairSide'],'siblingRemaining':sib,'siblingPrice':float(sp0['siblingPrice']),
              'ordinary':dict(sp0['ordinary']),'composite':dict(sp0['composite'])}
        spec['composite']['qty']=cq;spec['composite']['unreservedDebt']=unres;spec['composite']['venue']=venue
        ps=float(rng.uniform(.05,.95));po=float(rng.uniform(.05,.95));pc=float(rng.uniform(.05,.95));pr=float(rng.uniform(.05,.95))
        common=[1.0 if sp0['scopeSide']=='UP' else 0.0,debt,sib,float(sp0['siblingPrice']),float(sp0['ordinary']['price']),float(sp0['ordinary']['qty']),cp,cq,unres,venue,f,b,g,ps,po,pc,pr]
        initial=(u,d,c,f,b,g)
        for ai,action in enumerate(ACTIONS):
            pb=po if action=='ORDINARY_REEXPAND' else pc
            feats=common+[1.0 if ai==0 else 0.0,1.0 if ai==1 else 0.0,1.0 if ai==2 else 0.0]
            X.append(feats);Y.append(expected(initial,spec,action,ps,pb,pr));G.append(int(mid));S.append(sid)
    return np.asarray(X,np.float32),np.asarray(Y,np.float32),np.asarray(G,np.int64),np.asarray(S,np.int64)

def action_only_baseline(y,groups,states):
    # LOMO baseline: per-action target means from training anchors. Caller slices X separately.
    pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--anchors',required=True);ap.add_argument('--states',type=int,default=5000);ap.add_argument('--seed',type=int,default=260907);ap.add_argument('--trees',type=int,default=96);ap.add_argument('--output',required=True);ap.add_argument('--save-corpus',action='store_true');a=ap.parse_args()
    doc=json.loads(Path(a.anchors).read_text(encoding='utf-8'));X,Y,G,S=make_data(doc,a.states,a.seed);mids=sorted(set(G.tolist()))
    pred=np.zeros_like(Y,dtype=np.float64);base=np.zeros_like(Y,dtype=np.float64);folds=[]
    act=np.argmax(X[:,-3:],axis=1)
    for mid in mids:
        tr=G!=mid;te=G==mid
        model=ExtraTreesRegressor(n_estimators=a.trees,min_samples_leaf=2,max_features=1.0,n_jobs=-1,random_state=a.seed+int(mid)%10000)
        model.fit(X[tr],Y[tr]);pred[te]=model.predict(X[te])
        for ai in range(3):
            tm=tr & (act==ai);em=te & (act==ai);mu=Y[tm].mean(axis=0) if tm.any() else Y[tr].mean(axis=0);base[em]=mu
        folds.append({'heldOutAnchor':int(mid),'rows':int(te.sum())})
    mae=[float(mean_absolute_error(Y[:,j],pred[:,j])) for j in range(Y.shape[1])]
    bmae=[float(mean_absolute_error(Y[:,j],base[:,j])) for j in range(Y.shape[1])]
    metrics={TARGETS[j]:{'actionOnlyMAE':bmae[j],'stateActionMAE':mae[j],'improvement':bmae[j]-mae[j]} for j in range(len(TARGETS))}
    # Within-state argmax accuracy for Floor/Best plus Pareto-set Jaccard on predicted vs true economics.
    true_floor=Y[:,0].reshape(a.states,3);true_best=Y[:,1].reshape(a.states,3);pr_floor=pred[:,0].reshape(a.states,3);pr_best=pred[:,1].reshape(a.states,3)
    floor_acc=float(np.mean(np.argmax(true_floor,axis=1)==np.argmax(pr_floor,axis=1)));best_acc=float(np.mean(np.argmax(true_best,axis=1)==np.argmax(pr_best,axis=1)))
    def front(f,b):
        out=[]
        for i in range(3):
            dom=False
            for j in range(3):
                if j==i:continue
                if f[j]>=f[i]-1e-9 and b[j]>=b[i]-1e-9 and (f[j]>f[i]+1e-9 or b[j]>b[i]+1e-9):dom=True;break
            if not dom:out.append(i)
        return set(out)
    js=[]
    for i in range(a.states):
        t=front(true_floor[i],true_best[i]);p=front(pr_floor[i],pr_best[i]);js.append(len(t&p)/len(t|p) if t|p else 1.0)
    summary={'states':a.states,'rows':int(len(X)),'anchors':mids,'features':FEATURES,'targets':TARGETS,'metrics':metrics,'floorBestChoiceAccuracy':{'floorArgmax':floor_acc,'bestArgmax':best_acc,'paretoJaccardMean':float(np.mean(js))},'unsafeRateMean':float(Y[:,8].mean()),'folds':folds}
    gates={'allFinite':bool(np.isfinite(X).all() and np.isfinite(Y).all() and np.isfinite(pred).all()),'stateActionBeatsActionOnlyPrimary':bool(all(metrics[k]['improvement']>0 for k in ['dFloor','dBest','dGap','repairPaid','newExposureQty','newExposureRisk','oldDebtRemaining'])),'floorArgmaxAbove80pct':floor_acc>=.80,'bestArgmaxAbove80pct':best_acc>=.80,'paretoJaccardAbove80pct':float(np.mean(js))>=.80}
    out={'version':'LANE_G_MULTI_ACTION_MICROWORLD_V1_DOMAIN_RANDOMIZED_ACTION_VALUE_20260907','researchOnly':True,'runtimeAuthority':False,'curriculumOnly':True,'probabilityCalibration':'UNIFORM_DOMAIN_RANDOMIZATION_NOT_MARKET_CALIBRATED','summary':summary,'gates':gates,'trainingPass':all(gates.values()),'boundary':['Five consumed H100 exact-fork anchors only for geometry.','Binary full-fill vs terminal/cancel curriculum; pFill/race are synthetic state inputs, not market estimates.','No future realized fill is given to the model; only synthetic hazard probabilities are state features.','Targets remain vector-valued; no scalar reward or policy authority.','R303 uses Repair-first then overflow-second; overflow risk > one-unit authority is a separate unsafe target.','No fresh/no winner/no dream fill/no 8781.','Promotion requires later exact-HFT/world-model calibration.']}
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    if a.save_corpus:
        np.savez_compressed(op.parent/'corpus.npz',X=X,Y=Y,G=G,S=S)
    print(json.dumps({'ok':True,'trainingPass':out['trainingPass'],'gates':gates,'metrics':metrics,'choice':summary['floorBestChoiceAccuracy'],'unsafeRateMean':summary['unsafeRateMean']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
