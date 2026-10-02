from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--trace',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--cohort',required=True);a=ap.parse_args()
    d=json.load(open(a.trace,encoding='utf-8')); out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True); idx=[]
    for ordinal,m in enumerate(d['markets'],1):
        t=m['terminal'];mid=int(t['marketId']);dom='UP' if t['up']>t['down']+1e-9 else 'DOWN' if t['down']>t['up']+1e-9 else 'FLAT';match=(dom==t['winner']) if dom!='FLAT' else None
        acc=[x for x in m['actionAttempts'] if x.get('accepted')];mat=m['materializedActions'];eps=m['reexpandEpisodes']
        summary={'marketId':mid,'cohort':a.cohort,'ordinal':ordinal,'winner':t['winner'],'pnl':t['pnl'],'oppositePnl':t.get('oppositePnl'),'floor':t.get('floor'),'bestEndpoint':max(t['pnl'],t.get('oppositePnl',t['pnl'])),'worstEndpoint':min(t['pnl'],t.get('oppositePnl',t['pnl'])),'buyNotional':t['buyNotional'],'pairCoverage':t['pairCoverage'],'absNet':t['absNet'],'fills':t['fills'],'submits':t['submits'],'up':t['up'],'down':t['down'],'dominantSide':dom,'dominantMatch':match,'duplicateBlocked':t.get('duplicateBlocked'),'localPendingBlocked':t.get('localPendingBlocked'),'acceptedRepair':sum(x['roleAtAttempt']=='REPAIR' for x in acc),'acceptedExpand':sum(x['roleAtAttempt']=='EXPAND' for x in acc),'materializedRepair':sum(x['roleAtAttempt']=='REPAIR' for x in mat),'materializedExpand':sum(x['roleAtAttempt']=='EXPAND' for x in mat),'reexpandEpisodes':len(eps),'zeroRepairReexpandEpisodes':sum(x['materializedRepairCount']==0 for x in eps),'hasRepairReexpandEpisodes':sum(x['materializedRepairCount']>0 for x in eps),'targetPnl':t.get('targetPnl')}
        detail={'summary':summary,'trace':m}
        jp=out/f'{mid}.json';jp.write_text(json.dumps(detail,indent=2,ensure_ascii=False),encoding='utf-8')
        md=f"""# Market {mid} — {a.cohort}\n\n- Ordinal: {ordinal}\n- Winner: `{t['winner']}`\n- OUR PnL: `{t['pnl']:.6f}`\n- Opposite endpoint: `{t.get('oppositePnl',float('nan')):.6f}`\n- Floor / Best endpoint: `{summary['worstEndpoint']:.6f}` / `{summary['bestEndpoint']:.6f}`\n- Buy notional: `{t['buyNotional']:.6f}`\n- Pair coverage: `{t['pairCoverage']:.6f}`\n- Abs-net: `{t['absNet']:.6f}`\n- Fills / submits: `{t['fills']}` / `{t['submits']}`\n- UP / DOWN terminal shares: `{t['up']:.6f}` / `{t['down']:.6f}`\n- Dominant side / matched winner: `{dom}` / `{match}`\n- Local-pending blocks: `{t.get('localPendingBlocked')}`\n- Accepted Repair / Expand: `{summary['acceptedRepair']}` / `{summary['acceptedExpand']}`\n- Materialized Repair / Expand: `{summary['materializedRepair']}` / `{summary['materializedExpand']}`\n- Re-expand episodes: `{len(eps)}`; zero-repair `{summary['zeroRepairReexpandEpisodes']}`; has-repair `{summary['hasRepairReexpandEpisodes']}`\n- Target same-market PnL (offline anatomy only): `{t.get('targetPnl')}`\n\n## Detail pointer\n\nFull strict-past action attempts, fill events, materialized actions, and re-expand episode snapshots are in `{jp.as_posix()}`.\n"""
        mp=out/f'{mid}.md';mp.write_text(md,encoding='utf-8');idx.append({'marketId':mid,'ordinal':ordinal,'summary':summary,'md':mp.as_posix(),'json':jp.as_posix()})
    (out/'INDEX.json').write_text(json.dumps({'cohort':a.cohort,'sourceTrace':a.trace,'markets':idx},indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'cohort':a.cohort,'markets':len(idx),'out':str(out)},ensure_ascii=False))
if __name__=='__main__':main()
