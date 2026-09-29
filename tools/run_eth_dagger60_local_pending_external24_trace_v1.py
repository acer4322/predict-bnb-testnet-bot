from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_dagger_fresh101_local_pending_extreme_trace_v1 as tr

MIDS=[1823553,1823598,1823603,1823611,1823614,1823755,1823769,1823886,1823894,1823897,1823907,1824031,1824034,1824037,1824045,1824293,1824296,1824301,1824401,1824747,1824755,1824758,1824840,1824845]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train-bundle',required=True);ap.add_argument('--eval-bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    ttrain=Path(tempfile.mkdtemp(prefix='dagger60_ext24_train_')); teval=Path(tempfile.mkdtemp(prefix='dagger60_ext24_eval_'))
    try:
        zipfile.ZipFile(a.train_bundle).extractall(ttrain); traincohort=json.load(open(ttrain/'cohort.json',encoding='utf-8'))['rows']; traj=json.load(open(ttrain/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(ttrain,traincohort,traj)
        zipfile.ZipFile(a.eval_bundle).extractall(teval); evalrows=json.load(open(teval/'cohort.json',encoding='utf-8'))['rows']; byid={int(r['marketId']):r for r in evalrows}
        if any(mid not in byid for mid in MIDS): raise RuntimeError('frozen market missing from eval bundle')
        rows=[];markets=[]
        for i,mid in enumerate(MIDS,1):
            cr=byid[mid]; sim=tr.TraceSim(teval/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models)
            try:r=sim.run_student(models,cr['winner']); mats,eps=sim.materialized_trace()
            finally:sim.close()
            r.update({'marketId':mid,'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy'),'duplicateBlocked':sim.duplicateBlocked,'localPendingBlocked':sim.localPendingBlocked})
            opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional']; rows.append(r)
            markets.append({'marketId':mid,'terminal':r,'actionAttempts':sim.traceActions,'fillEvents':sim.traceFills,'materializedActions':mats,'reexpandEpisodes':eps,'acceptedAttempts':sum(x['accepted'] for x in sim.traceActions),'blockedAttempts':sum(not x['accepted'] for x in sim.traceActions)})
            if i%4==0 or i==len(MIDS): print(json.dumps({'progress':i,'of':len(MIDS),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows),'actionsLogged':sum(len(x['actionAttempts']) for x in markets),'fillsLogged':sum(len(x['fillEvents']) for x in markets),'episodes':sum(len(x['reexpandEpisodes']) for x in markets)}),flush=True)
        out={'version':'DAGGER60_LOCAL_PENDING_EXTERNAL24_TRACE_V1_20260909','researchOnly':True,'runtimeAuthority':False,
             'selectionRule':'first 24 chronological eth_v9_frozen_holdout100_v1 markets after Fresh101 frontier; outcome-blind; development evidence only',
             'marketIds':MIDS,'round1Offline':off1,'round2Offline':off2,'summary':lp.agg(rows),'rows':rows,'markets':markets}
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':out['summary'],'actionsLogged':sum(len(x['actionAttempts']) for x in markets),'fillsLogged':sum(len(x['fillEvents']) for x in markets),'episodes':sum(len(x['reexpandEpisodes']) for x in markets)},ensure_ascii=False),flush=True)
    finally:
        shutil.rmtree(ttrain,ignore_errors=True);shutil.rmtree(teval,ignore_errors=True)
if __name__=='__main__':main()
