from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',required=True);ap.add_argument('--replication',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    c=json.loads(Path(a.corpus).read_text(encoding='utf-8')); rep=json.loads(Path(a.replication).read_text(encoding='utf-8'))
    by={(int(r['marketId']),str(r['stateSpec']['marketProposalSide'])):r for r in rep['rows'] if r.get('valid')}
    outrows=[]
    for row in c['rows']:
        mid=int(row['marketId']); rr=next(r for r in rep['rows'] if int(r['marketId'])==mid); F=str(rr['stateSpec']['marketProposalSide']); O='DOWN' if F=='UP' else 'UP'; branch=str(row['action'])
        b0=rr['terminalMetrics']['MARKET_DIRECTION']; b1=rr['terminalMetrics'][branch]
        pf0=float(b0['upQty'] if F=='UP' else b0['downQty'])-float(b0['buyNotional']); po0=float(b0['upQty'] if O=='UP' else b0['downQty'])-float(b0['buyNotional'])
        pf1=float(b1['upQty'] if F=='UP' else b1['downQty'])-float(b1['buyNotional']); po1=float(b1['upQty'] if O=='UP' else b1['downQty'])-float(b1['buyNotional'])
        z=dict(row); z['fixedFavoredSideUp']=1 if F=='UP' else 0; z['dFavoredTerminal']=pf1-pf0; z['dOppositeTerminal']=po1-po0
        for hz in (5000,10000):
            q=rr['localDeltaVsMarketDirection'][str(hz)][branch]
            z[f'dFavored{hz}ms']=float(q['upPayoff'] if F=='UP' else q['downPayoff']); z[f'dOpposite{hz}ms']=float(q['upPayoff'] if O=='UP' else q['downPayoff'])
        outrows.append(z)
    c2=dict(c); c2['version']='MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_CORPUS_V2_FIXED_FAVORED'; c2['rows']=outrows; c2['boundary']=list(c.get('boundary') or [])+['favored side frozen before branch as baseline current-market proposal; never winner/final-Best relabeled','fixed favored/opposite terminal and 5s/10s deltas derived from exact-fork branch payoffs']
    Path(a.output).write_text(json.dumps(c2,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'rows':len(outrows),'markets':len(set(x['marketId'] for x in outrows))},ensure_ascii=False))
if __name__=='__main__':main()
