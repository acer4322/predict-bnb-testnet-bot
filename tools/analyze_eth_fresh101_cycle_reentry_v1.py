from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,math,statistics
from pathlib import Path
from collections import Counter
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp


def weak_side(inv):
    u=float(inv['UP']); d=float(inv['DOWN'])
    if u<d-v1.EPS:return 'UP'
    if d<u-v1.EPS:return 'DOWN'
    return None

def metrics(sim):
    u=float(sim.inv['UP']); d=float(sim.inv['DOWN']); g=u+d; base=min(u,d)
    return {
        'up':u,'down':d,'cost':float(sim.cost),'absNet':abs(u-d),
        'pairCoverage':2*base/g if g>v1.EPS else 0.0,'floor':base-float(sim.cost),
        'weakSide':weak_side(sim.inv),
        'reservedUp':float(sim.reserved_authoritative('UP')),
        'reservedDown':float(sim.reserved_authoritative('DOWN')),
    }

class ReentryDiagSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,target_traj):
        super().__init__(tape,mode,models)
        self.traj=sorted(target_traj or [],key=lambda r:int(r['t']));self.ti=0;self.target={'UP':0.0,'DOWN':0.0}
        self.boundary=None;self.boundaries=[];self.actions=[];self._before=None
    def oracle_action_authoritative(self,qv):
        ds=[]
        for side in ('UP','DOWN'):
            d=max(0.0,float(self.target[side])-float(self.inv[side])-float(self.reserved_authoritative(side)))
            ds.append((d,side))
        for d,side in sorted(ds,reverse=True):
            if d<=.25:continue
            p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9;qty=d
            if qty<legal:
                if qty<.5*legal:continue
                qty=legal
            qty=min(qty,12.0)
            if self.econ_ok(side,p,qty):return 1,side,qty
        return 0,'UP',0.0
    def process(self,t):
        pre=metrics(self)
        super().process(t)
        post=metrics(self)
        du=post['up']-pre['up'];dd=post['down']-pre['down']
        fill=du>v1.EPS or dd>v1.EPS
        release_up=pre['reservedUp']>v1.EPS and post['reservedUp']<=v1.EPS
        release_down=pre['reservedDown']>v1.EPS and post['reservedDown']<=v1.EPS
        flip=(pre['weakSide']!=post['weakSide'] and pre['weakSide'] is not None and post['weakSide'] is not None)
        if fill or release_up or release_down or flip:
            causes=[]
            if fill:causes.append('FILL_PROGRESS')
            if release_up:causes.append('UP_CARRIER_RELEASE')
            if release_down:causes.append('DOWN_CARRIER_RELEASE')
            if flip:causes.append('WEAK_SIDE_FLIP')
            ev={'boundaryStartT':int(t),'lastBoundaryT':int(t),'causes':causes,'pre':pre,'post':post,
                'deltaUp':du,'deltaDown':dd,'deltaAbsNet':post['absNet']-pre['absNet'],
                'deltaPairCoverage':post['pairCoverage']-pre['pairCoverage'],'deltaFloor':post['floor']-pre['floor'],
                'studentFirstAccepted':None}
            if self.boundary is None:
                self.boundary=ev
            else:
                self.boundary['lastBoundaryT']=int(t);self.boundary['causes']+=causes;self.boundary['post']=post
                self.boundary['deltaUp']+=du;self.boundary['deltaDown']+=dd
                self.boundary['deltaAbsNet']=post['absNet']-self.boundary['pre']['absNet']
                self.boundary['deltaPairCoverage']=post['pairCoverage']-self.boundary['pre']['pairCoverage']
                self.boundary['deltaFloor']=post['floor']-self.boundary['pre']['floor']
    def finish_boundary_on_action(self,rec):
        if self.boundary is None:return
        b=self.boundary;b['studentFirstAccepted']=rec.copy();b['reentryDelayMs']=int(rec['t'])-int(b['lastBoundaryT'])
        self.boundaries.append(b);self.boundary=None
    def run_diag(self,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);self.advance_target(t);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            arr=self.features(t,qv,ca,end);xx=arr.reshape(1,-1);pa=float(self.models['action'].predict_proba(xx)[0,1])
            if pa<self.models['actionTh']:continue
            ps=float(self.models['side'].predict_proba(xx)[0,1]);side='UP' if ps>=self.models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(self.models['qty'].predict(xx)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            oa,oside,oqty=self.oracle_action_authoritative(qv)
            pre=metrics(self);accepted=bool(self.submit(t,side,p,qty));
            if not accepted:continue
            rec={'t':int(t),'secondsLeft':(end-t)/1000.,'side':side,'qty':qty,'price':p,'oracleAct':int(oa),'oracleSide':oside if oa else None,
                 'oracleQty':float(oqty),'oracleSideMatch':bool(oa and oside==side),'studentWhenOracleHold':bool(not oa),
                 'state':pre,'pa':pa,'psUp':ps}
            rec['isReentry']=self.boundary is not None
            self.actions.append(rec);self.finish_boundary_on_action(rec)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        if self.boundary is not None:self.boundaries.append(self.boundary);self.boundary=None
        pnl=float(self.inv.get(str(winner).upper(),0.0)-self.cost);opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';opp_pnl=float(self.inv[opp]-self.cost)
        return {'pnl':pnl,'oppositePnl':opp_pnl,'buyNotional':float(self.cost),'up':float(self.inv['UP']),'down':float(self.inv['DOWN']),
                'actions':self.actions,'boundaries':self.boundaries,'submits':int(self.submits),'fills':int(self.fills)}

def action_stats(actions):
    if not actions:return {'n':0}
    oa=[x for x in actions if x['oracleAct']]
    return {'n':len(actions),'oracleActAtStudentRate':len(oa)/len(actions),
            'oracleSideMatchRateGivenAct':sum(x['oracleSideMatch'] for x in oa)/len(oa) if oa else None,
            'studentWhenOracleHoldRate':sum(x['studentWhenOracleHold'] for x in actions)/len(actions),
            'meanAbsNet':statistics.mean(x['state']['absNet'] for x in actions),
            'meanPairCoverage':statistics.mean(x['state']['pairCoverage'] for x in actions)}

def boundary_stats(bs):
    if not bs:return {'n':0}
    witha=[b for b in bs if b.get('studentFirstAccepted')]
    return {'n':len(bs),'withStudentReentry':len(witha),'reentryRate':len(witha)/len(bs),
            'meanReentryDelayMs':statistics.mean(b['reentryDelayMs'] for b in witha) if witha else None,
            'meanDeltaAbsNet':statistics.mean(b['deltaAbsNet'] for b in bs),'meanDeltaPairCoverage':statistics.mean(b['deltaPairCoverage'] for b in bs),
            'meanDeltaFloor':statistics.mean(b['deltaFloor'] for b in bs),'causeCounts':dict(Counter(c for b in bs for c in b['causes']))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-traj',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_f101_reentry_diag_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];base_traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,base_traj)
        td=json.load(open(a.target_traj,encoding='utf-8'))['trajectory'];test=[r for r in cohort if r['split']!='TRAIN40' and str(r['marketId']) in td];rows=[]
        for i,cr in enumerate(test,1):
            sim=ReentryDiagSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,td[str(cr['marketId'])])
            try:r=sim.run_diag(cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'index':next(j+1 for j,z in enumerate([x for x in cohort if x['split']!='TRAIN40']) if int(z['marketId'])==int(cr['marketId']))});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'actions':sum(len(x['actions']) for x in rows),'boundaries':sum(len(x['boundaries']) for x in rows)}),flush=True)
        def group(name,lo,hi):
            rr=[r for r in rows if lo<=r['index']<=hi];aa=[x for r in rr for x in r['actions']];re=[x for x in aa if x['isReentry']];ordinary=[x for x in aa if not x['isReentry']];bb=[b for r in rr for b in r['boundaries']]
            return {'name':name,'markets':len(rr),'pnl':sum(r['pnl'] for r in rr),'wins':sum(r['pnl']>0 for r in rr),'winRate':sum(r['pnl']>0 for r in rr)/len(rr) if rr else None,
                    'allActions':action_stats(aa),'reentryActions':action_stats(re),'ordinaryActions':action_stats(ordinary),'boundaries':boundary_stats(bb)}
        groups=[group('EARLY_1_20',1,20),group('MID_21_40',21,40),group('MID_41_60',41,60),group('MID_61_80',61,80),group('LATE_81_100',81,100),group('ALL_SUPPORTED',1,101)]
        out={'version':'ETH_FRESH101_CYCLE_REENTRY_DIAGNOSTIC_V1','posthocDiagnosticOnly':True,'noStrategyChange':True,
             'boundary':['Fresh101 already consumed before this diagnosis','Target Maker strict-past trajectory used only as offline re-entry teacher/diagnostic','student runtime remains frozen Local Pending Reservation + BOOK_IMBALANCE','cycle boundary = own fill progress, authoritative carrier release, or weak-side flip; consecutive transitions merge until next accepted student responsibility','no winner/outcome enters decisions'],
             'round1Offline':off1,'round2Offline':off2,'supportedMarkets':len(rows),'groups':groups,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'supportedMarkets':len(rows),'groups':groups},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
