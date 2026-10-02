"""Outcome-blind immediate-post-submit preflight for H3a qLadder arming.

Replays only to each frozen target receipt, submits the frozen current-V3B Repair
candidate with current management-arm semantics, records immediate post-submit state,
then intentionally aborts. No suffix/future fill/PnL is evaluated.
"""
from __future__ import annotations
import argparse, importlib.util, json, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('h3a_h4base', HERE/'run_gpt6_h4_joint_candidate_factorial_fork_v1.py')
h4=importlib.util.module_from_spec(spec);spec.loader.exec_module(h4)

class StopAfterIntervention(RuntimeError): pass

class PreflightFork(h4.JointCandidateFork):
    def __init__(self,tape,state):
        super().__init__(tape,state,'A_REPAIR'); self.capture=None
    def _open_one_option(self,t,qv,end):
        target=int(t)==int(self.spec['t']) and not self.seen
        out=super()._open_one_option(t,qv,end)
        if target:
            legs=list(self.interventionLegs)
            key=self.interventionKeys[-1] if self.interventionKeys else None
            o=self.orders.get(key,{}) if key else {}
            self.capture={
                'marketId':int(self.spec['marketId']),'t':int(t),'prefixDigest':self.prefixDigest,
                'submitOk':bool(legs and legs[-1].get('ok')),'leg':legs[-1] if legs else None,
                'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),
                'qLadder':None if self.q_ladder is None else dict(self.q_ladder),
                'physicalCarrier':None if not key else {
                    'side':str(o.get('side') or ''),'role':str(self.key_role.get(key,'UNASSIGNED')),
                    'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),
                    'status':str(o.get('status') or ''),'cancelRequested':bool(o.get('cancelRequested'))
                },
                'liveSlotsAfter':len(self.slot_key),
                'prefixFreeSlots':int(self.spec.get('freeSlots') or 0),
                'prefixQLadderLive':bool(self.spec.get('qLadderLive')),
                'prefixPendingActive':bool(self.spec.get('qPendingActive')),
                'freezeErrors':list(self.freezeErrors),
                'initialDebtQty':float(self.spec.get('initialDebtQty') or 0.0),
                'remainingDebtQty':float(self.spec.get('remainingDebtQty') or 0.0),
                'repairProgressFrac':float(self.spec.get('repairProgressFrac') or 0.0),
                'bookSpread':float((self.spec.get('book') or {}).get('spread') or 0.0)
            }
            raise StopAfterIntervention()
        return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    src=json.loads(Path(a.states).read_text(encoding='utf-8')); states=sorted(list(src['states']),key=lambda s:(int(s['t']),int(s['marketId'])))
    excluded={1823553,1823598,1823603,1823611,1823755,1823769,1823897,1824301,1825962,1827135,1827839,1829435}
    rows=[]; selected=[]; outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3a_arm_preflight_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId'])
            if mid in excluded: continue
            if bool(s.get('qLadderLive')) or bool(s.get('qPendingActive')) or int(s.get('freeSlots') or 0)<1: continue
            sim=PreflightFork(root/'tapes'/f'{mid}.json.xz',s)
            try:
                try: sim.run_qty('__UNSCORED__')
                except StopAfterIntervention: pass
                cap=sim.capture
            finally: sim.close()
            if cap is None: continue
            eligible=bool(cap['submitOk'] and cap['qLadderRoute']=='PASSIVE')
            cap['armEligible']=eligible;rows.append(cap)
            if eligible and len(selected)<3:selected.append(cap)
            print(json.dumps({'progressState':i,'marketId':mid,'submitOk':cap['submitOk'],'qLadderRoute':cap['qLadderRoute'],'armEligible':eligible,'selectedSoFar':[x['marketId'] for x in selected]},ensure_ascii=False),flush=True)
            if len(selected)>=3: break
    payload={'version':'GPT6_H3A_Q_LADDER_ARM_PREFLIGHT_H100_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'scannedRows':rows,'selectedStates':selected,'selectedMarketIds':[x['marketId'] for x in selected],
             'selectionOutcomeBlind':True,'selectedCount':len(selected),'gatePass':len(selected)>=2,
             'boundary':['immediate post-submit only; intentional abort before suffix','current V3B frozen Repair semantics','consumed H100; Round1 exclusions','no winner/future fill/PnL selection','no dream fill/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'selectedMarketIds':payload['selectedMarketIds'],'selectedCount':len(selected),'gatePass':payload['gatePass']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
