from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CURR=ROOT/'data/research/r3_v0/r31_r3s_response_l21_l25_v1.json'
REAL=ROOT/'data/research/r3_v0/r31_r3s_response_real_echtgeld_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_r31_exact_precedence_v2.json'

def summarize_state(r31:dict):
    live=r31.get('liveBySide') if isinstance(r31.get('liveBySide'),dict) else {}
    unresolved=0.0; unknown=0.0; maxage=0.0
    for side in ('UP','DOWN'):
        ss=live.get(side) if isinstance(live.get(side),dict) else {}
        for role in ('maker','taker'):
            rr=ss.get(role) if isinstance(ss.get(role),dict) else {}
            unresolved += float(rr.get('unresolvedQty') or 0.0)
            unknown += float(rr.get('unknownCount') or 0.0)
            maxage=max(maxage,float(rr.get('maxAgeMs') or 0.0))
    return unresolved,unknown,maxage

def exact_response(r31:dict):
    unresolved,unknown,maxage=summarize_state(r31)
    situation=str(r31.get('situationCode') or '')
    fill15=int(r31.get('recent15sFillDeltaCount') or 0)
    stall15=int(r31.get('recent15sStallCount') or 0)
    if unknown>0 or situation in {'UNKNOWN_QUARANTINE','CANCEL_UNKNOWN'}:
        return 'WAIT_EXECUTION_CERTAINTY'
    if situation=='CANCEL_PENDING':
        return 'WAIT_CANCEL_TERMINAL_ACK'
    if unresolved<=1e-9:
        return 'NORMAL_R3S'
    if fill15>0 and stall15==0:
        return 'KEEP_AND_OBSERVE_RECOVERY'
    if maxage>=15000 and stall15>0:
        return 'REASSESS_OLDEST_BLOCKER'
    return 'WAIT_PENDING_CHILDREN'

def main():
    curr=json.loads(CURR.read_text(encoding='utf-8'))
    exact=[]
    for c in curr.get('cases',[]):
        pred=exact_response(c['r31']); exp=str(c.get('expectedPrimary') or '')
        exact.append({'name':c.get('name'),'expected':exp,'predicted':pred,'pass':pred==exp})
    real=json.loads(REAL.read_text(encoding='utf-8'))
    real_rows=[]; reconstructable=[]; unreconstructable=[]
    for market in real.get('rows',[]):
        mid=int(market.get('marketId') or 0)
        for s in market.get('responseSamples',[]):
            situation=str(s.get('situation') or '')
            expected=str(s.get('response') or '')
            # The old response artifact omitted recent15sFillDeltaCount/recent15sStallCount.
            # Only precedence states uniquely determined from retained fields are exact-reconstructable.
            if situation in {'UNKNOWN_QUARANTINE','CANCEL_UNKNOWN'}:
                pred='WAIT_EXECUTION_CERTAINTY'; why='certainty_precedence'
            elif situation=='CANCEL_PENDING':
                pred='WAIT_CANCEL_TERMINAL_ACK'; why='cancel_terminal_precedence'
            elif not (s.get('oldest') or s.get('latest')) and situation in {'IDLE','TERMINAL_REMAINDER'}:
                pred='NORMAL_R3S'; why='no_live_child_proxy'
            else:
                unreconstructable.append({'marketId':mid,'atMs':s.get('atMs'),'situation':situation,'expected':expected,'reason':'missing_recent15sFillDeltaCount/recent15sStallCount and/or unresolvedQty'})
                continue
            r={'marketId':mid,'atMs':s.get('atMs'),'situation':situation,'expected':expected,'predicted':pred,'reason':why,'pass':pred==expected}
            reconstructable.append(r)
    art={
        'version':'R4_MANAGEMENT_R31_EXACT_PRECEDENCE_V2',
        'researchOnly':True,'actionAuthority':False,
        'classifierOrder':['UNKNOWN/CANCEL_UNKNOWN -> WAIT_EXECUTION_CERTAINTY','CANCEL_PENDING -> WAIT_CANCEL_TERMINAL_ACK','no unresolved -> NORMAL_R3S','recent fill and no stall -> KEEP_AND_OBSERVE_RECOVERY','age>=15s and stall>0 -> REASSESS_OLDEST_BLOCKER','else -> WAIT_PENDING_CHILDREN'],
        'completeStateCurriculum':{'cases':len(exact),'passed':sum(x['pass'] for x in exact),'allPass':all(x['pass'] for x in exact),'rows':exact},
        'realEchtgeldRetainedFieldReplay':{'totalSamples':sum(len(x.get('responseSamples',[])) for x in real.get('rows',[])),'exactReconstructable':len(reconstructable),'passed':sum(x['pass'] for x in reconstructable),'allPass':all(x['pass'] for x in reconstructable) if reconstructable else None,'rows':reconstructable,'notReconstructable':len(unreconstructable),'notReconstructableExamples':unreconstructable[:12]},
        'guard':'Do not claim 46/46 exact replay: old real-response artifact did not persist the 15s fill/stall counters needed to distinguish WAIT/KEEP/REASSESS within LIVE states.'
    }
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'curriculum':art['completeStateCurriculum'],'realSummary':{k:v for k,v in art['realEchtgeldRetainedFieldReplay'].items() if k not in {'rows','notReconstructableExamples'}}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
