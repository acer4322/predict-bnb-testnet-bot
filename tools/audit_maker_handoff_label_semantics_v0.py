from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, log_loss

import train_target_maker_taker_coordination_big_v1 as coord

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
CSV=OUT/'post_taker_handoff_states_v1.csv'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=OUT/'maker_handoff_label_semantics_v0_report.json'
AUG=OUT/'post_taker_handoff_label_semantics_v0.csv'


def ro(path:Path):
    c=sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def sides_to_label(sides:set[str],taker_side:str)->str:
    if not sides:return 'PAUSE'
    if len(sides)>=2:return 'BOTH'
    x=next(iter(sides)); return 'SAME' if x==taker_side else 'OPP'

def label_to_sides(label:str,taker_side:str)->set[str]:
    opp='DOWN' if taker_side=='UP' else 'UP'
    if label=='SAME': return {taker_side}
    if label=='OPP': return {opp}
    if label=='BOTH': return {'UP','DOWN'}
    return set()

def metrics(y,pred,prob,classes):
    cm=confusion_matrix(y,pred,labels=classes)
    return {'n':len(y),'truthDistribution':{c:int((y==c).sum()) for c in classes},'predictedDistribution':{c:int(np.sum(pred==c)) for c in classes},'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,labels=classes,average='macro',zero_division=0)),'logLoss':float(log_loss(y,prob,labels=classes)),'perClassRecall':{c:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,c in enumerate(classes)},'confusionMatrix':{'labels':classes,'matrix':cm.tolist()}}

def train(df,label,name):
    features=coord.HANDOFF_FEATURE_SETS['FULL']; sp=coord.split_markets(df); parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}
    m=coord.ebm(features); m.fit(coord.numeric(parts['train'],features),parts['train'][label].astype(str).tolist()); classes=[str(x) for x in m.classes_]
    rep={'features':features,'splitMarkets':{k:len(v) for k,v in sp.items()}}
    for k in ('train','validation','test'):
        x=coord.numeric(parts[k],features); y=parts[k][label].astype(str); rep[k]=metrics(y,m.predict(x),m.predict_proba(x),classes)
    rep['topTerms']=coord.top_terms(m,20); art=OUT/f'handoff_{name}_full_v0.joblib'; joblib.dump({'features':features,'classes':classes,'model':m,'label':label},art); rep['artifact']=str(art); return rep

def main():
    df=pd.read_csv(CSV); con=ro(TARGET_DB)
    by_market=defaultdict(list)
    try:
        for mid in sorted(set(df.market_id.astype(int))):
            for r in con.execute("""select event_ms,side,shares from wallet_shadow_target_events where asset='BTC' and quote_type='BID' and role='MAKER' and market_id=? order by event_ms,id""",(int(mid),)):
                by_market[int(mid)].append(dict(r))
    finally: con.close()
    fill_labels=[]; union_labels=[]; fill_counts=[]; fill_shares=[]
    cross=defaultdict(int); pause_with_fill=0
    for _,r in df.iterrows():
        cp=int(r.checkpoint_ms); ts=str(r.intervention_side); ev=[e for e in by_market[int(r.market_id)] if cp < int(e['event_ms']) <= cp+5000]
        fs={str(e['side']) for e in ev}; fl=sides_to_label(fs,ts); ps=label_to_sides(str(r.label_handoff),ts); ul=sides_to_label(ps|fs,ts)
        fill_labels.append(fl); union_labels.append(ul); fill_counts.append(float(len(ev))); fill_shares.append(float(sum(float(e['shares']) for e in ev)))
        cross[(str(r.label_handoff),fl)]+=1
        if str(r.label_handoff)=='PAUSE' and fl!='PAUSE': pause_with_fill+=1
    df['label_handoff_fill5s']=fill_labels; df['label_handoff_activity_union5s']=union_labels; df['post5s_maker_fill_count_label_only']=fill_counts; df['post5s_maker_fill_shares_label_only']=fill_shares; df.to_csv(AUG,index=False)
    fillrep=train(df,'label_handoff_fill5s','fill5s'); unionrep=train(df,'label_handoff_activity_union5s','activity_union5s')
    baseline=json.loads((OUT/'report_handoff_full.json').read_text(encoding='utf-8'))
    report={'reportVersion':'MAKER_HANDOFF_LABEL_SEMANTICS_V0','researchOnly':True,'futureTargetUsedAsLabelOnly':True,'question':'Is post-Taker Maker handoff better defined by actual Maker fills or union of new placements+fills rather than new placements alone?','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'placementPauseRows':int((df.label_handoff=='PAUSE').sum()),'placementPauseButFillActive':int(pause_with_fill),'placementPauseButFillActiveRate':float(pause_with_fill/max(1,(df.label_handoff=='PAUSE').sum()))},'labelAgreement':{'placementVsFill':float((df.label_handoff==df.label_handoff_fill5s).mean()),'placementVsUnion':float((df.label_handoff==df.label_handoff_activity_union5s).mean()),'fillVsUnion':float((df.label_handoff_fill5s==df.label_handoff_activity_union5s).mean())},'placementVsFillCross':{f'{a}->{b}':n for (a,b),n in sorted(cross.items())},'historicalPlacementBaseline':{'validation':baseline['validation'],'test':baseline['test']},'fill5sModel':fillrep,'activityUnion5sModel':unionrep,'comparison':{},'guard':'All features stop at Taker completion. Maker fills/placements after completion are labels only.'}
    for split in ('validation','test'):
        bb=float(baseline[split]['balancedAccuracy']); report['comparison'][split]={'placementBalanced':bb,'fillBalanced':float(fillrep[split]['balancedAccuracy']),'fillLiftVsPlacement':float(fillrep[split]['balancedAccuracy'])-bb,'unionBalanced':float(unionrep[split]['balancedAccuracy']),'unionLiftVsPlacement':float(unionrep[split]['balancedAccuracy'])-bb,'placementMacroF1':float(baseline[split]['macroF1']),'fillMacroF1':float(fillrep[split]['macroF1']),'unionMacroF1':float(unionrep[split]['macroF1'])}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
