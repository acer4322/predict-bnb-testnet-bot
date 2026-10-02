from __future__ import annotations
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r3_v0/r31_r3s_response_real_echtgeld_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_r31_precedence_replay_v1.json'
def created_ms(s):
 xs=re.findall(r'\d{13}',str(s));return int(xs[0]) if xs else None
def manager_response(sample):
 sit=str(sample.get('situation','')); own=str(sample.get('ownership','')); at=int(sample['atMs']); c=created_ms(sample.get('oldest'))
 if sit=='UNKNOWN_QUARANTINE' or own=='UNKNOWN_CHILD':return 'WAIT_EXECUTION_CERTAINTY'
 if sit=='CANCEL_PENDING':return 'WAIT_CANCEL_TERMINAL_ACK'
 age=(at-c)/1000. if c else 0.
 if sit=='LIVE_NO_FILL' and age>=15:return 'REASSESS_OLDEST_BLOCKER'
 return 'WAIT_PENDING_CHILDREN'
def main():
 src=json.loads(SRC.read_text(encoding='utf-8'));rows=[]
 for m in src['rows']:
  for s in m.get('responseSamples',[]):
   pred=manager_response(s);rows.append({'marketId':m['marketId'],'atMs':s['atMs'],'situation':s.get('situation'),'ownership':s.get('ownership'),'oldestAgeSec':None if created_ms(s.get('oldest')) is None else (int(s['atMs'])-created_ms(s.get('oldest')))/1000.,'expected':s.get('response'),'predicted':pred,'pass':pred==s.get('response')})
 art={'version':'R4_MANAGEMENT_R31_PRECEDENCE_REPLAY_V1','researchOnly':True,'actionAuthority':False,'purpose':'Verify that the new R4 Manager can consume R3.1 information-only execution facts with the legacy safety precedence unchanged, before learned M0/M1 arbitration.','coverage':{'markets':len(set(r['marketId'] for r in rows)),'samples':len(rows),'passed':sum(r['pass'] for r in rows),'passRate':sum(r['pass'] for r in rows)/len(rows) if rows else None},'precedence':['UNKNOWN_QUARANTINE/UNKNOWN_CHILD -> WAIT_EXECUTION_CERTAINTY','CANCEL_PENDING -> WAIT_CANCEL_TERMINAL_ACK','LIVE_NO_FILL and oldest unresolved age >=15s -> REASSESS_OLDEST_BLOCKER','otherwise -> WAIT_PENDING_CHILDREN'],'rows':rows,'guards':['R3.1 remains information-only; these are Manager responses to R3.1 facts.','This hard execution-certainty precedence sits above learned M0/M1 and cannot be overridden by model scores.','No order/cancel/Taker mutation is authorized by this replay itself.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'failures':[r for r in rows if not r['pass']]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
