from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
base=v3b.base
HORIZONS=(500,1000,2000,5000,10000)


def norm(x):
    if isinstance(x,dict): return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)): return [norm(v) for v in x]
    if isinstance(x,(int,float,str,bool)) or x is None: return x
    try:return float(x)
    except Exception:return str(x)

def digest(x): return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def near(a,b,tol=1e-9): return abs(float(a)-float(b))<=tol


class ThreeWayFork(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,spec,branch):
        super().__init__(tape)
        self.spec=spec; self.branch=str(branch)
        self.target_seen=False; self.prefix=None; self.prefix_digest=None; self.intervention=None
        self.timeline=[]

    def _snapshot_prefix(self,t,qv):
        live={}
        for key,o in self.orders.items():
            if key in set(self.slot_key.values()) or base.v2.base.live(str(o.get('status') or '')):
                live[key]={k:o.get(k) for k in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        snap={
            't':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),
            'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),'liveOrders':live,
            'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),
            'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),
            'placeHist':list(self.placeHist),'fillSequence':copy.deepcopy(self.fill_side_sequence),
            'quotes':norm(qv)
        }
        self.prefix=norm(snap); self.prefix_digest=digest(self.prefix)

    def _state(self,t):
        up=float(self.inv['UP']); dn=float(self.inv['DOWN']); cost=float(self.cost)
        pu=up-cost; pd=dn-cost
        debt_up=float(self._aggregate_for_repair_side('UP')); debt_dn=float(self._aggregate_for_repair_side('DOWN'))
        return {'t':int(t),'upPayoff':pu,'downPayoff':pd,'best':max(pu,pd),'floor':min(pu,pd),'gap':abs(pu-pd),
                'upQty':up,'downQty':dn,'cost':cost,'repairDebtUP':debt_up,'repairDebtDOWN':debt_dn,
                'totalDebt':debt_up+debt_dn,'liveSlots':len(self.slot_key),
                'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),
                'pendingActive':self.q_pending_active is not None}

    def process(self,t):
        super().process(t)
        if self.target_seen and int(t)>=int(self.spec['t']):
            self.timeline.append(self._state(t))

    def _open_one_option(self,t,qv,end):
        target=int(self.spec['t'])
        if int(t)==target and not self.target_seen:
            self.target_seen=True; self._snapshot_prefix(t,qv)
            ctrl=self.spec['nativeReexpand']; rep=self.spec['nativeRepair']
            dec=base.MinimalPairRoleSim._role_decision(self,qv)
            before_hist=len(self.slot_history); before_n=int(self.n)
            common={'decision':norm(dec),'beforeN':before_n,'branch':self.branch}
            if self.branch=='NATIVE_REEXPAND':
                out=super()._open_one_option(t,qv,end)
                new=norm(self.slot_history[before_hist:])
                submits=[x for x in self.slot_history[before_hist:] if x.get('event')=='ROLE_SLOT_SUBMIT']
                ok=any(str(x.get('side'))==str(ctrl['side']) and str(x.get('role'))=='SATELLITE_EXPAND' and near(x.get('price') or 0,ctrl['price'],1e-12) and near(x.get('qty') or 0,ctrl['qty'],1e-8) for x in submits)
                self.intervention={**common,'submitMatchesFrozen':bool(ok),'newSlotEvents':new}
                return out
            if self.branch=='HOLD':
                exact=bool(dec[0]==ctrl['side'] and dec[1]=='SATELLITE_EXPAND' and len(self.slot_key)<self.max_slots)
                self.intervention={**common,'exactPreconditions':exact,'holdApplied':exact,'newSlotEvents':[]}
                if exact:return
                return super()._open_one_option(t,qv,end)
            if self.branch=='NATIVE_REPAIR':
                debt=float(self._aggregate_for_repair_side(rep['side']))
                managed_claim=bool(self.q_ladder is not None or self.q_pending_active is not None)
                live_rep=[x for x in self._live_role_rows(side=rep['side']) if x[3] in v3b.REPAIR_ROLES]
                free=max(0,int(self.max_slots)-len(self.slot_key))
                exact=bool(dec[0]==ctrl['side'] and dec[1]=='SATELLITE_EXPAND' and debt+EPS>=float(rep['qty']) and not managed_claim and not live_rep and free>0)
                ok=False
                if exact:
                    ok=base.MinimalPairRoleSim._submit_role(self,int(t),rep['side'],rep['role'],float(rep['price']),float(rep['qty']),None,'MANAGEMENT_V1_HISTORY_MARKET_CONFLICT_NATIVE_REPAIR')
                self.intervention={**common,'exactPreconditions':exact,'submitOk':bool(ok),'debt':debt,'managedClaim':managed_claim,'liveRepairCount':len(live_rep),'freeSlots':free,'newSlotEvents':norm(self.slot_history[before_hist:])}
                if exact:return
                return super()._open_one_option(t,qv,end)
            raise RuntimeError(self.branch)
        return super()._open_one_option(t,qv,end)

    def run_branch(self):
        r=super().run_qty('__UNSCORED__')
        r['branch']=self.branch; r['prefixDigest']=self.prefix_digest; r['intervention']=self.intervention
        r['forkTimeline']=self.timeline
        return r


def at_horizon(r,target,h):
    rows=[x for x in r.get('forkTimeline',[]) if int(x['t'])<=int(target)+int(h)]
    return rows[-1] if rows else None

def metrics(r):
    return {'floor':float(r['floor']),'best':float(r['best']),'gap':float(r['best'])-float(r['floor']),
            'fills':int(r['fillEvents']),'submits':int(r['submits']),'alternations':int(r.get('fillSideAlternations') or 0),
            'buyNotional':float(r['buyNotional']),'upQty':float(r['upQty']),'downQty':float(r['downQty']),
            'activeSubmits':int((r.get('quantityLadderCounters') or {}).get('managedActiveSubmits',0)),
            'managedRepairQty':float(r.get('quantityManagedRepairQtyDiagnostic') or 0.0),
            'ledgerViolations':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},
            'maxSlots':int(r.get('maxSimultaneousSlots') or 0)}

def delta(a,b):
    if a is None or b is None:return None
    keys=('upPayoff','downPayoff','best','floor','gap','upQty','downQty','cost','repairDebtUP','repairDebtDOWN','totalDebt')
    return {k:float(b[k])-float(a[k]) for k in keys}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--specs',required=True); ap.add_argument('--output',required=True); ap.add_argument('--max-states',type=int,default=3); a=ap.parse_args()
    scan=json.loads(Path(a.specs).read_text(encoding='utf-8'))
    specs=(scan.get('smokeCandidates') or scan.get('firstEligiblePerMarket') or [])[:max(1,int(a.max_states))]
    if not specs: raise RuntimeError('no smoke candidates in specs')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_threeway_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for idx,s in enumerate(specs,1):
            mid=int(s['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; result={}
            for branch in ('NATIVE_REEXPAND','HOLD','NATIVE_REPAIR'):
                sim=ThreeWayFork(tape,s,branch)
                try:r=sim.run_branch()
                finally:sim.close()
                result[branch]=r
            digs={b:result[b].get('prefixDigest') for b in result}; prefix_parity=len(set(digs.values()))==1 and None not in digs.values()
            m={b:metrics(result[b]) for b in result}
            ctrl=result['NATIVE_REEXPAND']; target=int(s['t'])
            local={}
            for h in HORIZONS:
                c=at_horizon(ctrl,target,h)
                local[str(h)]={b:delta(c,at_horizon(result[b],target,h)) for b in ('HOLD','NATIVE_REPAIR')}
            checks={
                'prefixParity':prefix_parity,
                'controlSubmitMatchesFrozen':bool((result['NATIVE_REEXPAND'].get('intervention') or {}).get('submitMatchesFrozen')),
                'holdExact':bool((result['HOLD'].get('intervention') or {}).get('exactPreconditions')) and bool((result['HOLD'].get('intervention') or {}).get('holdApplied')),
                'repairExact':bool((result['NATIVE_REPAIR'].get('intervention') or {}).get('exactPreconditions')) and bool((result['NATIVE_REPAIR'].get('intervention') or {}).get('submitOk')),
                'allLedgerClean':all(not m[b]['ledgerViolations'] for b in m),
                'allMax4':all(m[b]['maxSlots']<=4 for b in m)
            }
            term={b:{k:(m[b][k]-m['NATIVE_REEXPAND'][k]) for k in ('floor','best','gap','fills','submits','alternations','buyNotional','activeSubmits','managedRepairQty')} for b in ('HOLD','NATIVE_REPAIR')}
            row={'marketId':mid,'t':target,'stateSpec':s,'prefixDigests':digs,'checks':checks,'localDeltaVsNativeReexpand':local,'terminalMetrics':m,'terminalDeltaVsNativeReexpand':term,'valid':all(checks.values())}
            rows.append(row)
            print(json.dumps({'progress':idx,'marketId':mid,'valid':row['valid'],'checks':checks,'terminalDelta':term},ensure_ascii=False),flush=True)
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_CONFLICT_THREEWAY_FORK_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,
         'stateCount':len(rows),'allCorrectnessPass':all(x['valid'] for x in rows),'rows':rows,
         'boundary':['same strict-past V3B prefix for all three branches','one receipt intervention only, then frozen V3B suffix from resulting state','actions: native baseline REEXPAND vs HOLD vs unclaimed ordinary native REPAIR','no manager authority minted and max4 unchanged','winner/settlement/Target future absent from state and action selection','local vector outcomes at 0.5/1/2/5/10s; terminal outcome diagnostic only','realistic HFT/exact FIFO/no dream fill','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output)
    op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'stateCount':len(rows)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
