from __future__ import annotations
import argparse,copy,hashlib,json,math,os,tempfile,zipfile
from pathlib import Path
try:
    from tools import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl
except ImportError:
    import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl

EPS=cl.EPS
BRANCHES=('NATIVE','EPOCH_REPAIR_BIAS','EPOCH_REEXPAND_BIAS')

def norm(x):
    if isinstance(x,dict): return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)): return [norm(v) for v in x]
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    try:return float(x)
    except:return str(x)

def dig(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def delta(a,b):
    ks=('floor','best','favoredPayoff','weakPayoff','upPayoff','downPayoff','targetRepairDebt','liveSlots','fills','submits','alternations')
    return {k:float(b[k])-float(a[k]) for k in ks}

class EpochBiasFork(cl.ClosedLoopManager):
    def __init__(self,tape,spec,branch):
        super().__init__(tape,'NATIVE');self.spec=spec;self.branch=branch;self.seen=False;self.prefix=None;self.prefixDigest=None
        self.prefixState=None;self.epochActive=False;self.epochStartT=None;self.epochEnd=None;self.managed=[];self.forceFailures=0
        self.startWeak=str(spec['weakSide']);self.startExpand=str(spec['expandSide']);self.startDebt=None
        self.startResponsibilityIds=[];self.startResponsibilityInitialRemaining={};self.invalidStartReason=None
    def _state_vec(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);pu=u-c;pd=d-c;pay={'UP':pu,'DOWN':pd}
        return {'t':int(t),'upPayoff':pu,'downPayoff':pd,'floor':min(pu,pd),'best':max(pu,pd),
                'favoredPayoff':float(pay[self.startExpand]),'weakPayoff':float(pay[self.startWeak]),
                'targetRepairDebt':float(self._aggregate_for_repair_side(self.startWeak)),
                'frozenResponsibilityRemaining':float(self._frozen_remaining()) if self.startResponsibilityIds else 0.0,'liveSlots':len(self.slot_key),
                'fills':int(getattr(self,'fills',0)),'submits':int(self.submits),
                'alternations':int(len([1 for i in range(1,len(self.fill_side_sequence)) if self.fill_side_sequence[i]['side']!=self.fill_side_sequence[i-1]['side']]))}
    def _frozen_rows(self):
        byid={int(x.get('id')):x for x in self.resp_all if x.get('id') is not None}
        return [byid.get(int(i)) for i in self.startResponsibilityIds]
    def _frozen_remaining(self):
        rows=self._frozen_rows();return sum(float(x.get('remainingQty') or 0.0) for x in rows if x is not None)
    def _snapshot(self,t,qv):
        live={}
        for k,o in self.orders.items():
            if k in set(self.slot_key.values()) or cl.base.v2.base.live(str(o.get('status') or '')):
                live[k]={z:o.get(z) for z in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        lots=self.serializable_lots()
        frozen=[x for x in lots if str(x.get('repairSide'))==self.startWeak and float(x.get('remainingQty') or 0.0)>EPS]
        self.startResponsibilityIds=sorted(int(x['id']) for x in frozen)
        self.startResponsibilityInitialRemaining={str(int(x['id'])):float(x.get('remainingQty') or 0.0) for x in frozen}
        if not self.startResponsibilityIds:self.invalidStartReason='NO_START_RESPONSIBILITY_FOR_WEAK_SIDE'
        x={'t':int(t),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),
           'liveOrders':live,'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),
           'responsibilities':lots,'frozenResponsibilityIds':list(self.startResponsibilityIds),
           'frozenResponsibilityInitialRemaining':dict(self.startResponsibilityInitialRemaining),'quotes':norm(qv)}
        self.prefix=norm(x);self.prefixDigest=dig(self.prefix);self.prefixState=self._state_vec(t);self.startDebt=float(self.prefixState['targetRepairDebt'])
    def _end_reason(self):
        if self.invalidStartReason:return 'INVALID_START_'+self.invalidStartReason
        rows=self._frozen_rows()
        if any(x is None for x in rows):return 'INVALID_FROZEN_RESPONSIBILITY_MISSING'
        if rows and all(float(x.get('remainingQty') or 0.0)<=EPS or x.get('completedAt') is not None for x in rows):return 'START_RESPONSIBILITIES_COMPLETE'
        if self.q_pending_active is not None:return 'PENDING_ACTIVE_AUTHORITY'
        st,_,weak=self._state()
        if st!='TWO_SIDED':return 'LEFT_TWO_SIDED'
        if str(weak)!=self.startWeak:return 'WEAK_SIDE_CHANGED'
        return None
    def _close_epoch(self,t,reason):
        if self.epochActive:
            self.epochEnd={'t':int(t),'reason':str(reason),'state':self._state_vec(t),'managedActions':len(self.managed),'forceFailures':int(self.forceFailures),'frozenResponsibilityIds':list(self.startResponsibilityIds),'frozenInitialRemaining':dict(self.startResponsibilityInitialRemaining),'frozenCurrentRemaining':float(self._frozen_remaining()) if self.startResponsibilityIds else None}
            self.epochActive=False
    def _open_one_option(self,t,qv,end):
        target=int(self.spec['t'])
        if not self.seen and int(t)==target:
            self.seen=True;self._snapshot(t,qv);self.epochActive=True;self.epochStartT=int(t)
            if self.branch=='NATIVE':return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
            kind='REPAIR' if self.branch=='EPOCH_REPAIR_BIAS' else 'REEXPAND'
            ok,side,role,cand=self._force_one(t,qv,kind)
            self.managed.append({'t':int(t),'kind':kind,'ok':bool(ok),'side':side,'role':role,'candidate':cand,'stateBefore':self.prefixState})
            if ok:return None
            self.forceFailures+=1;return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        if not self.seen or not self.epochActive:return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        r=self._end_reason()
        if r is not None:
            self._close_epoch(t,r);return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        if self.branch=='NATIVE':return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        e=self._eligible(t,qv,end)
        if e is not None:
            kind='REPAIR' if self.branch=='EPOCH_REPAIR_BIAS' else 'REEXPAND'
            before=self._state_vec(t);ok,side,role,cand=self._force_one(t,qv,kind)
            self.managed.append({'t':int(t),'kind':kind,'ok':bool(ok),'side':side,'role':role,'candidate':cand,'stateBefore':before})
            if ok:return None
            self.forceFailures+=1
        return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
    def run_epoch(self):
        r=super().run_qty('__UNSCORED__')
        if self.epochActive:self._close_epoch(int(r.get('lastT') or self.epochStartT or self.spec['t']),'MARKET_END')
        return r

def terminal(r,winner):return cl.metrics(r,winner)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    sp=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=list(sp['states'])[:4];rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_epoch_bias_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();br={}
            for b in BRANCHES:
                sim=EpochBiasFork(tape,s,b)
                try:r=sim.run_epoch()
                finally:sim.close()
                term=terminal(r,winner);end=sim.epochEnd
                br[b]={'triggered':sim.seen,'validStart':sim.invalidStartReason is None,'invalidStartReason':sim.invalidStartReason,'startResponsibilityIds':list(sim.startResponsibilityIds),'startResponsibilityInitialRemaining':dict(sim.startResponsibilityInitialRemaining),'prefixDigest':sim.prefixDigest,'prefixState':sim.prefixState,'epochEnd':end,
                       'boundaryDelta':None if not end else delta(sim.prefixState,end['state']),'managedEvents':sim.managed,
                       'terminal':term,'terminalFromPrefix':None if sim.prefixState is None else {
                         'floor':float(term['floor'])-float(sim.prefixState['floor']),'best':float(term['best'])-float(sim.prefixState['best']),
                         'favoredPayoff':(float(r['upQty'])-float(r['buyNotional']) if s['expandSide']=='UP' else float(r['downQty'])-float(r['buyNotional']))-float(sim.prefixState['favoredPayoff']),
                         'weakPayoff':(float(r['upQty'])-float(r['buyNotional']) if s['weakSide']=='UP' else float(r['downQty'])-float(r['buyNotional']))-float(sim.prefixState['weakPayoff'])}}
            digs={x['prefixDigest'] for x in br.values()};checks={'allTriggered':all(x['triggered'] for x in br.values()),'allStartsValid':all(x['validStart'] and len(x['startResponsibilityIds'])>0 for x in br.values()),'prefixParity':len(digs)==1 and None not in digs,
                'allEpochBoundariesObserved':all(x['epochEnd'] is not None for x in br.values()),'allLedgerClean':all(not x['terminal']['ledgerViolations'] for x in br.values()),'allMax4':all(x['terminal']['maxSlots']<=4 for x in br.values())}
            row={'marketId':mid,'t':int(s['t']),'stratum':s.get('h4ResearchStratum'),'checks':checks,'correctnessPass':all(checks.values()),'branches':br};rows.append(row)
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'correct':row['correctnessPass'],'end':{b:br[b]['epochEnd']['reason'] if br[b]['epochEnd'] else None for b in BRANCHES},'managed':{b:len(br[b]['managedEvents']) for b in BRANCHES}},ensure_ascii=False),flush=True)
    def cmp(branch,metric,where='boundaryDelta'):
        vals=[]
        for r in rows:
            x=r['branches'][branch].get(where);n=r['branches']['NATIVE'].get(where)
            if x is None or n is None:continue
            vals.append(float(x[metric])-float(n[metric]))
        return {'values':vals,'sum':sum(vals),'positive':sum(v>1e-9 for v in vals),'negative':sum(v<-1e-9 for v in vals),'zero':sum(abs(v)<=1e-9 for v in vals)}
    agg={'seams':len(rows),'correct':sum(r['correctnessPass'] for r in rows)}
    for b in ('EPOCH_REPAIR_BIAS','EPOCH_REEXPAND_BIAS'):
        agg[b]={m:cmp(b,m) for m in ('floor','best','favoredPayoff','weakPayoff')}
    out={'version':'MANAGEMENT_MAINLINE_RESPONSIBILITY_EPOCH_BIAS_SMOKE4_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'aggregate':agg,
         'boundary':['current V3B exact-HFT/exact FIFO','four outcome-blind H4 seams reused','epoch is structural not time-based','no Target/winner in decisions','winner terminal scoring posthoc only','no NEW24-B','no 8781','no dream fill']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
