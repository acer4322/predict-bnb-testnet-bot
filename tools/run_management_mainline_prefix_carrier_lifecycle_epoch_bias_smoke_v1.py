from __future__ import annotations
import argparse,copy,hashlib,json,os,tempfile,zipfile
from pathlib import Path
try:
    from tools import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl
except ImportError:
    import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl

EPS=cl.EPS
BRANCHES=('NATIVE','LIFECYCLE_REPAIR_BIAS','LIFECYCLE_REEXPAND_BIAS')

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    try:return float(x)
    except:return str(x)

def digest(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def delta(a,b):
    ks=('floor','best','favoredPayoff','weakPayoff','upPayoff','downPayoff','targetRepairDebt','liveSlots','fills','submits','alternations')
    return {k:float(b[k])-float(a[k]) for k in ks}

class LifecycleEpochFork(cl.ClosedLoopManager):
    def __init__(self,tape,spec,branch):
        super().__init__(tape,'NATIVE');self.spec=spec;self.branch=str(branch);self.seen=False
        self.startWeak=str(spec['weakSide']);self.startExpand=str(spec['expandSide'])
        self.prefix=None;self.prefixDigest=None;self.prefixState=None;self.episodeActive=False;self.episodeStartT=None;self.episodeEnd=None
        self.prefixCarrierKeys=set();self.managedKeys=set();self.managedEvents=[];self.forceFailures=0
    def _vec(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);pu=u-c;pd=d-c;pay={'UP':pu,'DOWN':pd}
        return {'t':int(t),'upPayoff':pu,'downPayoff':pd,'floor':min(pu,pd),'best':max(pu,pd),
                'favoredPayoff':float(pay[self.startExpand]),'weakPayoff':float(pay[self.startWeak]),
                'targetRepairDebt':float(self._aggregate_for_repair_side(self.startWeak)),'liveSlots':len(self.slot_key),
                'fills':int(getattr(self,'fills',0)),'submits':int(self.submits),
                'alternations':int(len([1 for i in range(1,len(self.fill_side_sequence)) if self.fill_side_sequence[i]['side']!=self.fill_side_sequence[i-1]['side']]))}
    def _snap(self,t,qv):
        self.prefixCarrierKeys=set(self.slot_key.values())
        live={}
        for k,o in self.orders.items():
            if k in self.prefixCarrierKeys or cl.base.v2.base.live(str(o.get('status') or '')):
                live[k]={z:o.get(z) for z in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        x={'t':int(t),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),
           'liveOrders':live,'prefixCarrierKeys':sorted(self.prefixCarrierKeys),'qLadder':copy.deepcopy(self.q_ladder),
           'qPendingActive':copy.deepcopy(self.q_pending_active),'responsibilities':self.serializable_lots(),'quotes':norm(qv)}
        self.prefix=norm(x);self.prefixDigest=digest(self.prefix);self.prefixState=self._vec(t)
    def _tracked(self):return set(self.prefixCarrierKeys)|set(self.managedKeys)
    def _close_episode(self,t,reason,event=None):
        if not self.episodeActive:return
        self.episodeEnd={'t':int(t),'reason':str(reason),'event':norm(event),'state':self._vec(t),
                         'durationMsDiagnostic':int(t)-int(self.episodeStartT),'prefixCarrierKeys':sorted(self.prefixCarrierKeys),
                         'managedKeys':sorted(self.managedKeys),'managedActions':len(self.managedEvents),'forceFailures':int(self.forceFailures)}
        self.episodeActive=False
    def _state_boundary(self,t):
        if self.q_pending_active is not None:return ('PENDING_ACTIVE_AUTHORITY',None)
        st,_,weak=self._state()
        if st!='TWO_SIDED':return ('LEFT_TWO_SIDED',None)
        if str(weak)!=self.startWeak:return ('WEAK_SIDE_CHANGED',None)
        return (None,None)
    def process(self,t):
        before=len(self.fill_accounting)
        super().process(t)
        if not self.episodeActive:return
        hits=[x for x in self.fill_accounting[before:] if str(x.get('key')) in self._tracked() and float(x.get('confirmedQty') or 0.0)>EPS]
        if hits:
            self._close_episode(t,'TRACKED_CARRIER_CONFIRMED_FILL',{'fills':hits});return
        r,_=self._state_boundary(t)
        if r:self._close_episode(t,r)
    def _refresh_slots(self,t):
        before=len(self.slot_history)
        super()._refresh_slots(t)
        if not self.episodeActive:return
        ev=[x for x in self.slot_history[before:] if x.get('event')=='SLOT_RELEASE' and str(x.get('key')) in self._tracked()]
        if ev:self._close_episode(t,'TRACKED_CARRIER_SLOT_RELEASE',{'events':ev})
    def _force_and_track(self,t,qv,kind):
        before=set(self.orders.keys());ok,side,role,cand=self._force_one(t,qv,kind);new=sorted(set(self.orders.keys())-before)
        self.managedKeys.update(new)
        self.managedEvents.append({'t':int(t),'kind':kind,'ok':bool(ok),'side':side,'role':role,'candidate':cand,'newKeys':new,'stateBefore':self._vec(t)})
        if not ok:self.forceFailures+=1
        return bool(ok)
    def _native_and_track(self,t,qv,end):
        before=set(self.orders.keys());out=cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end);new=sorted(set(self.orders.keys())-before);self.managedKeys.update(new);return out
    def _open_one_option(self,t,qv,end):
        target=int(self.spec['t'])
        if not self.seen and int(t)==target:
            self.seen=True;self._snap(t,qv);self.episodeActive=True;self.episodeStartT=int(t)
            if self.branch=='NATIVE':return self._native_and_track(t,qv,end)
            kind='REPAIR' if self.branch=='LIFECYCLE_REPAIR_BIAS' else 'REEXPAND'
            if self._force_and_track(t,qv,kind):return None
            return self._native_and_track(t,qv,end)
        if not self.seen or not self.episodeActive:return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        r,_=self._state_boundary(t)
        if r:
            self._close_episode(t,r);return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        if self.branch=='NATIVE':return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        e=self._eligible(t,qv,end)
        if e is not None:
            kind='REPAIR' if self.branch=='LIFECYCLE_REPAIR_BIAS' else 'REEXPAND'
            if self._force_and_track(t,qv,kind):return None
        return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
    def run_episode(self):
        r=super().run_qty('__UNSCORED__')
        if self.episodeActive:self._close_episode(int(r.get('lastT') or self.episodeStartT or self.spec['t']),'MARKET_END')
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    specs=list(json.loads(Path(a.specs).read_text(encoding='utf-8'))['states'])[:4];rows=[]
    with tempfile.TemporaryDirectory(prefix='prefix_lifecycle_epoch_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);winner=str(co[mid]['winner']).upper();tape=root/'tapes'/f'{mid}.json.xz';br={}
            for b in BRANCHES:
                sim=LifecycleEpochFork(tape,s,b)
                try:r=sim.run_episode()
                finally:sim.close()
                term=cl.metrics(r,winner);end=sim.episodeEnd
                br[b]={'triggered':sim.seen,'prefixDigest':sim.prefixDigest,'prefixState':sim.prefixState,'prefixCarrierKeys':sorted(sim.prefixCarrierKeys),
                       'managedKeys':sorted(sim.managedKeys),'episodeEnd':end,'boundaryDelta':None if end is None else delta(sim.prefixState,end['state']),
                       'managedEvents':sim.managedEvents,'terminal':term}
            digs={x['prefixDigest'] for x in br.values()};checks={'allTriggered':all(x['triggered'] for x in br.values()),'prefixParity':len(digs)==1 and None not in digs,
                'allBoundariesObserved':all(x['episodeEnd'] is not None for x in br.values()),'allLedgerClean':all(not x['terminal']['ledgerViolations'] for x in br.values()),'allMax4':all(x['terminal']['maxSlots']<=4 for x in br.values())}
            row={'marketId':mid,'t':int(s['t']),'stratum':s.get('h4ResearchStratum'),'checks':checks,'correctnessPass':all(checks.values()),'branches':br};rows.append(row)
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'correct':row['correctnessPass'],'end':{b:br[b]['episodeEnd']['reason'] for b in BRANCHES},'durMs':{b:br[b]['episodeEnd']['durationMsDiagnostic'] for b in BRANCHES},'managed':{b:len(br[b]['managedEvents']) for b in BRANCHES}},ensure_ascii=False),flush=True)
    def contrast(branch,metric):
        vals=[]
        for r in rows:
            x=r['branches'][branch]['boundaryDelta'];n=r['branches']['NATIVE']['boundaryDelta'];vals.append(float(x[metric])-float(n[metric]))
        return {'values':vals,'sum':sum(vals),'positive':sum(v>1e-9 for v in vals),'negative':sum(v<-1e-9 for v in vals),'zero':sum(abs(v)<=1e-9 for v in vals)}
    agg={'seams':len(rows),'correct':sum(r['correctnessPass'] for r in rows)}
    for b in ('LIFECYCLE_REPAIR_BIAS','LIFECYCLE_REEXPAND_BIAS'):agg[b]={m:contrast(b,m) for m in ('floor','best','favoredPayoff','weakPayoff')}
    out={'version':'MANAGEMENT_MAINLINE_PREFIX_CARRIER_LIFECYCLE_EPOCH_BIAS_SMOKE4_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'aggregate':agg,
         'boundary':['current V3B exact-HFT/exact FIFO','outcome-blind H4 seams','carrier-lifecycle structural horizon; duration diagnostic only','no fixed timer','no Target/winner in decisions','winner terminal scoring posthoc only','no NEW24-B','no 8781','no dream fill']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
