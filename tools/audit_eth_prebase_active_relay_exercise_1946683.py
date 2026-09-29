from __future__ import annotations
import argparse,json,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
v2=base.v2
EPS=1e-9;MID=1946683

class RelayExerciseAudit(base.MinimalPairRoleSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.relayAudit=[]
    def _refresh_slots(self,t:int):
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        pre=[]
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or self.key_role.get(key)!='SATELLITE_EXPAND':continue
            try:s=self.snap(o)
            except Exception as ex:s={'status':'SNAP_ERR','error':str(ex)}
            status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status in v2.TERMINAL_STATUSES:
                qv=v2.base.quotes(self.book);side=str(o.get('side'));ask=(float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None)
                state=self._state();pair=(self._pair_ok(side,ask) if ask is not None else None)
                pre.append({'t':int(t),'phase':'PRE_SUPER_TERMINAL_SAT_EXPAND','sid':int(sid),'key':key,'side':side,'status':status,'cum':cum,'state':state,'inv':dict(self.inv),'cost':float(self.cost),'secondsLeft':(end-int(t))/1000.0,'ask':ask,'pairOkAtAsk':pair,'slotKeysBefore':dict(self.slot_key)})
        self.relayAudit.extend(pre)
        super()._refresh_slots(t)
        for row in pre:
            row['slotStillOwnedAfterSuper']=int(row['sid']) in self.slot_key
            row['slotKeysAfter']=dict(self.slot_key)
            state=self._state();row['stateAfterSuper']=state;row['invAfterSuper']=dict(self.inv)
            row['wouldBasicRelayQualify']=bool(row['cum']<=EPS and state[0]=='ONE_SIDED' and state[1]==row['side'] and row['secondsLeft']>180 and row['ask'] is not None and row['pairOkAtAsk'] and not row['slotStillOwnedAfterSuper'])
    def run_audit(self,winner):
        r=self.run_minimal(winner);r['relayExerciseAudit']=self.relayAudit;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='relay_exercise_audit_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];tape=tmp/'tapes'/f'{MID}.json.xz';s=RelayExerciseAudit(tape,4,False)
        try:r=s.run_audit(cr['winner'])
        finally:s.close()
        out={'version':'ETH_PREBASE_ACTIVE_RELAY_EXERCISE_AUDIT_1946683_V1','date':'2026-09-05','researchOnly':True,'behaviorChange':False,'marketId':MID,'metrics':base.slim(r),'audit':r['relayExerciseAudit'],'summary':{'terminalSatelliteExpandEvents':len(r['relayExerciseAudit']),'basicRelayQualifying':sum(bool(x.get('wouldBasicRelayQualify')) for x in r['relayExerciseAudit']),'zeroFillTerminal':sum(float(x.get('cum') or 0.0)<=EPS for x in r['relayExerciseAudit'])},'boundary':['behavior-inert telemetry only','Pair-only baseline action sequence unchanged','no Target/winner runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'audit':out['audit'][:50]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
