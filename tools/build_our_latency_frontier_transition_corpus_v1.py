from __future__ import annotations

"""Build native OUR passive decision -> arrival-frontier transition corpus.

Decision microstructure is causal at the current V3B decision receipt clock.
Arrival microstructure at decision+entryLatencyMs is FUTURE offline supervision only.
No branch outcome, PnL, winner, Target action or HFT fill label is read.
"""
import argparse,json,lzma,os,tempfile,zipfile
from pathlib import Path
import duckdb
from build_phaseb_execution_microstructure_capsule_v1 import apply_update,micro,norm_match,outcome_quote,qpath


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True,type=Path);ap.add_argument('--our',required=True,type=Path);ap.add_argument('--output-dir',required=True);ap.add_argument('--entry-latency-ms',type=int,default=1092);ap.add_argument('--expected-rows',type=int,default=11917);ap.add_argument('--expected-markets',type=int,default=100);a=ap.parse_args()
 outdir=Path(os.environ['BTC5M_LAN_RESULT_DIR']) if a.output_dir.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output_dir);outdir.mkdir(parents=True,exist_ok=True);op=outdir/'our_latency_frontier_transition_v1.parquet';mp=outdir/'our_latency_frontier_transition_v1.manifest.json'
 c=duckdb.connect(database=':memory:');p=qpath(a.our);cur=c.execute("select market_id,decision_ms,carrier_key,window_end_ms,state_timing,candidate_already_in_state,context_side_bid,context_side_ask,action_side,action_role,action_route,action_price,action_qty,action_price_to_bid,action_ask_to_price,action_pair_legal from read_parquet('"+p+"') where action_route='PASSIVE' order by window_end_ms,market_id,decision_ms,carrier_key");cols=[x[0] for x in cur.description];rows=[dict(zip(cols,r)) for r in cur.fetchall()];c.close();by={}
 for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
 out=[];quote_mismatch=[];decision_future=[];arrival_future=[];arrival_after_decision=0;decision_sameclock=0;trades_total=0
 with zipfile.ZipFile(a.bundle) as z:
  for mi,(mid,rr) in enumerate(sorted(by.items()),1):
   tape=json.loads(lzma.decompress(z.read(f'tapes/{mid}.json.xz')));ups=sorted(tape.get('updates') or [],key=lambda x:(int(x[1]),int(x[0])));tr=[x for x in (norm_match(q) for q in tape.get('matches') or []) if x is not None];tr.sort(key=lambda x:x['effectiveMs']);trades_total+=len(tr)
   db,da={},{};ab,aa={},{};di=ai=0
   for r in rr:
    t=int(r['decision_ms']);front=t+int(a.entry_latency_ms)
    while di<len(ups) and int(ups[di][1])<=t:apply_update(ups[di],db,da);di+=1
    while ai<len(ups) and int(ups[ai][1])<=front:apply_update(ups[ai],ab,aa);ai+=1
    du=ups[max(0,di-32):di];au=ups[max(0,ai-32):ai]
    if du and int(du[-1][1])==t:decision_sameclock+=1
    if any(int(u[1])>t for u in du):decision_future.append((mid,t))
    if any(int(u[1])>front for u in au):arrival_future.append((mid,t,front))
    if au and int(au[-1][1])>t:arrival_after_decision+=1
    qb,qa=outcome_quote(str(r['action_side']),db,da)
    if qb is None or qa is None or abs(float(qb)-float(r['context_side_bid']))>1e-8 or abs(float(qa)-float(r['context_side_ask']))>1e-8:
     quote_mismatch.append({'marketId':mid,'decisionMs':t,'carrierKey':r['carrier_key'],'teacher':[r['context_side_bid'],r['context_side_ask']],'recon':[qb,qa]})
    base={k:r[k] for k in ('market_id','decision_ms','carrier_key','window_end_ms','state_timing','candidate_already_in_state','action_side','action_role','action_route','action_price','action_qty','action_price_to_bid','action_ask_to_price','action_pair_legal')}
    dm=micro(r,db,da,du,tr);base.update(dm)
    ar=dict(r);ar['decision_ms']=front;am=micro(ar,ab,aa,au,tr);base.update({'oracle_arrival_'+k[len('micro_'):]:v for k,v in am.items()});out.append(base)
   print(json.dumps({'progress':mi,'of':len(by),'marketId':mid,'rows':len(rr),'updates':len(ups),'trades':len(tr)},ensure_ascii=False),flush=True)
 with tempfile.TemporaryDirectory(prefix='our_frontier_') as td:
  jp=Path(td)/'rows.jsonl'
  with jp.open('w',encoding='utf-8') as f:
   for r in out:f.write(json.dumps(r,ensure_ascii=False)+'\n')
  c=duckdb.connect(database=':memory:');c.execute("copy (select * from read_json_auto('"+qpath(jp)+"',format='newline_delimited') order by window_end_ms,market_id,decision_ms,carrier_key) to '"+qpath(op)+"' (format parquet,compression zstd)");desc=[x[0] for x in c.execute("describe select * from read_parquet('"+qpath(op)+"')").fetchall()];n=int(c.execute("select count(*) from read_parquet('"+qpath(op)+"')").fetchone()[0]);markets=int(c.execute("select count(distinct market_id) from read_parquet('"+qpath(op)+"')").fetchone()[0]);c.close()
 checks={'expectedRows':n==int(a.expected_rows),'expectedMarkets':markets==int(a.expected_markets),'decisionBookQuoteParity':not quote_mismatch,'noDecisionFutureBookUpdate':not decision_future,'noArrivalBookUpdatePastFrontier':not arrival_future,'arrivalFutureObserved':arrival_after_decision>0,'passiveOnly':all(str(r['action_route'])=='PASSIVE' for r in out),'labelFree':not any(x.startswith('label_') for x in desc),'oracleArrivalClearlyNamed':all((not x.startswith('oracle_')) or x.startswith('oracle_arrival_') for x in desc)}
 man={'version':'OUR_LATENCY_FRONTIER_TRANSITION_CORPUS_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'futureOracleSupervision':True,'entryLatencyMs':int(a.entry_latency_ms),'rows':n,'markets':markets,'columns':desc,'checks':checks,'promotionGate':{'pass':all(checks.values()),'required':list(checks)},'decisionClockSemantics':'OUR simulator consumes same receipt-clock feed update before decision; decision micro uses receivedMs <= decision_ms','arrivalClockSemantics':'OFFLINE FUTURE SUPERVISION ONLY: receivedMs <= decision_ms + entryLatencyMs','tradeClockSemantics':'raw Predict matches use canonical executedAt +500ms MID offset; decision/arrival micro uses only trades at or before its respective frontier','decisionSameClockRows':decision_sameclock,'arrivalRowsWithPostDecisionBookUpdate':arrival_after_decision,'normalizedTradeRows':trades_total,'diagnostics':{'quoteMismatch':quote_mismatch[:20],'decisionFuture':decision_future[:20],'arrivalFuture':arrival_future[:20]},'boundary':['PASSIVE native current-V3B actions only','future arrival features are labels/supervision for transition learning, NEVER direct runtime features','negative L2 depth is churn only, never synthetic trade','no HFT fill outcome/branch value/winner/Target action read','1092ms measured execution latency, not strategy timing rule','no NEW24-B/no 8781']};mp.write_text(json.dumps(man,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'rows':n,'markets':markets,'parquetBytes':op.stat().st_size,'checks':checks,'promotionPass':all(checks.values()),'output':str(op),'manifest':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
