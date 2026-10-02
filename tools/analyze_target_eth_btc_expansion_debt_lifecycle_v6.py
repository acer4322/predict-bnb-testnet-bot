from __future__ import annotations
import argparse,json,sqlite3,statistics,math,os
from collections import defaultdict
from pathlib import Path

EPS=1e-9

def qtile(xs,q):
    if not xs:return None
    z=sorted(xs);i=(len(z)-1)*q;lo=int(math.floor(i));hi=int(math.ceil(i))
    if lo==hi:return float(z[lo])
    w=i-lo;return float(z[lo]*(1-w)+z[hi]*w)

def summarize_eps(eps):
    if not eps:return {'episodes':0}
    completed=[e for e in eps if e['cleared']]
    reexp=[e for e in eps if e['reexpandCount']>0]
    first_repaid=[e['firstReexpandRepaidFrac'] for e in reexp if e.get('firstReexpandRepaidFrac') is not None]
    return {
      'episodes':len(eps),
      'clearedEpisodes':len(completed),'clearRate':len(completed)/len(eps),
      'reexpandedBeforeClearEpisodes':len(reexp),'reexpandBeforeClearRate':len(reexp)/len(eps),
      'medianInitialDebtShares':qtile([e['initialDebt'] for e in eps],.5),
      'medianPeakDebtShares':qtile([e['peakDebt'] for e in eps],.5),
      'medianFirstReexpandRepaidFrac':qtile(first_repaid,.5),
      'p25FirstReexpandRepaidFrac':qtile(first_repaid,.25),
      'p75FirstReexpandRepaidFrac':qtile(first_repaid,.75),
      'meanFirstReexpandRepaidFrac':statistics.mean(first_repaid) if first_repaid else None,
      'firstReexpandAfterAtLeast50PctRepairRate':sum(x>=.5 for x in first_repaid)/len(first_repaid) if first_repaid else None,
      'firstReexpandAfterAtLeast75PctRepairRate':sum(x>=.75 for x in first_repaid)/len(first_repaid) if first_repaid else None,
      'firstReexpandAfterFullRepairRate':sum(x>=.999 for x in first_repaid)/len(first_repaid) if first_repaid else None,
      'medianEventsToClear':qtile([e['eventsToClear'] for e in completed],.5),
      'medianMsToClear':qtile([e['msToClear'] for e in completed],.5),
      'medianRepairSharesBeforeFirstReexpand':qtile([e['repairBeforeFirstReexpand'] for e in reexp],.5),
      'medianDebtRemainingAtFirstReexpand':qtile([e['debtAtFirstReexpand'] for e in reexp],.5),
    }

def build_scope(rows, scope):
    by=defaultdict(list)
    for r in rows:
        if scope=='MAKER_ONLY' and r['role']!='MAKER':continue
        by[(r['asset'],int(r['market_id']))].append(r)
    out={'BTC':[],'ETH':[]}
    event_stats={a:{'events':0,'expand':0,'repair':0,'flat':0,'reexpandWhileDebt':0,'repairWhileDebt':0} for a in ('BTC','ETH')}
    for (asset,mid),zz in by.items():
        zz=sorted(zz,key=lambda r:(int(r['first_event_ms']),str(r['parent_id'])))
        up=dn=0.0; debt=0.0; ep=None; event_idx=0
        for r in zz:
            sh=float(r['shares']);side=str(r['side']);t=int(r['first_event_ms']);event_idx+=1
            pre_abs=abs(up-dn)
            if side=='UP':up+=sh
            else:dn+=sh
            post_abs=abs(up-dn);dabs=post_abs-pre_abs
            es=event_stats[asset];es['events']+=1
            if dabs>EPS:
                es['expand']+=1
                if ep is None:
                    debt=dabs
                    ep={'marketId':mid,'asset':asset,'startT':t,'startEvent':event_idx,'initialDebt':dabs,'peakDebt':debt,
                        'repairPaid':0.0,'reexpandCount':0,'firstReexpandRepaidFrac':None,'repairBeforeFirstReexpand':0.0,
                        'debtAtFirstReexpand':None,'cleared':False,'eventsToClear':None,'msToClear':None,'lastExpandPostDebt':debt}
                else:
                    es['reexpandWhileDebt']+=1;ep['reexpandCount']+=1
                    base=max(ep['lastExpandPostDebt'],EPS)
                    repaid=max(0.0,min(1.0,(base-debt)/base))
                    if ep['firstReexpandRepaidFrac'] is None:
                        ep['firstReexpandRepaidFrac']=repaid;ep['repairBeforeFirstReexpand']=ep['repairPaid'];ep['debtAtFirstReexpand']=debt
                    debt+=dabs;ep['peakDebt']=max(ep['peakDebt'],debt);ep['lastExpandPostDebt']=debt
            elif dabs<-EPS:
                es['repair']+=1
                if ep is not None and debt>EPS:
                    es['repairWhileDebt']+=1
                    pay=min(debt,-dabs);debt-=pay;ep['repairPaid']+=pay
                    if debt<=EPS:
                        debt=0.0;ep['cleared']=True;ep['eventsToClear']=event_idx-ep['startEvent'];ep['msToClear']=t-ep['startT'];out[asset].append(ep);ep=None
            else:es['flat']+=1
        if ep is not None:
            ep['endDebt']=debt;out[asset].append(ep)
    return out,event_stats

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    con=sqlite3.connect(a.db);con.row_factory=sqlite3.Row
    rows=list(con.execute("select parent_id,asset,market_id,role,side,first_event_ms,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"));con.close()
    scopes={}
    for scope in ('ALL_PARENT','MAKER_ONLY'):
        eps,ev=build_scope(rows,scope)
        scopes[scope]={'eventStats':ev,'BTC':summarize_eps(eps['BTC']),'ETH':summarize_eps(eps['ETH'])}
        b=scopes[scope]['BTC'];e=scopes[scope]['ETH']
        scopes[scope]['ETHminusBTC']={
          'clearRate':(e.get('clearRate') or 0)-(b.get('clearRate') or 0),
          'reexpandBeforeClearRate':(e.get('reexpandBeforeClearRate') or 0)-(b.get('reexpandBeforeClearRate') or 0),
          'medianFirstReexpandRepaidFrac':(e.get('medianFirstReexpandRepaidFrac') or 0)-(b.get('medianFirstReexpandRepaidFrac') or 0),
          'reexpandAfter50Rate':(e.get('firstReexpandAfterAtLeast50PctRepairRate') or 0)-(b.get('firstReexpandAfterAtLeast50PctRepairRate') or 0),
        }
    out={'version':'TARGET_ETH_BTC_EXPANSION_DEBT_LIFECYCLE_V6','researchOnly':True,
         'definition':{'expansionDebt':'positive increase in absolute UP-DOWN shares created by actual Target parent fills; repair pays debt by later negative abs-net deltas','episode':'starts when expansion occurs with no outstanding expansion debt; remains persistent across later repairs/re-expansions until debt reaches zero or market data ends','normalization':'primary re-entry metric is fraction of previous post-expansion debt repaid before re-expansion; raw share size is descriptive only','scopes':['ALL_PARENT','MAKER_ONLY actual fills only; no inferred placement quantity and no TARGET_UNIT=18']},
         'sourceDb':os.path.abspath(a.db),'rows':len(rows),'scopes':scopes}
    p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'scopes':scopes},ensure_ascii=False))
if __name__=='__main__':main()
