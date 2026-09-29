from __future__ import annotations
import json,lzma,sqlite3,sys,math
from pathlib import Path
import joblib,numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.train_r2_multichild_keep_reassess_curriculum_v2 import rows_for_market,DB,OUT

ART=joblib.load(OUT/'r2_multichild_pairwise_ranker_v3.joblib')
MODEL=ART.get('ranker') if isinstance(ART,dict) else ART
SCALER=ART.get('scaler') if isinstance(ART,dict) else None
FEATURE_COUNT=int(getattr(MODEL,'n_features_in_',0) or 0)


def collect_pairs(mid:int):
    rows=rows_for_market(mid)
    groups={}
    for r in rows:groups.setdefault((r['marketId'],r['landmarkMs']),[]).append(r)
    out=[]
    for (m,lm),g in groups.items():
        pos=[r for r in g if r['keep']==1]
        neg=[r for r in g if r['keep']==0]
        for a in pos:
            for b in neg:
                xa=np.asarray(a['x'],float);xb=np.asarray(b['x'],float)
                # V3 was trained on pairwise difference vectors. Trim only if artifact declares fewer cols.
                d=xa-xb
                if FEATURE_COUNT and len(d)!=FEATURE_COUNT:
                    d=d[:FEATURE_COUNT]
                z=SCALER.transform([d]) if SCALER is not None else [d]
                score=float(MODEL.decision_function(z)[0])
                out.append({'marketId':m,'landmarkMs':lm,'keepOrderId':a['orderId'],'reassessOrderId':b['orderId'],'margin':score,'correct':score>0})
    return out


def main():
    con=sqlite3.connect(DB);ids=[int(r[0]) for r in con.execute("select distinct market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id desc limit 40")];con.close()
    # Treat latest slice as read-only shadow evaluation; no retraining.
    rows=[]
    for m in ids:rows+=collect_pairs(m)
    acc=sum(r['correct'] for r in rows)/len(rows) if rows else None
    by={}
    for r in rows:by.setdefault(r['landmarkMs'],[]).append(r)
    rep={'version':'R2_MULTICHILD_PAIRWISE_RANKER_V3_SHADOW_V1','researchOnly':True,'orderMutationAuthority':False,'cancelAuthority':False,'desiredPortfolioMutationAuthority':False,'markets':ids,'pairs':len(rows),'pairwiseConsistency':acc,'byAge':[{'ageSec':lm/1000,'pairs':len(g),'consistency':sum(x['correct'] for x in g)/len(g)} for lm,g in sorted(by.items())],'decision':'KEEP_READ_ONLY_SHADOW' if rows and acc is not None and acc>=0.8 else 'NEED_MORE_SHADOW_DATA','note':'No R2/HFT trajectory mutation. This evaluates only ranking outputs on existing closed-loop artifacts.'}
    (OUT/'r2_multichild_pairwise_ranker_v3_shadow_v1_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
