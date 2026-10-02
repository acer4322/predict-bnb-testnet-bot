from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    j=json.loads(Path(args.input).read_text());rows=[]
    for r in j.get('episodes',[]):
        reasons=[]
        if not (60.0 <= float(r['startSecondsLeft']) < 180.0): reasons.append('OUTSIDE_MANAGEMENT')
        if float(r['startSecondsLeft']) < 130.642: reasons.append('INSUFFICIENT_COMPLETION_HORIZON')
        if float(r['startFloor']) > 1e-9: reasons.append('POSITIVE_FLOOR')
        if float(r['startAbsNet']) <= 36.0 + 1e-9: reasons.append('SMALL_DEFICIT_LE_36')
        if float(r['startOwners']) < 1.0: reasons.append('NO_EXISTING_WEAK_CARRIER')
        # Same-objective Taker semantics: parent recurrence and Taker role must agree.
        if float(r['maxPParent']) < 0.5: reasons.append('NO_PARENT_RECURRENCE_CONSENSUS')
        if float(r['maxPTaker']) < 0.5: reasons.append('NO_TAKER_SIGNAL')
        if float(r['maxPTaker']) <= float(r['maxPMaker']): reasons.append('TAKER_NOT_ABOVE_MAKER')
        z=dict(r);z['eligible']=not reasons;z['blockedReasons']=reasons;rows.append(z)
    elig=[r for r in rows if r['eligible']]
    out={'version':'R4_V15_CAUSAL_CANDIDATES_V3','researchOnly':True,'actionAuthority':False,'frozenBeforeCausalReplay':True,'selectionUsesOutcome':False,'selectionRule':{
        'phase':'60<=secondsLeft<180','completionHorizon':'secondsLeft>=130.642','floor':'startFloor<=0','deficit':'startAbsNet>36','carrier':'startOwners>=1','semanticConsensus':'maxPParent>=0.5 AND maxPTaker>=0.5 AND maxPTaker>maxPMaker'},
        'sourceEpisodes':len(rows),'eligibleCount':len(elig),'eligibleMarkets':sorted(set(int(r['marketId']) for r in elig)),'eligibleEpisodes':elig,'allEpisodesWithBlockReasons':rows,
        'guards':['No settlement/winner/PnL used for selection','No <=180s ADD exposure','Same-objective REPAIR only','Existing weak carrier required','0-60 Protection excluded','One-shot causal action only in next stage']}
    Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'eligibleCount':len(elig),'eligibleMarkets':out['eligibleMarkets'],'episodes':elig},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
