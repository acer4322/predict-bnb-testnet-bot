from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
from collections import Counter
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_target_repair_progress_authority_v1 as pa
EPS=1e-9

class DriftSim(pa.ProgressAuthoritySim):
    def __init__(self,tape,mode,models,iso):
        super().__init__(tape,mode,models,iso);self.submitRole={};self.firstFillSeen=set();self.material=[]
    def role_now(self,side):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        return 'REPAIR' if weak is not None and side==weak else 'EXPAND'
    def submit(self,t,side,p,q):
        before=int(self.n);role=self.role_now(side);ok=super().submit(t,side,p,q)
        if ok is not False and int(self.n)>before:self.submitRole[before]={'submitT':int(t),'submitRole':role,'side':side,'preUp':float(self.inv['UP']),'preDown':float(self.inv['DOWN'])}
        return ok
    def process(self,t):
        for o in self.orders.values():
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
            if inc>EPS:
                n=int(o['n'])
                if n not in self.firstFillSeen:
                    fillRole=self.role_now(o['side']);sm=self.submitRole.get(n,{'submitT':int(o.get('placed',t)),'submitRole':'UNKNOWN','side':o['side']})
                    self.material.append({'n':n,'side':o['side'],'submitT':int(sm['submitT']),'fillT':int(t),'ageMs':int(t)-int(sm['submitT']),'submitRole':sm['submitRole'],'fillRole':fillRole,'flipped':sm['submitRole']!=fillRole and sm['submitRole']!='UNKNOWN','preFillUp':float(self.inv['UP']),'preFillDown':float(self.inv['DOWN'])})
                    self.firstFillSeen.add(n)
                self.record_fill(t,o['side'],inc,v1.fill_price(o['side'],s,o['price']));self.fills+=1;o['cum']=cum
            o['status']=s.get('status')
        for side in ('UP','DOWN'):
            for key in list(self.localPending[side]):
                o=self.orders.get(key)
                if o is None:self.localPending[side].pop(key,None);continue
                s=self.snap(o)
                if s.get('status') is not None:self.localPending[side].pop(key,None)

def summarize(rows):
    if not rows:return {'n':0}
    c=Counter((r['submitRole'],r['fillRole']) for r in rows);fl=[r for r in rows if r['flipped']]
    def age(z):return {'median':float(statistics.median([x['ageMs'] for x in z])),'p75':float(np.quantile([x['ageMs'] for x in z],.75)),'p90':float(np.quantile([x['ageMs'] for x in z],.9))} if z else None
    return {'n':len(rows),'flipRate':len(fl)/len(rows),'transitions':{f'{a}->{b}':n for (a,b),n in c.items()},'allAge':age(rows),'flipAge':age(fl)}

def burst_stats(markets):
    pairs=[]
    for m in markets:
        a=sorted(m['material'],key=lambda z:(z['fillT'],z['n']))
        for x,y in zip(a,a[1:]):
            if x['fillRole']=='EXPAND' and y['fillRole']=='EXPAND' and y['fillT']==x['fillT']:
                pairs.append({'marketId':m['marketId'],'first':x,'second':y})
    if not pairs:return {'n':0}
    return {'n':len(pairs),'secondOriginallyRepairRate':sum(p['second']['submitRole']=='REPAIR' for p in pairs)/len(pairs),'secondRoleFlipRate':sum(p['second']['flipped'] for p in pairs)/len(pairs),'secondAgeMedian':float(statistics.median([p['second']['ageMs'] for p in pairs])),'examples':pairs[:20]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_carrier_role_drift_v13_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);iso,teacher=pa.build_iso(a.target_db);test=[r for r in cohort if r['split']!='TRAIN40'];markets=[];allrows=[]
        for i,cr in enumerate(test,1):
            sim=DriftSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:sim.run_student_progress(models,cr['winner']);mat=sim.material
            finally:sim.close()
            markets.append({'marketId':int(cr['marketId']),'material':mat});allrows.extend(mat)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'materializedCarriers':len(allrows)}),flush=True)
        out={'version':'ETH_CARRIER_SEMANTIC_ROLE_DRIFT_V13','researchOnly':True,'controllerMutation':False,'boundary':['Fresh101 development-only','Same Target Repair Progress Authority controller; no strategy mutation','Each carrier labeled at accepted submit and again immediately before first material fill using authoritative filled inventory','Measures semantic responsibility drift while resting/in-flight; Target numeric policy is not copied'],'summary':summarize(allrows),'sameTickExpandBurst':burst_stats(markets),'markets':markets,'targetProgressTeacher':teacher,'round1Offline':off1,'round2Offline':off2}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'sameTickExpandBurst':{k:v for k,v in out['sameTickExpandBurst'].items() if k!='examples'}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
