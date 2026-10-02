from __future__ import annotations
import argparse,copy,hashlib,json,os,tempfile,zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
base=v3b.base;EPS=v3b.EPS;H=(500,1000,2000,5000,10000)

def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    try:return float(x)
    except:return str(x)
def dg(x):return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

class Fork(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,spec,branch):
        super().__init__(tape);self.spec=spec;self.branch=branch;self.force_direction=None;self.seen=False;self.prefix=None;self.prefixDigest=None;self.intervention=None;self.timeline=[]
    def _direction(self,qv):
        return str(self.force_direction) if self.force_direction is not None else super()._direction(qv)
    def snap_prefix(self,t,qv):
        live={}
        for k,o in self.orders.items():
            if k in set(self.slot_key.values()) or base.v2.base.live(str(o.get('status') or '')):
                live[k]={z:o.get(z) for z in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        x={'t':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),'liveOrders':live,
           'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),
           'placeHist':list(self.placeHist),'fillSequence':copy.deepcopy(self.fill_side_sequence),'quotes':norm(qv)}
        self.prefix=norm(x);self.prefixDigest=dg(self.prefix)
    def state(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);pu=u-c;pd=d-c;du=float(self._aggregate_for_repair_side('UP'));dd=float(self._aggregate_for_repair_side('DOWN'))
        return {'t':int(t),'upPayoff':pu,'downPayoff':pd,'best':max(pu,pd),'floor':min(pu,pd),'gap':abs(pu-pd),'upQty':u,'downQty':d,'cost':c,'repairDebtUP':du,'repairDebtDOWN':dd,'totalDebt':du+dd,'liveSlots':len(self.slot_key),'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),'pendingActive':self.q_pending_active is not None}
    def process(self,t):
        super().process(t)
        if self.seen and int(t)>=int(self.spec['t']):self.timeline.append(self.state(t))
    def _open_one_option(self,t,qv,end):
        if int(t)==int(self.spec['t']) and not self.seen:
            self.seen=True;self.snap_prefix(t,qv);before=len(self.slot_history);baseline=base.MinimalPairRoleSim._role_decision(self,qv)
            if self.branch=='MARKET_DIRECTION':
                out=super()._open_one_option(t,qv,end);new=self.slot_history[before:];subs=[x for x in new if x.get('event')=='ROLE_SLOT_SUBMIT' and int(x.get('t') or -1)==int(t)]
                ok=any(str(x.get('side'))==str(self.spec['marketProposalSide']) for x in subs)
                self.intervention={'branch':self.branch,'baselineDecision':norm(baseline),'submitOnMarketSide':ok,'newSlotEvents':norm(new)};return out
            if self.branch=='HISTORY_DIRECTION':
                self.force_direction=str(self.spec['historyForcedSide'])
                try:forced=base.MinimalPairRoleSim._role_decision(self,qv);out=super()._open_one_option(t,qv,end)
                finally:self.force_direction=None
                new=self.slot_history[before:];subs=[x for x in new if x.get('event')=='ROLE_SLOT_SUBMIT' and int(x.get('t') or -1)==int(t)]
                ok=any(str(x.get('side'))==str(self.spec['historyForcedSide']) for x in subs)
                self.intervention={'branch':self.branch,'baselineDecision':norm(baseline),'forcedDecision':norm(forced),'submitOnHistorySide':ok,'newSlotEvents':norm(new)};return out
            if self.branch=='HOLD':
                self.intervention={'branch':self.branch,'baselineDecision':norm(baseline),'holdApplied':True,'newSlotEvents':[]};return
            raise RuntimeError(self.branch)
        return super()._open_one_option(t,qv,end)
    def run_branch(self):
        r=super().run_qty('__UNSCORED__');r['branch']=self.branch;r['prefixDigest']=self.prefixDigest;r['intervention']=self.intervention;r['forkTimeline']=self.timeline;return r

def at(r,t,h):
    xs=[x for x in r.get('forkTimeline',[]) if int(x['t'])<=int(t)+int(h)];return xs[-1] if xs else None
def dv(c,x):
    if c is None or x is None:return None
    ks=('upPayoff','downPayoff','best','floor','gap','upQty','downQty','cost','repairDebtUP','repairDebtDOWN','totalDebt');return {k:float(x[k])-float(c[k]) for k in ks}
def tm(r):
    return {'floor':float(r['floor']),'best':float(r['best']),'gap':float(r['best'])-float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),'buyNotional':float(r['buyNotional']),'upQty':float(r['upQty']),'downQty':float(r['downQty']),'activeSubmits':int((r.get('quantityLadderCounters') or {}).get('managedActiveSubmits',0)),'managedRepairQty':float(r.get('quantityManagedRepairQtyDiagnostic') or 0),'ledgerViolations':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},'maxSlots':int(r.get('maxSimultaneousSlots') or 0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=3);a=ap.parse_args();scan=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=(scan.get('smokeCandidates') or [])[:a.max_states]
    specs=[s for s in specs if not bool(s.get('qPendingActive'))]
    if not specs:raise RuntimeError('no smoke candidates after pending-active exclusion')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='hist_dir_fork_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';rr={}
            for b in ('MARKET_DIRECTION','HISTORY_DIRECTION','HOLD'):
                sim=Fork(tape,s,b)
                try:rr[b]=sim.run_branch()
                finally:sim.close()
            digs={b:rr[b].get('prefixDigest') for b in rr};m={b:tm(rr[b]) for b in rr};ctrl=rr['MARKET_DIRECTION'];tt=int(s['t'])
            checks={'prefixParity':len(set(digs.values()))==1 and None not in digs.values(),'marketSubmitExercised':bool((rr['MARKET_DIRECTION'].get('intervention') or {}).get('submitOnMarketSide')),'historySubmitExercised':bool((rr['HISTORY_DIRECTION'].get('intervention') or {}).get('submitOnHistorySide')),'holdApplied':bool((rr['HOLD'].get('intervention') or {}).get('holdApplied')),'allLedgerClean':all(not m[b]['ledgerViolations'] for b in m),'allMax4':all(m[b]['maxSlots']<=4 for b in m)}
            local={str(h):{b:dv(at(ctrl,tt,h),at(rr[b],tt,h)) for b in ('HISTORY_DIRECTION','HOLD')} for h in H}
            term={b:{k:m[b][k]-m['MARKET_DIRECTION'][k] for k in ('floor','best','gap','fills','submits','alternations','buyNotional','activeSubmits','managedRepairQty')} for b in ('HISTORY_DIRECTION','HOLD')}
            row={'marketId':mid,'t':tt,'stateSpec':s,'prefixDigests':digs,'interventions':{b:rr[b].get('intervention') for b in rr},'checks':checks,'localDeltaVsMarketDirection':local,'terminalMetrics':m,'terminalDeltaVsMarketDirection':term,'valid':all(checks.values())};rows.append(row);print(json.dumps({'progress':i,'marketId':mid,'valid':row['valid'],'checks':checks,'terminalDelta':term},ensure_ascii=False),flush=True)
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_VS_MARKET_DIRECTION_THREEWAY_FORK_V2','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'stateCount':len(rows),'allCorrectnessPass':all(r['valid'] for r in rows),'rows':rows,'boundary':['same strict-past V3B prefix all branches','MARKET_DIRECTION = unchanged current V3B','HISTORY_DIRECTION = override only _direction at one target receipt; current V3B chooses role/qty/route/ledger behavior','HOLD = suppress one target open decision only','suffix returns to current V3B','no new responsibility/risk authority minted outside current V3B','local vector outcomes at 0.5/1/2/5/10s','winner/settlement/Target future absent from selection','realistic HFT/exact FIFO/max4/no dream fill/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'stateCount':len(rows)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
