"""H4 joint-candidate same-prefix factorial smoke on current V3B realistic HFT.

Research only. Consumed data only. No Target/winner/future input enters candidate generation.
The experiment tests whether two concurrent legal candidate carriers have non-additive
value beyond single Repair / single Expand actions under matched slot/notional controls.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from pathlib import Path
from typing import Any
import sys

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import importlib.util

def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
STAGED=Path(__file__).resolve().parent
H4_STAGE_PACKAGE=STAGED/'h4_tools_v1'
if H4_STAGE_PACKAGE.exists(): sys.path.insert(0,str(H4_STAGE_PACKAGE))
if (STAGED/'run_management_mainline_v3b_role_switch_global_event_fork_v2.py').exists():
    f=_load('h4_role_switch',STAGED/'run_management_mainline_v3b_role_switch_global_event_fork_v2.py')
else:
    from tools import run_management_mainline_v3b_role_switch_global_event_fork_v2 as f
if (STAGED/'extract_management_v1_v3b_preaction_rows_v1.py').exists():
    clean=_load('h4_clean_preaction',STAGED/'extract_management_v1_v3b_preaction_rows_v1.py')
else:
    from tools import extract_management_v1_v3b_preaction_rows_v1 as clean

EPS=f.EPS
TICK=0.01
BRANCHES=(
    'NATIVE','ZERO','A_REPAIR','B_EXPAND',
    'AB_REPAIR_THEN_EXPAND','BA_EXPAND_THEN_REPAIR',
    'AA_REPAIR_FANOUT','BB_EXPAND_FANOUT',
)


def finite(x, default=0.0):
    try:
        y=float(x)
        return y if math.isfinite(y) else default
    except Exception:return default


def payoff_from_terminal(t:dict[str,Any], favored:str)->dict[str,float]:
    up=float(t['upQty'])-float(t['buyNotional']);down=float(t['downQty'])-float(t['buyNotional'])
    fav=up if favored=='UP' else down;opp=down if favored=='UP' else up
    return {'upPayoff':up,'downPayoff':down,'midpoint':0.5*(up+down),
            'inventorySkew':0.5*(fav-opp),'favoredPayoff':fav,'oppositePayoff':opp,
            'floor':min(up,down),'best':max(up,down),'gap':abs(up-down),
            'fills':float(t['fills']),'submits':float(t['submits']),'alternations':float(t['alternations']),
            'buyNotional':float(t['buyNotional']),'gross':float(t['upQty'])+float(t['downQty']),
            'absNetQty':abs(float(t['upQty'])-float(t['downQty']))}


def interaction(yab,ya,yb,y0):
    return {k:float(yab[k])-float(ya[k])-float(yb[k])+float(y0[k]) for k in yab}


class JointCandidateFork(f.RoleSwitchFork):
    def __init__(self,tape,spec,branch):
        super().__init__(tape,spec,branch)
        self.prefixLiveKeys=set();self.interventionKeys=[];self.interventionLegs=[]
        self.prefixFillAccountingLen=0
        self.frozenCandidates={};self.freezeErrors=[];self.sameSideAltMeta=[]

    def _freeze(self,t,qv):
        out={}
        # Repair candidate with the exact managed-arm semantics available at the prefix.
        side=str(self.spec['weakSide']);role=self._repair_role(side);arm=self._forced_arm(t,qv,side,role)
        self.q_arm=arm
        try:rc=self._candidate_from_levels(side,True,False)
        finally:self.q_arm=None
        if rc is None:self.freezeErrors.append('NO_REPAIR_CANDIDATE')
        else:
            p,q,proj=rc;out['A']={'kind':'REPAIR','side':side,'role':role,'price':float(p),'qty':float(q),'proj':proj,'arm':copy.deepcopy(arm)}
            sp=self.spec.get('repairCandidate') or {}
            if sp and (not f.close(p,sp.get('price')) or not f.close(q,sp.get('qty'),1e-8)):
                self.freezeErrors.append('REPAIR_SPEC_MISMATCH')
        side=str(self.spec['expandSide']);role='SATELLITE_EXPAND';ec=self._candidate_from_levels(side,True,False)
        if ec is None:self.freezeErrors.append('NO_EXPAND_CANDIDATE')
        else:
            p,q,proj=ec;out['B']={'kind':'EXPAND','side':side,'role':role,'price':float(p),'qty':float(q),'proj':proj,'arm':None}
            sp=self.spec.get('expandCandidate') or {}
            if sp and (not f.close(p,sp.get('price')) or not f.close(q,sp.get('qty'),1e-8)):
                self.freezeErrors.append('EXPAND_SPEC_MISMATCH')
        self.frozenCandidates=out
        return out

    def _nearest_same_side_alt(self,c,qv):
        side=str(c['side']);p0=float(c['price']);ask=float(qv[side]['ask']);used={round(float(x),10) for x in self._used_prices(side)}
        trials=[]
        # Prefer less aggressive quotes. More aggressive is fallback but must remain passive.
        for n in range(1,9):trials.append(round(p0-n*TICK,10))
        for n in range(1,9):trials.append(round(p0+n*TICK,10))
        for p in trials:
            if not (0.01-EPS<=p<ask-EPS and p<1.0-EPS):continue
            if round(p,10) in used:continue
            if not self._pair_ok(side,p):continue
            q=1.0/p
            if q<=0 or q>12.0+EPS or p*q<1.0-EPS:continue
            return {'kind':c['kind'],'side':side,'role':c['role'],'price':float(p),'qty':float(q),'proj':c['proj'],'arm':None,'alt':True,'primaryPrice':p0}
        return None

    def _submit_frozen(self,t,qv,c,source):
        if c is None:return {'ok':False,'reason':'NO_FROZEN_CANDIDATE'}
        if self.q_pending_active is not None:return {'ok':False,'reason':'PENDING_ACTIVE_PRESENT'}
        if len(self.slot_key)>=self.max_slots:return {'ok':False,'reason':'NO_FREE_SLOT'}
        p=float(c['price']);q=float(c['qty']);side=str(c['side']);role=str(c['role'])
        if not (0<p<1 and q>0 and q<=12+EPS and p*q>=1-EPS):return {'ok':False,'reason':'VENUE_QUANTITY'}
        if any(abs(float(x)-p)<=EPS for x in self._used_prices(side)):return {'ok':False,'reason':'PRICE_ALREADY_USED'}
        if not self._pair_ok(side,p):return {'ok':False,'reason':'PAIR_ILLEGAL'}
        before_n=int(self.n);before_sub=int(self.submits)
        self.q_arm=copy.deepcopy(c.get('arm')) if c.get('kind')=='REPAIR' and not c.get('alt') and self.q_ladder is None else None
        try:ok=bool(self._submit_role(int(t),side,role,p,q,c.get('proj'),source))
        finally:self.q_arm=None
        key=f'{side}_{before_n}' if ok and int(self.submits)>before_sub else None
        if key:self.interventionKeys.append(key)
        rec={'ok':ok,'reason':None if ok else 'SUBMIT_FALSE','side':side,'role':role,'price':p,'qty':q,'key':key,
             'notional':p*q,'alt':bool(c.get('alt')),'qArmUsed':bool(c.get('kind')=='REPAIR' and not c.get('alt'))}
        self.interventionLegs.append(rec)
        return rec

    def _custom_intervention(self,t,qv):
        A=self.frozenCandidates.get('A');B=self.frozenCandidates.get('B')
        plan=[]
        if self.branch=='ZERO':return []
        if self.branch=='A_REPAIR':plan=[A]
        elif self.branch=='B_EXPAND':plan=[B]
        elif self.branch=='AB_REPAIR_THEN_EXPAND':plan=[A,B]
        elif self.branch=='BA_EXPAND_THEN_REPAIR':plan=[B,A]
        elif self.branch=='AA_REPAIR_FANOUT':
            a2=self._nearest_same_side_alt(A,qv);self.sameSideAltMeta.append({'for':'A','candidate':copy.deepcopy(a2)});plan=[A,a2]
        elif self.branch=='BB_EXPAND_FANOUT':
            b2=self._nearest_same_side_alt(B,qv);self.sameSideAltMeta.append({'for':'B','candidate':copy.deepcopy(b2)});plan=[B,b2]
        else:raise ValueError(self.branch)
        return [self._submit_frozen(t,qv,c,'GPT6_H4_JOINT_FACTORIAL_V1') for c in plan]

    def _open_one_option(self,t,qv,end):
        if int(t)==int(self.spec['t']) and not self.seen:
            self.seen=True;self._snap_prefix(t,qv);self.prefixLiveKeys=set((self.prefix or {}).get('liveOrders',{}));self.prefixFillAccountingLen=len(getattr(self,'fill_accounting',[]) or []);self._freeze(t,qv)
            before_n=int(self.n);before=len(self.slot_history)
            if self.branch=='NATIVE':
                out=f.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
                keys=[]
                for n in range(before_n,int(self.n)):
                    for side in ('UP','DOWN'):
                        k=f'{side}_{n}'
                        if k in self.orders:keys.append(k)
                self.interventionKeys=list(keys)
                self.interventionLegs=[{'ok':True,'key':k,'side':self.orders[k]['side'],'price':float(self.orders[k]['price']),'qty':float(self.orders[k]['qty']),'notional':float(self.orders[k]['price'])*float(self.orders[k]['qty']),'role':self.key_role.get(k)} for k in keys]
                self.intervention={'branch':'NATIVE','keys':keys,'newSlotEvents':copy.deepcopy(self.slot_history[before:]),'freezeErrors':self.freezeErrors}
                self.postSubmitState=self._payoff_state(t);self.globalReady=True;return out
            legs=self._custom_intervention(t,qv)
            self.intervention={'branch':self.branch,'legs':copy.deepcopy(legs),'frozenCandidates':copy.deepcopy(self.frozenCandidates),
                               'sameSideAltMeta':copy.deepcopy(self.sameSideAltMeta),'freezeErrors':list(self.freezeErrors),'newSlotEvents':copy.deepcopy(self.slot_history[before:])}
            self.branchKey=self.interventionKeys[-1] if self.interventionKeys else None
            self.branchRole=self.key_role.get(self.branchKey) if self.branchKey else None
            self.postSubmitState=self._payoff_state(t);self.globalReady=True;return
        return super()._open_one_option(t,qv,end)

    def lineage(self,terminal):
        target=int(self.spec['t']);cats={k:{'fills':0,'qty':0.0,'repairQty':0.0,'overflowQty':0.0,'upPayoffDelta':0.0,'downPayoffDelta':0.0,'notional':0.0,'keys':set()} for k in ('INTERVENTION','PREFIX_EXISTING','NEW_SUFFIX')}
        ik=set(self.interventionKeys);pk=set(self.prefixLiveKeys)
        for x in list(getattr(self,'fill_accounting',[]) or [])[int(self.prefixFillAccountingLen):]:
            key=str(x.get('key'));cat='INTERVENTION' if key in ik else ('PREFIX_EXISTING' if key in pk else 'NEW_SUFFIX')
            q=float(x.get('confirmedQty') or 0.0);p=float(x.get('executionPriceFromInheritedSubstrate') or 0.0);side=str(x.get('side'))
            if q<=EPS:continue
            du=q*(1-p) if side=='UP' else -q*p;dd=q*(1-p) if side=='DOWN' else -q*p
            z=cats[cat];z['fills']+=1;z['qty']+=q;z['repairQty']+=float(x.get('matchedRepairQty') or 0.0);z['overflowQty']+=float(x.get('overflowQty') or 0.0);z['upPayoffDelta']+=du;z['downPayoffDelta']+=dd;z['notional']+=q*p;z['keys'].add(key)
        for z in cats.values():z['keys']=sorted(z['keys'])
        p0=self.prefixState or {};up0=float(p0.get('upPayoff') or 0.0);dn0=float(p0.get('downPayoff') or 0.0)
        up1=float(terminal['upQty'])-float(terminal['buyNotional']);dn1=float(terminal['downQty'])-float(terminal['buyNotional'])
        su=sum(z['upPayoffDelta'] for z in cats.values());sd=sum(z['downPayoffDelta'] for z in cats.values())
        return {'categories':cats,'terminalMinusPrefix':{'upPayoff':up1-up0,'downPayoff':dn1-dn0},
                'sumAttributed':{'upPayoff':su,'downPayoff':sd},'residual':{'upPayoff':up1-up0-su,'downPayoff':dn1-dn0-sd}}


def branch_required_legs(branch):
    return {'NATIVE':None,'ZERO':0,'A_REPAIR':1,'B_EXPAND':1,'AB_REPAIR_THEN_EXPAND':2,'BA_EXPAND_THEN_REPAIR':2,'AA_REPAIR_FANOUT':2,'BB_EXPAND_FANOUT':2}[branch]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=4)
    a=ap.parse_args();spec_payload=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=list(spec_payload['states'])[:a.max_states]
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    rows=[]
    with tempfile.TemporaryDirectory(prefix='gpt6_h4_joint_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz'
            native=f.v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:reference=native.run_qty('__UNSCORED__')
            finally:native.close()
            br={}
            for b in BRANCHES:
                sim=JointCandidateFork(tape,s,b)
                try:
                    raw=sim.run_branch();term=f.terminal(raw,s);fav=str(s['expandSide']);pv=payoff_from_terminal(term,fav)
                    winner=str(cohort[mid]['winner']).upper();term['winnerPnlPosthoc']=float(raw['raw']['upQty'] if winner=='UP' else raw['raw']['downQty'])-float(raw['raw']['buyNotional']);term['gross']=float(term['upQty'])+float(term['downQty'])
                    lineage=sim.lineage(term)
                    legs=copy.deepcopy(sim.interventionLegs);req=branch_required_legs(b)
                    available=(b=='NATIVE') or (req==0) or (len(legs)==req and all(bool(x.get('ok')) for x in legs))
                    br[b]={'available':available,'prefixDigest':raw['prefixDigest'],'prefixState':raw['prefixState'],'intervention':copy.deepcopy(sim.intervention),
                           'interventionKeys':list(sim.interventionKeys),'interventionSubmittedNotional':sum(float(x.get('notional') or 0.0) for x in legs if x.get('ok')),
                           'interventionSubmittedQty':sum(float(x.get('qty') or 0.0) for x in legs if x.get('ok')),'terminal':term,'vector':pv,'lineage':lineage,
                           'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen'])}
                finally:sim.close()
            digs={x['prefixDigest'] for x in br.values()};native_parity=clean._eq(clean._physical_core(reference),clean._physical_core((lambda:None)() if False else reference))
            # Native parity against a fresh plain replay is evaluated via physical terminal core below.
            nterm=br['NATIVE']['terminal'];refterm={'floor':float(reference['floor']),'best':float(reference['best']),'upQty':float(reference['upQty']),'downQty':float(reference['downQty']),'buyNotional':float(reference['buyNotional']),'fills':int(reference['fillEvents']),'submits':int(reference['submits']),'alternations':int(reference.get('fillSideAlternations') or 0)}
            native_terminal_parity=all(f.close(nterm[k],refterm[k],1e-8) for k in refterm)
            availability=all(br[b]['available'] for b in BRANCHES)
            distinct_controls=True
            for b in ('AA_REPAIR_FANOUT','BB_EXPAND_FANOUT'):
                legs=(br[b]['intervention'] or {}).get('legs') or []
                distinct_controls &= len(legs)==2 and abs(float(legs[0].get('price') or 0)-float(legs[1].get('price') or 0))>EPS
            decomp_clean=all(max(abs(float(x['lineage']['residual']['upPayoff'])),abs(float(x['lineage']['residual']['downPayoff'])))<=1e-7 for x in br.values())
            checks={'prefixParity':len(digs)==1 and None not in digs,'nativeTerminalParity':native_terminal_parity,'allTriggered':all(x['triggered'] for x in br.values()),
                    'allBranchesAvailable':availability,'sameSideControlsDistinctPrice':bool(distinct_controls),'allLedgerClean':all(x['ledgerClean'] for x in br.values()),
                    'allMax4':all(x['max4'] for x in br.values()),'lineagePayoffAccountingCloses':decomp_clean}
            v={b:br[b]['vector'] for b in BRANCHES};j_ab=interaction(v['AB_REPAIR_THEN_EXPAND'],v['A_REPAIR'],v['B_EXPAND'],v['ZERO']);j_ba=interaction(v['BA_EXPAND_THEN_REPAIR'],v['A_REPAIR'],v['B_EXPAND'],v['ZERO'])
            resource={b:{'submitNotional':br[b]['interventionSubmittedNotional'],'submitQty':br[b]['interventionSubmittedQty'],'legs':len(br[b]['interventionKeys']),
                         'terminalBuyNotional':br[b]['terminal']['buyNotional'],'terminalGross':br[b]['terminal']['gross'],'terminalFills':br[b]['terminal']['fills']} for b in ('AB_REPAIR_THEN_EXPAND','BA_EXPAND_THEN_REPAIR','AA_REPAIR_FANOUT','BB_EXPAND_FANOUT')}
            row={'marketId':mid,'t':int(s['t']),'researchStratum':s.get('h4ResearchStratum'),'stateSpec':s,'checks':checks,'correctnessPass':all(checks.values()),'branches':br,
                 'interaction':{'AB':j_ab,'BA':j_ba,'orderEffectABminusBA':{k:v['AB_REPAIR_THEN_EXPAND'][k]-v['BA_EXPAND_THEN_REPAIR'][k] for k in v['AB_REPAIR_THEN_EXPAND']}},
                 'resourceControls':resource}
            rows.append(row);(outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'stratum':s.get('h4ResearchStratum'),'correct':row['correctnessPass'],'checks':checks,
                              'J_AB':{k:round(j_ab[k],6) for k in ('upPayoff','downPayoff','midpoint','inventorySkew','fills','buyNotional')},
                              'J_BA':{k:round(j_ba[k],6) for k in ('upPayoff','downPayoff','midpoint','inventorySkew','fills','buyNotional')}},ensure_ascii=False),flush=True)
    metrics=('upPayoff','downPayoff','midpoint','inventorySkew','favoredPayoff','oppositePayoff','floor','best','fills','submits','alternations','buyNotional','gross','absNetQty')
    aggregate={}
    for order in ('AB','BA'):
        aggregate[order]={k:{'values':[float(r['interaction'][order][k]) for r in rows],
                                    'mean':sum(float(r['interaction'][order][k]) for r in rows)/len(rows),
                                    'nonzero':sum(abs(float(r['interaction'][order][k]))>1e-8 for r in rows)} for k in metrics}
    payload={'version':'GPT6_H4_JOINT_CANDIDATE_FACTORIAL_FORK_V1_20260907','researchOnly':True,'runtimeAuthority':False,'seams':len(rows),'runs':len(rows)*len(BRANCHES),
             'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,'aggregateInteraction':aggregate,
             'boundary':['current V3B exact-FIFO realistic HFT','same strict-past prefix within each seam','consumed H100 only','Round1 markets excluded by prereg','A/B frozen from prefix before any branch submit','AB and BA represented separately; no false atomic-submit claim','AA/BB use two venue-minimum same-side carriers as equal-slot/equal-notional-class controls','suffix immediately returns to current V3B','winner appended posthoc only','no Target runtime input','no fixed-time strategy rule','no dream fill','max4 and <=180s inherited','no NEW24-B','no 8781'],
             'limitations':['repairProgressFrac is only a sampling proxy for service completion, not exact pending-reservation coverage','same-side AA/BB second price is nearest less-aggressive Pair-legal distinct quote; this is a resource control, not a proposed policy','lineage payoff decomposition is exact accounting attribution by carrier birth class, not a complete causal mediation proof','four-seam smoke tests mechanism existence only, not 70/30 breadth'],
             'sha256':{'runner':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'specs':hashlib.sha256(Path(a.specs).read_bytes()).hexdigest()}}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'seams':payload['seams'],'runs':payload['runs'],'allCorrectnessPass':payload['allCorrectnessPass'],'aggregateMidpointInteraction':{o:payload['aggregateInteraction'][o]['midpoint']['mean'] for o in ('AB','BA')}},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
