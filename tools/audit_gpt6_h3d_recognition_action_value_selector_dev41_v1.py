from __future__ import annotations
import json, math, zipfile
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
STATE_FILES=[
 BASE/'GPT6_H3B_CORE_ROLE_RESERVATION_SMOKE5_STATES_20260907.json',
 BASE/'GPT6_H3B_CORE_ROLE_RESERVATION_STAGEB16_STATES_20260907.json',
 BASE/'GPT6_H3C_RECOGNITION_ACTION_VALUE_DEV20_STATES_20260907.json',
]
RESULT_FILES=[
 BASE/'GPT6_H3C_CORE_RESERVATION_RECOGNITION_TIMING_SMOKE5_RESULT_20260907.json',
 BASE/'GPT6_H3C_CORE_RESERVATION_RECOGNITION_STAGEB16_RESULT_20260907.json',
 BASE/'GPT6_H3C_RECOGNITION_ACTION_VALUE_DEV20_RESULT_20260907.json',
]
BUNDLE=BASE/'v16_consumed_holdout100_bundle.zip'
OUT=BASE/'GPT6_H3D_RECOGNITION_ACTION_VALUE_SELECTOR_DEV41_RESULT_20260907.json'
EPS=1e-9
FAMILIES={
 'LIFECYCLE':['repairProgressFrac','remainingDebtQty','initialDebtQty','liveSlots','freeSlots','liveRepairSlots','liveExpandSlots','repairQtyOverDebt'],
 'ECONOMICS':['floor','best','gap','repairPrice','expandPrice','pairPriceSum','inventoryGap','cost'],
 'BOOK':['imbalance','spread'],
}
FAMILIES['COMBINED']=FAMILIES['LIFECYCLE']+FAMILIES['ECONOMICS']+FAMILIES['BOOK']

def fnum(x, default=0.0):
    try:return float(x)
    except:return float(default)

def feat(s):
    rp=fnum((s.get('repairCandidate') or {}).get('price'))
    rq=fnum((s.get('repairCandidate') or {}).get('qty'))
    ep=fnum((s.get('expandCandidate') or {}).get('price'))
    debt=fnum(s.get('remainingDebtQty'))
    inv=s.get('inventory') or {}
    return {
      'repairProgressFrac':fnum(s.get('repairProgressFrac')),
      'remainingDebtQty':debt,'initialDebtQty':fnum(s.get('initialDebtQty')),
      'liveSlots':fnum(s.get('liveSlots')),'freeSlots':fnum(s.get('freeSlots')),
      'liveRepairSlots':fnum(s.get('liveRepairSlots')),'liveExpandSlots':fnum(s.get('liveExpandSlots')),
      'repairQtyOverDebt':rq/debt if debt>EPS else 0.0,
      'floor':fnum(s.get('floor')),'best':fnum(s.get('best')),'gap':fnum(s.get('best'))-fnum(s.get('floor')),
      'repairPrice':rp,'expandPrice':ep,'pairPriceSum':rp+ep,
      'inventoryGap':abs(fnum(inv.get('UP'))-fnum(inv.get('DOWN'))),'cost':fnum(s.get('cost')),
      'imbalance':fnum((s.get('book') or {}).get('imbalance')),'spread':fnum((s.get('book') or {}).get('spread')),
    }

def terminal_pnl(t,w): return fnum(t.get('upQty') if w=='UP' else t.get('downQty'))-fnum(t.get('buyNotional'))
def shape(t): return fnum(t.get('best'))>2.0 and fnum(t.get('floor'))>-1.0

def model(): return make_pipeline(StandardScaler(),LogisticRegression(C=1.0,penalty='l2',solver='liblinear',max_iter=2000))

def safe_auc(y,p):
    return float(roc_auc_score(y,p)) if len(set(y))>1 else None

def main():
    states={}
    for p in STATE_FILES:
        for s in json.loads(p.read_text(encoding='utf-8'))['states']:states[int(s['marketId'])]=s
    results={}
    for p in RESULT_FILES:
        for r in json.loads(p.read_text(encoding='utf-8'))['rows']:results[int(r['marketId'])]=r
    with zipfile.ZipFile(BUNDLE) as z:
        winners={int(x['marketId']):str(x['winner']).upper() for x in json.loads(z.read('cohort.json').decode())['rows']}
    rows=[]
    for mid in sorted(results):
        s=states[mid]; r=results[mid]; w=winners[mid]
        I=r['branches']['CORE_IMMEDIATE_RESERVE']['terminal']; F=r['branches']['CORE_ON_CONFIRMED_FILL_RESERVE']['terminal']
        pi,pf=terminal_pnl(I,w),terminal_pnl(F,w); dp=pf-pi
        rows.append({'marketId':mid,'winnerPostHocLabelOnly':w,'features':feat(s),'pnlImmediate':pi,'pnlOnFill':pf,'deltaPnlOnFillMinusImmediate':dp,
                     'material':abs(dp)>EPS,'onFillBetter':dp>EPS if abs(dp)>EPS else None,'immediateTerminal':I,'onFillTerminal':F})
    material=[x for x in rows if x['material']]
    output={'version':'GPT6_H3D_RECOGNITION_ACTION_VALUE_SELECTOR_DEV41_V1_20260907','researchOnly':True,'runtimeAuthority':False,
            'n':len(rows),'materialN':len(material),'equalN':len(rows)-len(material),'families':{},'rows':rows,
            'boundary':['strict-past numeric state features only','winner used post-hoc label/scoring only','fixed L2 logistic C=1.0','fixed threshold .5','leave-one-market-out','no threshold scan','sealed holdout25 unopened']}
    for fam,cols in FAMILIES.items():
        # Materiality diagnostic LOOCV on all 41.
        ym=np.array([1 if x['material'] else 0 for x in rows],dtype=int); pm=[]
        for i,x in enumerate(rows):
            tr=[j for j in range(len(rows)) if j!=i]
            X=np.array([[rows[j]['features'][c] for c in cols] for j in tr],dtype=float); y=ym[tr]
            clf=model(); clf.fit(X,y); pm.append(float(clf.predict_proba(np.array([[x['features'][c] for c in cols]],dtype=float))[0,1]))
        # Value-direction LOOCV only on material rows.
        yv=np.array([1 if x['onFillBetter'] else 0 for x in material],dtype=int); pv=[]
        for i,x in enumerate(material):
            tr=[j for j in range(len(material)) if j!=i]
            X=np.array([[material[j]['features'][c] for c in cols] for j in tr],dtype=float); y=yv[tr]
            clf=model(); clf.fit(X,y); pv.append(float(clf.predict_proba(np.array([[x['features'][c] for c in cols]],dtype=float))[0,1]))
        # Train on all material rows only to get scores for economically-equal rows; material rows keep true OOF scores.
        Xall=np.array([[x['features'][c] for c in cols] for x in material],dtype=float); vall=model(); vall.fit(Xall,yv)
        p_by_mid={x['marketId']:p for x,p in zip(material,pv)}
        for x in rows:
            if x['marketId'] not in p_by_mid:
                p_by_mid[x['marketId']]=float(vall.predict_proba(np.array([[x['features'][c] for c in cols]],dtype=float))[0,1])
        selected=[]
        for x in rows:
            choose_on=p_by_mid[x['marketId']]>=0.5
            term=x['onFillTerminal'] if choose_on else x['immediateTerminal']
            pnl=x['pnlOnFill'] if choose_on else x['pnlImmediate']
            selected.append({'marketId':x['marketId'],'chooseOnFill':choose_on,'pOnFillBetter':p_by_mid[x['marketId']],
                             'selectedPnl':pnl,'baselinePnl':x['pnlImmediate'],'deltaVsImmediate':pnl-x['pnlImmediate'],'shape':shape(term),
                             'floor':fnum(term['floor']),'best':fnum(term['best']),'fills':fnum(term['fills']),'submits':fnum(term['submits'])})
        material_correct=sum((p>=0.5)==bool(y) for p,y in zip(pv,yv));
        baseline_total=sum(x['pnlImmediate'] for x in rows); selected_total=sum(x['selectedPnl'] for x in selected)
        deltas=[x['deltaVsImmediate'] for x in selected]; biggest=max(rows,key=lambda x:x['deltaPnlOnFillMinusImmediate'])['marketId']
        without=[x for x in selected if x['marketId']!=biggest]
        output['families'][fam]={
          'features':cols,
          'materiality':{'auc':safe_auc(ym,pm),'accuracy':float(np.mean((np.array(pm)>=.5)==ym)),'logloss':float(log_loss(ym,pm)),'brier':float(brier_score_loss(ym,pm))},
          'valueDirection':{'auc':safe_auc(yv,pv),'correct':int(material_correct),'n':len(material),'accuracy':material_correct/len(material),'probabilities':[{'marketId':x['marketId'],'pOnFillBetterOOF':p,'labelOnFillBetter':bool(y)} for x,p,y in zip(material,pv,yv)]},
          'selector':{'baselineImmediateTotalPnl':baseline_total,'selectedTotalPnl':selected_total,'deltaTotalPnl':selected_total-baseline_total,
                      'improvedMarkets':sum(d>EPS for d in deltas),'equalMarkets':sum(abs(d)<=EPS for d in deltas),'worseMarkets':sum(d<-EPS for d in deltas),
                      'worstMarketDelta':min(deltas),'bestMarketDelta':max(deltas),'selectedShapeCount':sum(x['shape'] for x in selected),'selectedShapeRate':sum(x['shape'] for x in selected)/len(selected),
                      'selectedFloorSafeCount':sum(x['floor']>-1 for x in selected),'selectedBestGt2Count':sum(x['best']>2 for x in selected),
                      'selectedFills':sum(x['fills'] for x in selected),'selectedSubmits':sum(x['submits'] for x in selected),
                      'largestPositiveDeltaMarket':biggest,'deltaPnlWithoutLargestPositiveMarket':sum(x['selectedPnl']-x['baselinePnl'] for x in without),
                      'rows':selected}}
    passing=[]
    for fam,v in output['families'].items():
        vd=v['valueDirection']; sel=v['selector']
        if vd['correct']>=14 and (vd['auc'] or 0)>=0.65 and sel['deltaPnlWithoutLargestPositiveMarket']>0:passing.append(fam)
    output['developmentPassingFamilies']=passing
    output['decision']='OPEN_SEALED_HOLDOUT25_FOR_FIXED_FAMILY_CONFIRM' if passing else 'STOP_SELECTOR_HOLDOUT_REMAINS_SEALED'
    OUT.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':output['decision'],'passing':passing,'summary':{k:{'materiality':v['materiality'],'valueDirection':v['valueDirection']|{'probabilities':None},'selector':{kk:vv for kk,vv in v['selector'].items() if kk!='rows'}} for k,v in output['families'].items()}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
