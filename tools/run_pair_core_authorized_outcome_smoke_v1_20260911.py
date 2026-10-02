"""Fixed12 historical quote-input exercises of a new controlled component.

These are NOT12 HFT markets and not actual Target goal/fill replay. All target
quantities, event-outcome labels and winners are excluded from quote selection.
Synthetic authorizations and scripted receipts stay explicitly separated.
"""
from pathlib import Path
from collections import Counter
from dataclasses import replace
import hashlib,json,math,subprocess,sys,time
import duckdb
from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant
from tools.pair_core_authorized_outcome_preview_v1 import (
    InitialPortfolio,EndpointAuthority,ProposedOrder,preview,commit_reservations)

BASE=Path('data/research/r4_v0/p0_provenance_v1')
OUT=BASE/'pair_core_authorized_outcome_v1_20260911'
SOURCE=BASE/'target_concurrent_route_economics_v1_20260911/side_states.parquet'
EXPECTED_SOURCE='2e97491cf365b9c3ba423b5a81fa0955a7d51bf6c298eb4ac36b017e20477147'
PINNED={'tools/pair_core_economic_grant_ledger_v1.py':'6ee5dffd59fa45a48f2b18e8a592b1922c9e5202116e2712f5e31df4d72fea0c',
        'tools/pair_core_asset_route_sizing_v2.py':'aac866e856748d95c0551f8366576e65e2b30139c8b6ef8806d08f83b7982ef8'}


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    with p.open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def ledger():
    gs=[Grant(1,'R1','DOWN',24.,0.,25.,'EXPERIMENT_REPAIR_24'),
        Grant(2,'A1','UP',0.,18.,20.,'EXPERIMENT_ADD_18')]
    out=EconomicGrantLedger(sum(g.cash_limit for g in gs))
    for g in gs:out.issue(g)
    return out


def snapshots():
    assert sha(SOURCE)==EXPECTED_SOURCE
    c=duckdb.connect();c.execute('SET threads=1');c.execute("SET memory_limit='64MB'")
    ids=c.execute('SELECT asset,block,MIN(market_id) AS market_id FROM read_parquet(?) GROUP BY asset,block ORDER BY asset,block',[str(SOURCE)]).fetchall()
    assert len(ids)==12
    rows=[]
    # Earliest paired fresh quote in EACH already chosen identity; not a price/PnL search.
    sql='''SELECT u.t,u.f_bid AS up_bid,u.f_ask AS up_ask,
      d.f_bid AS down_bid,d.f_ask AS down_ask,
      u.book_source_ms,u.book_received_ms,u.book_chain_source_max,u.book_chain_received_max
      FROM read_parquet(?) u JOIN read_parquet(?) d ON u.market_id=d.market_id AND u.t=d.t
      WHERE u.market_id=? AND u.side='UP' AND d.side='DOWN' AND u.fresh3 AND d.fresh3
      ORDER BY u.t LIMIT 1'''
    for asset,block,mid in ids:
        cur=c.execute(sql,[str(SOURCE),str(SOURCE),mid]);names=[x[0] for x in cur.description];r=cur.fetchone()
        item=dict(asset=asset,block=block,market_id=mid,quote=None)
        if r:
            q=dict(zip(names,r));assert max(q[k] for k in ['book_source_ms','book_received_ms','book_chain_source_max','book_chain_received_max'])<q['t']
            assert 0<q['up_bid']<q['up_ask']<1 and 0<q['down_bid']<q['down_ask']<1
            assert abs(q['up_bid']+q['down_ask']-1)<1e-8 and abs(q['up_ask']+q['down_bid']-1)<1e-8
            item['quote']=q
        rows.append(item)
    c.close();return rows


def exercise(r):
    if r['quote'] is None:return dict(**r,status='MISSING_PAIRED_QUOTE_NOT_REPLACED')
    q=r['quote'];asset=r['asset'];base=InitialPortfolio(120.,80.,85.)
    tight=EndpointAuthority(0.,-6.,'EXPLICIT_SYNTHETIC_BOUND_TIGHT')
    loose=EndpointAuthority(-100.,-100.,'EXPLICIT_SYNTHETIC_BOUND_LOOSE')
    # The absolute historical clock only proves quoteprovenance. Component eventclock
    # is fixed synthetic200s so noTarget event latency or availability is inferred.
    args=dict(quote_reference='HISTORICAL_QUOTE_'+str(r['market_id'])+'_'+str(q['t']),
              now_ms=200000,market_end_ms=300000)
    r_opts=[('PBEST','PASSIVE',q['down_bid']),('PDEEP','PASSIVE',round(q['down_bid']-.01,2)),('ACTIVE','ACTIVE',q['down_ask'])]
    a_opts=[('PBEST','PASSIVE',q['up_bid']),('PDEEP','PASSIVE',round(q['up_bid']-.01,2)),('ACTIVE','ACTIVE',q['up_ask'])]
    results=[]
    for rn,rr,rp in r_opts:
        for an,ar,ap in a_opts:
            orders=[ProposedOrder('R',1,rr,24.,rp),ProposedOrder('A',2,ar,18.,ap)]
            for bound,bname in [(tight,'TIGHT'),(loose,'LOOSE')]:
                L=ledger();p=preview(L,base,bound,asset,orders,**args)
                assert not L.carriers
                results.append(dict(repair_option=rn,add_option=an,bound=bname,**p))
    # Receipt script tests update semantics, not whether anyhistorical order would fill.
    L=ledger();ro=ProposedOrder('R',1,'PASSIVE',24.,round(q['down_bid']-.01,2))
    ao=ProposedOrder('A',2,'ACTIVE',18.,q['up_ask'])
    rp=preview(L,base,tight,asset,[ro],**args)
    progression=dict(repair_reservation=rp['status'],script='ARTIFICIAL20SHARE_CONFIRMED_RECEIPT_NOT_HFT')
    if rp['accepted']:
        commit_reservations(L,base,tight,asset,[ro],inspected=rp,**args)
        before=preview(L,base,tight,asset,[ao],**args)
        L.confirm_cumulative('R',20.,20.*ro.limit_price)
        after=preview(L,base,tight,asset,[ao],**args)
        progression.update(before=before['status'],after=after['status'],
            before_worst=before['projection']['coordinate_worst'] if before['projection'] else None,
            after_worst=after['projection']['coordinate_worst'] if after['projection'] else None,
            repair_remaining=L.account(1)['repair_remaining'],repair_pending=L.account(1)['reserved_qty'],
            neither_new_goal_nor_mode_switch=True)
        assert L.account(1)['repair_remaining']==4.
    return dict(asset=asset,block=r['block'],market_id=r['market_id'],quote=q,
                status='QUOTE_INPUT_EXERCISE_COMPLETE',plans=results,progression=progression)


def main():
    t0=time.monotonic();OUT.mkdir(exist_ok=True)
    if (OUT/'SCORE.json').exists():raise FileExistsError('immutable result exists')
    for p,h in PINNED.items():assert sha(p)==h,'existing helper drift: '+p
    source_rows=snapshots()
    save(OUT/'QUOTE_MANIFEST.json',dict(source=str(SOURCE),sha256=EXPECTED_SOURCE,rows=source_rows,
        cohort='one earliestmarket perasset/block;earliestpairedfreshquote,nooutcome/quantityfields',
        selected_target_labels=[],explicitlySyntheticGrantQuantities=dict(repair=24,add=18),
        moneyScope='per-intent45 total commitment in oldledger fixture,NOT a newstudent globalcapital rule'))
    smoke=[x for x in source_rows if x['block'] in ('W1','W6')];rest=[x for x in source_rows if x['block'] not in ('W1','W6')]
    a=[exercise(x) for x in smoke];assert len(a)==4
    save(OUT/'SMOKE4.json',dict(status='FUNCTIONAL_INPUT_GATE_COMPLETE_NOT_FILLS',results=a))
    results=a+[exercise(x) for x in rest]
    assert len(results)==12 and len({r['market_id'] for r in results})==12
    plans=[p for r in results for p in r.get('plans',[])];counts=Counter(p['status'] for p in plans)
    falsefull=[dict(market=r['market_id'],asset=r['asset'],r=p['repair_option'],a=p['add_option'])
      for r in results for p in r.get('plans',[]) if p.get('full_fill_within_bounds') and not p['accepted']]
    switched=[r['market_id'] for r in results if r.get('progression',{}).get('before')=='DECLARED_ENDPOINT_AUTHORITY_EXCEEDED'
              and r['progression'].get('after')=='ADMISSIBLE_RESERVATION_NOT_EXECUTION']
    for p,h in PINNED.items():assert sha(p)==h
    out=dict(version='PAIR_CORE_AUTHORIZED_OUTCOME_SMOKE_V1',status='CONDITIONAL_CONTROL_COMPONENT_ONLY',
      quoteMarkets=12,quoteSmoke4=4,quoteStage12IncludesSmoke=True,syntheticPlanEvaluations=len(plans),
      rejectedPlansAreNotTargetHOLD=dict(counts),fullFillOnlyWouldPassButIndependentFillRejected=falsefull,
      partialRepairEnablesExistingAddBeforeCompletion=switched,results=results,
      sourceSha256=EXPECTED_SOURCE,oldHelpersUnchanged=PINNED,
      newHFT=0,modelFits=0,newTraining=0,nativeOrders=0,realizedStrategyPnl=None,
      learnedAuthoritySource=False,targetPrivateMechanismVerified=False,
      originalStudentFundingAndRuntimeChanged=False,
      limitations=['all initialportfolio/endpointauthority/qty/receipts are synthetic testinputs',
       'historicalquotes give diverse inputvalues,not unseenHFT or12independentstrategytrials',
       'worstcase uses no probability,allremainingfills independent;may be moreconservative than a correlated/exchange-contingentexecution system',
       'oldPassive4/Active1 capability intentionally unchanged;2activeplan capacityfailure is not an economicdecision',
       'nativeActive in the separate minimalstudent remains unsupported;no workerjob was launched',
       'no privateTarget goals, risklimits or futurePnL inferred;correctness not profitablepolicy'],seconds=time.monotonic()-t0)
    save(OUT/'SCORE.json',out)
    print(json.dumps(dict(score=str(OUT/'SCORE.json'),sha256=sha(OUT/'SCORE.json'),seconds=out['seconds'],
      quotes=12,planEvaluations=len(plans),statusCounts=dict(counts),fullOnlyFalsePass=len(falsefull),
      partialEnables=switched,progressions=[dict(asset=r['asset'],block=r['block'],market=r['market_id'],**r.get('progression',{})) for r in results]),ensure_ascii=False))


if __name__=='__main__':main()
