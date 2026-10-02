from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,math
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
EPS=1e-9

def inv_state(sim):
    u=float(sim.inv['UP']); d=float(sim.inv['DOWN']); gross=u+d; base=min(u,d)
    weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
    return {'up':u,'down':d,'gross':gross,'absNet':abs(u-d),'pairCoverage':2*base/gross if gross>EPS else 0.0,
            'floor':base-float(sim.cost),'bestPnl':max(u,d)-float(sim.cost),'weakSide':weak,'buyNotional':float(sim.cost)}

def unmatched_qty(sim,side):
    return float(sum(float(a) for a,_ in sim.un[side]))

def avg_cost(sim,side):
    q=float(sim.inv[side]); return float(sim.sideCost[side])/q if q>EPS else None

class TraceSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.traceActions=[]; self.traceFills=[]; self._traceFeature=None
    def features(self,t,qv,ca,end):
        arr=super().features(t,qv,ca,end)
        self._traceFeature={'t':int(t),'end':int(end),'secondsLeft':(int(end)-int(t))/1000.0,
                            'quote':{'UP':dict(qv['UP']),'DOWN':dict(qv['DOWN']),'spread':float(qv['spread']),'bidDepth':float(qv['bd']),'askDepth':float(qv['ad']),'top3Bid':float(qv['tb']),'top3Ask':float(qv['ta']),'bookImbalance':float(qv['imb'])},
                            'updateFlow':{k:float(v) for k,v in ca.items()},
                            'featureMap':{k:float(arr[i]) for i,k in enumerate(v1.FEATURES)}}
        return arr
    def submit(self,t,side,p,q):
        s=inv_state(self); pre_n=int(self.n); weak=s['weakSide']; role='REPAIR' if weak is not None and side==weak else 'EXPAND'
        venue_up=float(self.reserved('UP')); venue_dn=float(self.reserved('DOWN'))
        local_up=float(self.local_reserved('UP')); local_dn=float(self.local_reserved('DOWN'))
        rec={'attemptOrdinal':len(self.traceActions)+1,'t':int(t),'side':side,'price':float(p),'qty':float(q),'preNextOrderN':pre_n,'roleAtAttempt':role,
             'inventory':s,'venueReserved':{'UP':venue_up,'DOWN':venue_dn},'localPending':{'UP':local_up,'DOWN':local_dn},
             'authoritativeReserved':{'UP':venue_up+local_up,'DOWN':venue_dn+local_dn},
             'unmatched':{'UP':unmatched_qty(self,'UP'),'DOWN':unmatched_qty(self,'DOWN')},
             'avgCost':{'UP':avg_cost(self,'UP'),'DOWN':avg_cost(self,'DOWN')},'pairedQty':float(self.pairedQty),'pairReserve':float(self.pairReserve),
             'duplicateBlockedBefore':int(self.duplicateBlocked),'localPendingBlockedBefore':int(self.localPendingBlocked),'strictPastFeature':self._traceFeature}
        ok=bool(super().submit(t,side,p,q)); rec['accepted']=ok;rec['nativeOrderN']=pre_n if ok else None
        rec['duplicateBlockedAfter']=int(self.duplicateBlocked);rec['localPendingBlockedAfter']=int(self.localPendingBlocked)
        self.traceActions.append(rec);return ok
    def record_fill(self,t,side,q,p):
        pre=inv_state(self); before_lp={'UP':float(self.local_reserved('UP')),'DOWN':float(self.local_reserved('DOWN'))}
        super().record_fill(t,side,q,p); post=inv_state(self)
        self.traceFills.append({'fillOrdinal':len(self.traceFills)+1,'t':int(t),'side':side,'qty':float(q),'price':float(p),'preInventory':pre,'postInventory':post,
                                'localPendingBeforeProcessReconcile':before_lp,'unmatchedAfter':{'UP':unmatched_qty(self,'UP'),'DOWN':unmatched_qty(self,'DOWN')},
                                'pairedQtyAfter':float(self.pairedQty),'pairReserveAfter':float(self.pairReserve)})
    def materialized_trace(self):
        byn={int(x['nativeOrderN']):x for x in self.traceActions if x.get('accepted') and x.get('nativeOrderN') is not None}
        acts=[]
        for o in self.orders.values():
            n=int(o['n']);cum=float(o.get('cum') or 0.0)
            if cum>EPS and n in byn:
                x=byn[n]
                acts.append({'n':n,'placed':int(o['placed']),'side':o['side'],'price':float(o['price']),'qty':float(o['qty']),'cum':cum,'roleAtAttempt':x['roleAtAttempt'],'preState':x})
        acts.sort(key=lambda z:(z['placed'],z['n']))
        episodes=[]; active=False; repairs=[]; last_expand=None
        for a in acts:
            if a['roleAtAttempt']=='EXPAND':
                if active and last_expand is not None:
                    episodes.append({'previousExpandN':last_expand['n'],'reexpandN':a['n'],'previousExpandPlaced':last_expand['placed'],'reexpandPlaced':a['placed'],
                                     'elapsedMs':a['placed']-last_expand['placed'],'materializedRepairCount':len(repairs),
                                     'materializedRepairNs':[z['n'] for z in repairs],'reexpandPreState':a['preState']})
                active=True; repairs=[]; last_expand=a
            elif active: repairs.append(a)
        return acts,episodes

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    ref=json.load(open(a.reference,encoding='utf-8')); refrows={int(r['marketId']):r for r in ref['rows']}
    tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_fresh101_trace_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r.get('split')!='TRAIN40'];rows=[];markets=[];all_parity=True
        if off1!=ref.get('round1Offline') or off2!=ref.get('round2Offline'):raise RuntimeError('offline model metrics drift from frozen Fresh101 reference')
        for i,cr in enumerate(test,1):
            mid=int(cr['marketId']);sim=TraceSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models)
            try:r=sim.run_student(models,cr['winner']); mats,eps=sim.materialized_trace()
            finally:sim.close()
            r.update({'marketId':mid,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy'],'duplicateBlocked':sim.duplicateBlocked,'localPendingBlocked':sim.localPendingBlocked});opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional']
            parity=(r==refrows[mid]);all_parity=all_parity and parity
            if not parity:
                diffs={k:{'trace':r.get(k),'reference':refrows[mid].get(k)} for k in sorted(set(r)|set(refrows[mid])) if r.get(k)!=refrows[mid].get(k)}
                raise RuntimeError(f'terminal parity fail market {mid}: {json.dumps(diffs)[:2000]}')
            rows.append(r);markets.append({'marketId':mid,'terminalParity':parity,'terminal':r,'actionAttempts':sim.traceActions,'fillEvents':sim.traceFills,
                                           'materializedActions':mats,'reexpandEpisodes':eps,'acceptedAttempts':sum(x['accepted'] for x in sim.traceActions),
                                           'blockedAttempts':sum(not x['accepted'] for x in sim.traceActions)})
            if i%10==0 or i==len(test):print(json.dumps({'traceProgress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'actionsLogged':sum(len(x['actionAttempts']) for x in markets),'fillsLogged':sum(len(x['fillEvents']) for x in markets),'episodes':sum(len(x['reexpandEpisodes']) for x in markets)}),flush=True)
        summary=lp.agg(rows);out={'version':'ETH_DAGGER_FRESH101_LOCAL_PENDING_EXTREME_TRACE_V1','researchOnly':True,'runtimeAuthority':False,
            'boundary':['Exact frozen Local Pending Reservation V1 policy; observer only','Fresh101 already consumed development evidence','All terminal rows must equal frozen Fresh101 reference exactly','Strict-past feature/state captured only at policy submit attempts; winner/Target outcome never added to runtime input','Materialized action/re-expand episode annotations are post-replay diagnostics'],
            'round1Offline':off1,'round2Offline':off2,'terminalParityAll101':all_parity,'summary':summary,'markets':markets}
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'terminalParityAll101':all_parity,'summary':summary,'actionsLogged':sum(len(x['actionAttempts']) for x in markets),'fillsLogged':sum(len(x['fillEvents']) for x in markets),'reexpandEpisodes':sum(len(x['reexpandEpisodes']) for x in markets)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
