from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,sqlite3,statistics
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.isotonic import IsotonicRegression
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_target_repair_progress_authority_v1 as pa

EPS=1e-9

def summarize_episodes(eps):
    if not eps:return {'n':0}
    cnt=[e['repairCount'] for e in eps];tm=[e['elapsedMs'] for e in eps]
    h=Counter('4+' if x>=4 else str(x) for x in cnt)
    return {'n':len(eps),'repairCountMedian':float(statistics.median(cnt)),'repairCountMean':float(statistics.mean(cnt)),
            'p0':h['0']/len(eps),'p1':h['1']/len(eps),'p2':h['2']/len(eps),'p3':h['3']/len(eps),'p4plus':h['4+']/len(eps),
            'elapsedMsMedian':float(statistics.median(tm)),'elapsedMsP75':float(np.quantile(tm,.75)),'elapsedMsP90':float(np.quantile(tm,.90))}

def target_episodes(db,asset):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
    out=[];cur=None;u=d=0.;active=False;rep=0;last_expand_t=None
    for r in rows:
        mid=int(r['market_id']);t=int(r['first_event_ms']);side=str(r['side'])
        if mid!=cur:
            cur=mid;u=d=0.;active=False;rep=0;last_expand_t=None
        weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;typ='REPAIR' if weak is not None and side==weak else 'EXPAND'
        if typ=='EXPAND':
            if active and last_expand_t is not None:
                out.append({'marketId':mid,'repairCount':rep,'elapsedMs':t-last_expand_t})
            active=True;rep=0;last_expand_t=t
        elif active:rep+=1
        sh=float(r['shares'])
        if side=='UP':u+=sh
        else:d+=sh
    return out

class StudentSeqSim(pa.ProgressAuthoritySim):
    def __init__(self,tape,mode,models,iso):
        super().__init__(tape,mode,models,iso);self.actionMeta={}
    def submit(self,t,side,p,q):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        typ='REPAIR' if weak is not None and side==weak else 'EXPAND'
        before=self.n
        super().submit(t,side,p,q)
        self.actionMeta[int(before)]={'placed':int(t),'type':typ,'side':side}
    def materialized_episodes(self):
        acts=[]
        for o in self.orders.values():
            n=int(o['n']);cum=float(o.get('cum') or 0.)
            if cum>EPS and n in self.actionMeta:
                z=self.actionMeta[n].copy();z['cum']=cum;z['n']=n;acts.append(z)
        acts.sort(key=lambda z:(z['placed'],z['n']))
        out=[];active=False;rep=0;last_expand_t=None
        for a in acts:
            if a['type']=='EXPAND':
                if active and last_expand_t is not None:out.append({'repairCount':rep,'elapsedMs':a['placed']-last_expand_t})
                active=True;rep=0;last_expand_t=a['placed']
            elif active:rep+=1
        return out,acts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_repair_streak_v11_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);iso,teacher=pa.build_iso(a.target_db)
        test=[r for r in cohort if r['split']!='TRAIN40'];student_eps=[];student_markets=[]
        for i,cr in enumerate(test,1):
            sim=StudentSeqSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:
                sim.run_student_progress(models,cr['winner']);eps,acts=sim.materialized_episodes();student_eps.extend(eps);student_markets.append({'marketId':int(cr['marketId']),'episodes':eps,'materializedActions':len(acts)})
            finally:sim.close()
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'studentEpisodes':len(student_eps)}),flush=True)
        btc=target_episodes(a.target_db,'BTC');eth=target_episodes(a.target_db,'ETH')
        out={'version':'ETH_BTC_REPAIR_STREAK_REENTRY_V11','researchOnly':True,'controllerMutation':False,
             'boundary':['Event-sequence anatomy only; no Target numeric threshold copied into runtime','Target = Maker parent actual-filled sequence; OUR = materialized Maker carriers only, zero-fill submits excluded','Action type is relative to own inventory at placement: weak-side=REPAIR, otherwise EXPAND','Fresh101 is consumed development evidence; no promotion'],
             'targetBTC':summarize_episodes(btc),'targetETH':summarize_episodes(eth),'studentProgressAuthority':summarize_episodes(student_eps),'targetProgressTeacher':teacher,
             'studentMarkets':student_markets,'round1Offline':off1,'round2Offline':off2}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'targetBTC':out['targetBTC'],'targetETH':out['targetETH'],'student':out['studentProgressAuthority']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
