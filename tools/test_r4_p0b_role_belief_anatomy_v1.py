from __future__ import annotations
import json,glob,joblib,statistics,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_target_teacher_economic_progress_v1 as lane
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
TRANS=ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib'
OUT=P/'r4_p0b_role_belief_anatomy_v1.json'
ROLES={'REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING','AMBIGUOUS_TRADEOFF'}

def prob1(m,X):
    p=m.predict_proba(X); cls=list(m.classes_); return p[:,cls.index(1)]

def psup(a,b):
    if not a or not b:return None
    s=0.0
    for x in a:
        for y in b:s+=1.0 if x>y else 0.5 if x==y else 0.0
    return s/(len(a)*len(b))

def summarize(rows,score):
    out={}
    for split in ('development','independentReplication'):
        out[split]={}
        for role in ('REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING','AMBIGUOUS_TRADEOFF'):
            xs=[float(r[score]) for r in rows if r['split']==split and r['role']==role]
            if xs:out[split][role]={'n':len(xs),'median':statistics.median(xs),'min':min(xs),'max':max(xs)}
    return out

def main():
    rows=[]
    files=glob.glob(str(P/'r4_p0b_role_group_branches_development_*_v1.json'))+glob.glob(str(P/'r4_p0b_role_group_branches_independentReplication_*_v1.json'))
    for f in files:
        d=json.loads(Path(f).read_text(encoding='utf-8')); sp=d['split']
        for r in d['rows']:
            if r.get('role') not in ROLES:continue
            c=r['candidate']; z={'split':sp,'marketId':int(r['marketId']),'candidateKey':r['candidateKey'],'role':r['role']}
            for k in lane.FULL:z[k]=float(c[k])
            side=str(c['candidateSide']); side_mid=float(c['predictUpMidPublic'] if side=='UP' else c['predictDownMidPublic'])
            z.update({'candidatePx':float(c['candidatePx']),'sideMid':side_mid,'candidatePxMinusSideMid':float(c['candidatePx'])-side_mid,'candidateUpside':float(c['candidateUpside']),'floor':float(c['floor']),'abs_gap':float(c['abs_gap']),'coverage':float(c['coverage']),'absnet_ratio':float(c['absnet_ratio']),'seconds_left':float(c['seconds_left'])})
            rows.append(z)
    d=pd.read_csv(lane.SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=lane.FULL+['market_id','t','seconds_left','abs_gap','risk_deficit']).copy()
    e=lane.add_future_labels(d).replace([np.inf,-np.inf],np.nan).dropna(subset=lane.FULL+['future_risk_deficit','future_abs_gap']).copy()
    pair=e[e.abs_gap>lane.EPS]
    pair_model=lane.model(31992).fit(pair[lane.FULL],pair.pair_balance_progress.astype(int))
    stack=joblib.load(STACK); m0=stack['M0_model']; m0f=list(stack['features']['full'])
    tr=joblib.load(TRANS); tm=tr['model']; tf=list(tr['features'])
    Xpair=np.asarray([[r[f] for f in lane.FULL] for r in rows],float)
    Xm0=np.asarray([[r[f] for f in m0f] for r in rows],float)
    Xtr=np.asarray([[r[f] for f in tf] for r in rows],float)
    pp=prob1(pair_model,Xpair); pm=prob1(m0,Xm0); pt=prob1(tm,Xtr)
    for r,a,b,c in zip(rows,pp,pm,pt):r['pairBalanceProgress']=float(a);r['m0EconomicProgress']=float(b);r['transitionNonprogress']=float(c)
    scores=['pairBalanceProgress','m0EconomicProgress','transitionNonprogress','candidatePx','candidatePxMinusSideMid','candidateUpside','events_15s','transitions_15s','current_mode_age_s']
    agg={s:summarize(rows,s) for s in scores}
    superiority={}
    for s in scores:
        superiority[s]={}
        for sp in ('development','independentReplication'):
            S=[float(r[s]) for r in rows if r['split']==sp and r['role']=='PARALLEL_STATE_SHAPING'];R=[float(r[s]) for r in rows if r['split']==sp and r['role']=='REJECT_NO_ACTION'];Pp=[float(r[s]) for r in rows if r['split']==sp and r['role']=='PREPOSITION_REPAIR_SUBSTITUTE']
            superiority[s][sp]={'STATE_gt_REJECT':psup(S,R),'PREPOSITION_gt_REJECT':psup(Pp,R),'STATE_gt_PREPOSITION':psup(S,Pp)}
    art={'version':'R4_P0B_ROLE_BELIEF_ANATOMY_V1','researchOnly':True,'actionAuthority':False,'rows':rows,'aggregate':agg,'probabilityOfSuperiority':superiority,'notes':['PAIR_BALANCE_PROGRESS_DIRECT is the previously frozen Lane-D semantic teacher, not a role classifier.','No thresholds or role fitting used.']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'rows':len(rows),'roleCounts':{k:sum(r['role']==k for r in rows) for k in sorted(ROLES)},'superiority':superiority},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
