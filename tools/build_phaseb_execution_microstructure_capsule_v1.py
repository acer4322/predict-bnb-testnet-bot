from __future__ import annotations

"""Build strict-causal Phase-B execution microstructure features from Execution Tape.

OUR simulator decisions are invoked after the feed event at the same receipt clock,
so the causal book contract here is receivedMs <= decision_ms. This differs from
external Target-action Decision Seam's stricter < action_ms ordering boundary.

No branch outcome/future label is read. Raw Predict matches use the canonical
second-granular MID offset policy (executedAt second + 500ms) for past-flow features.
"""
import argparse,datetime,hashlib,json,lzma,math,os,tempfile,zipfile
from pathlib import Path
from typing import Any
import duckdb

TICK=.01;EPS=1e-9
UPD_WINDOWS=(8,32);TRADE_WINDOWS=(8,32)
def safe(x,d=0.0):
 try:z=float(x);return z if math.isfinite(z) else d
 except:return d
def qpath(p:Path):return p.resolve().as_posix().replace("'","''")
def sha(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def iso_ms(s):
 dt=datetime.datetime.fromisoformat(str(s).replace('Z','+00:00'))
 if dt.tzinfo is None:dt=dt.replace(tzinfo=datetime.timezone.utc)
 return int(dt.timestamp()*1000)
def wei(v):return float(int(str(v)))/1e18
def norm_match(r):
 try:
  tak=r.get('taker') if isinstance(r.get('taker'),dict) else {};o=tak.get('outcome') if isinstance(tak.get('outcome'),dict) else {};name=str(o.get('name') or '').upper();qt=str(tak.get('quoteType') or '').upper()
  if name not in {'UP','DOWN','YES','NO'} or qt not in {'BID','ASK'}:return None
  px=wei(r.get('priceExecuted'));qty=wei(r.get('amountFilled'))
  if qty<=0 or not 0<px<1:return None
  isup=name in {'UP','YES'};native=px if isup else 1-px;agg=('BUY' if qt=='BID' else 'SELL') if isup else ('SELL' if qt=='BID' else 'BUY')
  return {'effectiveMs':iso_ms(r.get('executedAt'))+500,'qty':qty,'nativePrice':round(native,12),'aggressor':agg}
 except:return None
def apply_update(u,bids,asks):
 if int(u[3])==1 and u[4] is not None and u[5] is not None:
  bids.clear();asks.clear();bids.update({round(float(k),10):float(v) for k,v in u[4].items()});asks.update({round(float(k),10):float(v) for k,v in u[5].items()});return
 ch=u[6] or {}
 for key,book in [('bids',bids),('asks',asks)]:
  for it in ch.get(key,[]) or []:
   p,b,a,d=map(float,it);p=round(p,10)
   if a<=EPS:book.pop(p,None)
   else:book[p]=a
def depth(book,p):return float(book.get(round(float(p),10),0.0))
def outcome_quote(side,bids,asks):
 nb=max(bids) if bids else None;na=min(asks) if asks else None
 if side=='UP':return nb,na
 return (None if na is None else 1-na),(None if nb is None else 1-nb)
def native(side,price):return ('BUY',round(float(price),2)) if side=='UP' else ('SELL',round(1-float(price),2))
def ordered_depths(book,buy):return sorted(book.items(),key=lambda x:x[0],reverse=buy)
def micro(row,bids,asks,updates_used,trades):
 side=str(row['action_side']);actp=safe(row['action_price']);ns,np=native(side,actp);buy=ns=='BUY';same=bids if buy else asks;opp=asks if buy else bids;slev=ordered_depths(same,buy);olev=ordered_depths(opp,not buy)
 bests=slev[0] if slev else (None,0.0);besto=olev[0] if olev else (None,0.0);queue=depth(same,np)
 better=[(p,q) for p,q in same.items() if (p>np+EPS if buy else p<np-EPS)];worse={n:sum(q for p,q in same.items() if (np-n*TICK-EPS<=p<=np+EPS if buy else np-EPS<=p<=np+n*TICK+EPS)) for n in (1,2,4)}
 r={'micro_native_side_buy':1 if buy else 0,'micro_native_price':np,'micro_queue_ahead_qty':queue,'micro_same_best_price':None if bests[0] is None else float(bests[0]),'micro_same_best_depth':float(bests[1]),'micro_opp_best_price':None if besto[0] is None else float(besto[0]),'micro_opp_best_depth':float(besto[1]),'micro_same_top3_depth':sum(q for _,q in slev[:3]),'micro_same_top5_depth':sum(q for _,q in slev[:5]),'micro_opp_top3_depth':sum(q for _,q in olev[:3]),'micro_opp_top5_depth':sum(q for _,q in olev[:5]),'micro_better_same_depth':sum(q for _,q in better),'micro_better_same_levels':len(better),'micro_worse_within1tick_depth':worse[1],'micro_worse_within2tick_depth':worse[2],'micro_worse_within4tick_depth':worse[4],'micro_native_spread_ticks':((min(asks)-max(bids))/TICK if bids and asks else None),'micro_candidate_from_same_best_ticks':(abs(np-bests[0])/TICK if bests[0] is not None else None),'micro_book_levels_bid':len(bids),'micro_book_levels_ask':len(asks),'micro_current_order_count':int(updates_used[-1][2]) if updates_used else None,'micro_current_update_age_ms':int(row['decision_ms'])-int(updates_used[-1][1]) if updates_used else None}
 for n in UPD_WINDOWS:
  us=updates_used[-n:];sam='bids' if buy else 'asks';opk='asks' if buy else 'bids';sa=sn=sp=oa=ca=cn=cp=0.0;cc=0
  for u in us:
   ch=u[6] or {}
   for it in ch.get(sam,[]) or []:
    p,b,a,d=map(float,it);sa+=abs(d);sn+=max(0,-d);sp+=max(0,d)
    if abs(p-np)<1e-9:ca+=abs(d);cn+=max(0,-d);cp+=max(0,d);cc+=1
   for it in ch.get(opk,[]) or []:oa+=abs(float(it[3]))
  r.update({f'micro_u{n}_same_abs_delta':sa,f'micro_u{n}_same_negative_delta':sn,f'micro_u{n}_same_positive_delta':sp,f'micro_u{n}_opp_abs_delta':oa,f'micro_u{n}_candidate_abs_delta':ca,f'micro_u{n}_candidate_negative_delta':cn,f'micro_u{n}_candidate_positive_delta':cp,f'micro_u{n}_candidate_change_events':cc,f'micro_u{n}_order_count_delta':(int(us[-1][2])-int(us[0][2]) if len(us)>=2 else 0),'micro_u'+str(n)+'_span_ms':(int(us[-1][1])-int(us[0][1]) if len(us)>=2 else 0)})
 past=[t for t in trades if int(t['effectiveMs'])<=int(row['decision_ms'])];fillagg='SELL' if buy else 'BUY'
 for n in TRADE_WINDOWS:
  ts=past[-n:];tot=sum(t['qty'] for t in ts);fq=sum(t['qty'] for t in ts if t['aggressor']==fillagg);through=sum(t['qty'] for t in ts if t['aggressor']==fillagg and (t['nativePrice']<=np+EPS if buy else t['nativePrice']>=np-EPS));last=ts[-1]['effectiveMs'] if ts else None;support=[t for t in ts if t['aggressor']==fillagg]
  r.update({f'micro_t{n}_trade_count':len(ts),f'micro_t{n}_trade_qty':tot,f'micro_t{n}_fill_aggressor_qty':fq,f'micro_t{n}_fill_aggressor_share':fq/tot if tot>EPS else 0.0,f'micro_t{n}_at_or_through_candidate_qty':through,f'micro_t{n}_last_trade_age_ms':None if last is None else int(row['decision_ms'])-int(last),f'micro_t{n}_last_fill_aggressor_age_ms':None if not support else int(row['decision_ms'])-int(support[-1]['effectiveMs'])})
 return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True,type=Path);ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--output-dir',required=True);ap.add_argument('--expected-markets',type=int,default=100);a=ap.parse_args();outdir=(Path(os.environ['BTC5M_LAN_RESULT_DIR']) if a.output_dir.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output_dir));outdir.mkdir(parents=True,exist_ok=True);op=outdir/'phaseb_execution_microstructure_v1.parquet';mp=outdir/'phaseb_execution_microstructure_v1.manifest.json'
 c=duckdb.connect(database=':memory:');cur=c.execute("select market_id,decision_ms,pair_id,action_class,action_side,action_price,action_qty,context_side_bid,context_side_ask from read_parquet('"+qpath(a.phaseb)+"') order by market_id,decision_ms,action_class");cols=[x[0] for x in cur.description];rows=[dict(zip(cols,x)) for x in cur.fetchall()];c.close();by={}
 for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
 out=[];mismatch=[];future=[];sameclock=0;tradecount=0
 with zipfile.ZipFile(a.bundle) as z:
  for mi,(mid,rr) in enumerate(sorted(by.items()),1):
   tape=json.loads(lzma.decompress(z.read(f'tapes/{mid}.json.xz')));ups=sorted(tape.get('updates') or [],key=lambda x:(int(x[1]),int(x[0])));tr=[x for x in (norm_match(q) for q in tape.get('matches') or []) if x is not None];tr.sort(key=lambda x:x['effectiveMs']);tradecount+=len(tr);bids={};asks={};i=0
   for t in sorted({int(x['decision_ms']) for x in rr}):
    while i<len(ups) and int(ups[i][1])<=t:apply_update(ups[i],bids,asks);i+=1
    used=ups[:i]
    if used and int(used[-1][1])==t:sameclock+=1
    if any(int(u[1])>t for u in used):future.append((mid,t))
    for r in [x for x in rr if int(x['decision_ms'])==t]:
     qb,qa=outcome_quote(str(r['action_side']),bids,asks)
     if qb is None or qa is None or abs(float(qb)-safe(r['context_side_bid']))>1e-8 or abs(float(qa)-safe(r['context_side_ask']))>1e-8:mismatch.append({'marketId':mid,'t':t,'class':r['action_class'],'teacher':[r['context_side_bid'],r['context_side_ask']],'recon':[qb,qa]})
     base={k:r[k] for k in ('market_id','decision_ms','pair_id','action_class','action_side','action_price','action_qty')};base.update(micro(r,bids,asks,used,tr));out.append(base)
   print(json.dumps({'progress':mi,'of':len(by),'marketId':mid,'rows':len(rr),'updates':len(ups),'trades':len(tr)},ensure_ascii=False),flush=True)
 with tempfile.TemporaryDirectory(prefix='microcap_') as td:
  jp=Path(td)/'rows.jsonl'
  with jp.open('w',encoding='utf-8') as f:
   for r in out:f.write(json.dumps(r,ensure_ascii=False)+'\n')
  c=duckdb.connect(database=':memory:');c.execute("copy (select * from read_json_auto('"+qpath(jp)+"',format='newline_delimited') order by market_id,decision_ms,action_class) to '"+qpath(op)+"' (format parquet,compression zstd)");desc=[x[0] for x in c.execute("describe select * from read_parquet('"+qpath(op)+"')").fetchall()];n=int(c.execute("select count(*) from read_parquet('"+qpath(op)+"')").fetchone()[0]);c.close()
 checks={'expectedRows':n==2*int(a.expected_markets),'expectedMarkets':len(by)==int(a.expected_markets),'bookQuoteParity':not mismatch,'noFutureBookUpdate':not future,'sameClockFeedObserved':sameclock>0,'labelFree':not any(x.startswith('label_') for x in desc)};man={'version':'PHASEB_EXECUTION_MICROSTRUCTURE_CAPSULE_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'rows':n,'markets':len(by),'columns':desc,'checks':checks,'promotionGate':{'pass':all(checks.values()),'required':list(checks)},'bookClockSemantics':'OUR simulator: feed event at receipt clock is processed before decision; reconstruct updates with receivedMs <= decision_ms','tradeClockSemantics':'raw Predict matches are second-granular and mapped to canonical MID offset executedAt +500ms; only effectiveMs <= decision_ms included','updateWindows':UPD_WINDOWS,'tradeWindows':TRADE_WINDOWS,'sameClockDecisionCount':sameclock,'normalizedTradeRows':tradecount,'diagnostics':{'bookQuoteMismatch':mismatch[:20],'futureBookUpdates':future[:20]},'boundary':['derived execution research cache only; raw tape remains truth','negative depth is churn only, never synthetic trade','no branch outcome/future label read','event-count update/trade windows are representation, not fixed-time strategy rules','no winner/Target future/no NEW24-B/no 8781']};mp.write_text(json.dumps(man,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'rows':n,'parquetBytes':op.stat().st_size,'checks':checks,'promotionPass':all(checks.values()),'sameClockDecisions':sameclock,'normalizedTrades':tradecount,'output':str(op),'manifest':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
