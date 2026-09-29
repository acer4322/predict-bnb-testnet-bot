"""Small worker-only representation probe; no HFT, policy or financial tuning."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
    root=Path(__file__).resolve().parent
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for f,h in manifest['files'].items():assert sha(root/f)==h,(f,'hash mismatch')
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF','worker-only training'
    import numpy as np
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    data=json.loads((root/'dataset.json').read_text(encoding='utf-8'));rows=data['rows']
    assert all(r['book_received_ms']<r['anchor_ms']<r['next_fill_bucket'] for r in rows)
    assert all(r['last_fill_bucket']+999==r['anchor_ms'] for r in rows)
    if args.check_only:
        print(json.dumps(dict(check_pass=True,rows=len(rows),worker=socket.gethostname(),numpy=np.__version__,sklearn=sklearn.__version__)))
        return
    outdir=Path(os.environ['BTC5M_LAN_RESULT_DIR']);outdir.mkdir(parents=True,exist_ok=True)
    if (outdir/'result.json').exists():raise FileExistsError('existing results are immutable')
    started=time.monotonic()
    models={'CURRENT':data['base_features'],
        'LAST_EVENT':data['base_features']+['last_had_birth','last_had_payment','last_had_active_payment'],
        'PROGRESS':data['base_features']+data['history_features']}
    mids=sorted({r['market'] for r in rows});folds=[dict(name='LOMO_'+str(m),train=[x for x in mids if x!=m],test=[m]) for m in mids]
    folds.append(dict(name='FORWARD_ORIGINAL_TRAIN2',train=[2022527,2022538],test=[m for m in mids if m not in (2022527,2022538)]))
    results=[];predictions=[]
    for label in ('next_weak_payment','next_active_payment'):
        for fold in folds:
            train=[r for r in rows if r['market'] in fold['train'] and r['labels'][label] is not None]
            test=[r for r in rows if r['market'] in fold['test'] and r['labels'][label] is not None]
            assert not(set(r['market'] for r in train)&set(r['market'] for r in test))
            if fold['name'].startswith('FORWARD'):
                assert max(r['next_fill_bucket'] for r in train)<min(r['anchor_ms'] for r in test)
            y=np.array([r['labels'][label] for r in train]);yt=np.array([r['labels'][label] for r in test])
            def metrics(pred,mask=None):
                if mask is None:mask=np.ones(len(yt),dtype=bool)
                actual=yt[mask];p=pred[mask]
                return dict(n=len(actual),positive=int(actual.sum()),log_loss=float(log_loss(actual,p,labels=[0,1])),
                    brier=float(brier_score_loss(actual,p)),auc=float(roc_auc_score(actual,p)) if len(set(actual))==2 else None)
            base_pred=np.full(len(test),(y.sum()+1)/(len(y)+2))
            result=dict(label=label,fold=fold,train_rows=len(train),test_rows=len(test),models={'INTERCEPT':metrics(base_pred)})
            for name,features in models.items():
                x=np.array([[r['features'][f] for f in features] for r in train]);xt=np.array([[r['features'][f] for f in features] for r in test])
                model=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,solver='lbfgs',random_state=20260912))
                model.fit(x,y);pred=model.predict_proba(xt)[:,1]
                result['models'][name]=metrics(pred)
                result['models'][name]['per_market']={str(m):metrics(pred,np.array([r['market']==m for r in test])) for m in fold['test']}
                for row,prediction in zip(test,pred):
                    predictions.append(dict(fold=fold['name'],label=label,model=name,market=row['market'],
                        anchor_ms=row['anchor_ms'],next_fill_bucket=row['next_fill_bucket'],y=row['labels'][label],p=float(prediction)))
                if name=='PROGRESS':
                    # Fixed history-displacement negative control, no additional fit.
                    shuffled=xt.copy();rng=np.random.default_rng(20260912)
                    hist_start=len(data['base_features'])
                    for m in fold['test']:
                        indices=np.array([i for i,r in enumerate(test) if r['market']==m])
                        shuffled[indices,hist_start:]=xt[rng.permutation(indices),hist_start:]
                    result['models']['PROGRESS_HISTORY_SHUFFLED']=metrics(model.predict_proba(shuffled)[:,1])
                    result['models'][name]['standardized_coefficients']=dict(zip(features,map(float,model[-1].coef_[0])))
                    assert int(model[-1].n_iter_[0])<1000
            results.append(result)
    summaries={}
    for label in ('next_weak_payment','next_active_payment'):
        lomo=[r for r in results if r['label']==label and r['fold']['name'].startswith('LOMO')]
        forward=next(r for r in results if r['label']==label and r['fold']['name'].startswith('FORWARD'))
        gains={comparison:[r['models'][comparison]['log_loss']-r['models']['PROGRESS']['log_loss'] for r in lomo] for comparison in ('CURRENT','LAST_EVENT')}
        forward_gains={comparison:{m:forward['models'][comparison]['per_market'][m]['log_loss']-v['log_loss'] for m,v in forward['models']['PROGRESS']['per_market'].items()} for comparison in ('CURRENT','LAST_EVENT')}
        meets=all(sum(g>0 for g in gains[k])>=6 and np.median(gains[k])>0 and np.median(list(forward_gains[k].values()))>0
                  and forward['models']['PROGRESS']['log_loss']<forward['models'][k]['log_loss']
                  and forward['models']['PROGRESS']['brier']<=forward['models'][k]['brier'] for k in gains)
        summaries[label]=dict(lomo_log_loss_improvement=gains,forward_per_market_log_loss_improvement=forward_gains,
            evidence_gate='SUPPORTED_REPRESENTATION_CLUE' if meets else 'NOT_SUPPORTED_UNDER_FIXED_PROBE',
            causal_claim=False,runtime_promotion=False)
    output=dict(version='BTC5M_TARGET_NEXT_EVENT_PROGRESS_V1',status='COMPLETE',worker=socket.gethostname(),
        manifest_sha256=sha(root/'manifest.json'),rows=len(rows),fold_results=results,summaries=summaries,
        elapsed_seconds=time.monotonic()-started,native=False,financial_parameters_changed=False,
        limitations=data['limitations']+['Linear fixed-regularization probe; failure does not prove no nonlinear information.',
            'LOMO trains on other markets including later clocks; forward TRAIN2 comparison is separately required.',
            'History permutation may break state-history dependence and is only a negative-control diagnostic.',
            'No causal effect or private target trigger can be established from this conditional observed-fill prediction.'])
    (outdir/'predictions.json').write_text(json.dumps(predictions,indent=2)+'\n',encoding='utf-8')
    output['predictions_sha256']=sha(outdir/'predictions.json')
    (outdir/'result.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=output['status'],summaries=summaries,elapsed_seconds=output['elapsed_seconds'])),flush=True)


if __name__=='__main__':main()
