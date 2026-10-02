from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_only_refresh_notaker_wave40_plan_20260830.json'
SPLIT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_winrate_selector_train24_test16b_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_lifecycle_action_selector_train24_test16b_v1.json'
ACTIONS=['CONTINUE_PASSIVE','SAME_PRICE_REFRESH','REPRICE_1T','REPRICE_3T']
DIST={'CONTINUE_PASSIVE':0.0,'SAME_PRICE_REFRESH':0.0,'REPRICE_1T':1.0,'REPRICE_3T':3.0}


def f(x):
    try:
        z=float(x); return z if math.isfinite(z) else np.nan
    except Exception:return np.nan


def load_rows():
    plan=json.loads(PLAN.read_text(encoding='utf-8')); out={}
    for j in plan['jobs']:
        fp=ROOT/'data/research/lan_worker_returns'/j['job_id']/'result.json'
        if not fp.exists(): continue
        rep=json.loads(fp.read_text(encoding='utf-8'))
        for r in rep.get('rows') or []:
            if r.get('error') or not r.get('candidate') or not r.get('branches'): continue
            out[int(r['marketId'])]=r
    return out


def base_features(r):
    c=r['candidate']; p=c.get('portfolio') or {}; m=c.get('models') or {}; u=c.get('public') or {}
    act=None
    for md in ('REPRICE_1T','SAME_PRICE_REFRESH','REPRICE_3T'):
        a=(r['branches'].get(md) or {}).get('action')
        if isinstance(a,dict) and isinstance(a.get('pathAtCancel'),dict):act=a;break
    q=(act or {}).get('pathAtCancel') or {}
    rem=f(q.get('remainingQty')); cum=f(q.get('cumExecQty')); total=(0 if np.isnan(rem) else rem)+(0 if np.isnan(cum) else cum)
    return np.array([
      f(c.get('riskAgeMs')),f(c.get('bookAgeMs')),f(c.get('activeMakerOrders')),
      f(p.get('maker_abs_net')),f(p.get('maker_paired_coverage')),f(p.get('combined_abs_net')),f(p.get('combined_paired_coverage')),
      f(p.get('worst_case_floor')),f(p.get('best_case_pnl')),f(p.get('maker_avg_pair_edge')),
      f(m.get('pMakerUp')),f(m.get('pMakerDown')),f(m.get('pTaker1s')),f(m.get('pTaker3s')),
      f(u.get('secondsLeft')),f(u.get('directionScore')),f(u.get('spotQueueImbalance')),f(u.get('spotTakerImbalance1s')),
      f(u.get('futuresQueueImbalance')),f(u.get('futuresTakerImbalance1s')),
      f(q.get('orderAgeMs')),f(q.get('quoteOffsetTicks')),rem,cum,(cum/total if total>1e-9 and not np.isnan(cum) else np.nan),
      f(q.get('initialDepth')),f(q.get('publicCumDepletion')),1.0 if q.get('occupiedBefore') else 0.0
    ],float)


def outcome(r,a):
    if a=='CONTINUE_PASSIVE': return float(r['baselineScore']['pnlUsdt'])
    return float(r['branches'][a]['score']['pnlUsdt'])


def action_vec(r,a):
    b=base_features(r); dist=DIST[a]; refresh=float(a!='CONTINUE_PASSIVE'); same=float(a=='SAME_PRICE_REFRESH'); r1=float(a=='REPRICE_1T'); r3=float(a=='REPRICE_3T')
    # compact action-state interactions; all inputs are strict-past except action identity itself.
    qoff=b[21]; age=b[20]; floor=b[7]; cov=b[6]; edge=b[9]; active=b[2]; rem=b[22]
    inter=np.array([refresh,same,r1,r3,dist,
                    dist*qoff,dist*age/1000.0,dist*floor,dist*cov,dist*edge,dist*active,dist*rem,
                    refresh*qoff,refresh*age/1000.0,refresh*floor,refresh*edge],float)
    return np.concatenate([b,inter])


def eval_policy(rows, mids, win_model, pnl_model, mode):
    rr=[]
    for mid in mids:
        if mid not in rows: continue
        r=rows[mid]; acts=ACTIONS
        X=np.vstack([action_vec(r,a) for a in acts])
        pw=win_model.predict_proba(X)[:,1]; pp=pnl_model.predict(X)
        ci=0; bi=int(np.argmax(pw))
        if mode=='ARGMAX_WIN': chosen=acts[bi]
        elif mode=='MARGIN05': chosen=acts[bi] if bi!=ci and pw[bi]>=pw[ci]+0.05 else 'CONTINUE_PASSIVE'
        elif mode=='MARGIN10': chosen=acts[bi] if bi!=ci and pw[bi]>=pw[ci]+0.10 else 'CONTINUE_PASSIVE'
        elif mode=='WIN_THEN_PNL':
            feasible=np.where(pw>=0.50)[0]
            if len(feasible): chosen=acts[int(feasible[np.argmax(pp[feasible])])]
            else: chosen='CONTINUE_PASSIVE'
        else: raise ValueError(mode)
        bp=outcome(r,'CONTINUE_PASSIVE'); cp=outcome(r,chosen); oracle=max(acts,key=lambda a:((outcome(r,a)>0),outcome(r,a)))
        rr.append({'marketId':mid,'chosen':chosen,'pWin':{a:float(v) for a,v in zip(acts,pw)},'predPnl':{a:float(v) for a,v in zip(acts,pp)},
                   'baselinePnl':bp,'chosenPnl':cp,'oracleAction':oracle,'oraclePnl':outcome(r,oracle),
                   'conversion':('WIN' if bp>0 else 'LOSS')+'->'+('WIN' if cp>0 else 'LOSS')})
    return {'n':len(rr),'wins':sum(x['chosenPnl']>0 for x in rr),'baselineWins':sum(x['baselinePnl']>0 for x in rr),
            'pnl':sum(x['chosenPnl'] for x in rr),'baselinePnl':sum(x['baselinePnl'] for x in rr),
            'oracleWins':sum(x['oraclePnl']>0 for x in rr),'oraclePnl':sum(x['oraclePnl'] for x in rr),
            'conversions':dict(Counter(x['conversion'] for x in rr)),'actions':dict(Counter(x['chosen'] for x in rr)),'rows':rr}


def main():
    rows=load_rows(); sp=json.loads(SPLIT.read_text(encoding='utf-8'))
    train=[int(x) for x in sp['trainMarkets'] if int(x) in rows]; test=[int(x) for x in sp['testMarkets'] if int(x) in rows]
    X=[]; yw=[]; yp=[]; groups=[]
    for mid in train:
        r=rows[mid]
        for a in ACTIONS:
            val=outcome(r,a); X.append(action_vec(r,a)); yw.append(int(val>0)); yp.append(val); groups.append(mid)
    X=np.vstack(X); yw=np.array(yw); yp=np.array(yp,float)
    win=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=0.35,class_weight='balanced',max_iter=5000,solver='liblinear'))
    pnl=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=12.0))
    win.fit(X,yw); pnl.fit(X,yp)
    modes={m:eval_policy(rows,test,win,pnl,m) for m in ('ARGMAX_WIN','MARGIN05','MARGIN10','WIN_THEN_PNL')}
    fixed={}
    for a in ACTIONS:
        z=[]
        for mid in test:
            if mid not in rows:continue
            r=rows[mid];bp=outcome(r,'CONTINUE_PASSIVE');cp=outcome(r,a);z.append((bp,cp))
        fixed[a]={'wins':sum(cp>0 for bp,cp in z),'pnl':sum(cp for bp,cp in z),'baselineWins':sum(bp>0 for bp,cp in z),'baselinePnl':sum(bp for bp,cp in z)}
    out={'version':'R4_MAKER_LIFECYCLE_ACTION_SELECTOR_TRAIN24_TEST16B_V1','researchOnly':True,'actionAuthority':False,
         'training':'expanded market-action rows; terminal winner/PnL offline labels only; strict-past seam features + action identity/interactions',
         'trainMarkets':train,'testMarkets':test,'trainExactSeams':len(train),'testExactSeams':len(test),'trainActionRows':len(X),
         'fixedPoliciesOnTest':fixed,'policies':modes,
         'guards':['market-disjoint chronological train24->test16b split','no test threshold fitting','winner/terminal PnL labels offline only','no live mutation','passive Maker actions only; Taker fallback not yet included'],
         'interpretationBoundary':'Development selector diagnostic. Passing test16b is not promotion evidence.'}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    brief={k:{kk:v[kk] for kk in ('n','wins','baselineWins','pnl','baselinePnl','oracleWins','oraclePnl','conversions','actions')} for k,v in modes.items()}
    print(json.dumps({'train':train,'test':test,'fixed':fixed,'policies':brief},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
