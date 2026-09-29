from __future__ import annotations
import json, math, sqlite3, zlib
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'data'/'wallet_maker_book_inference.db'
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'/'public_maker_fill_proxy_audit_v1_report.json'
REST_MS=250; HORIZON_MS=1000; EPS=1e-9

def dec(x): return json.loads(zlib.decompress(x).decode()) if x else None
def apply(st,ch):
 if not isinstance(ch,dict):return
 for k in ('bids','asks'):
  for z in ch.get(k,[]) or []:
   p=float(z['price']); a=float(z['after'])
   if a<=1e-12:st[k].pop(p,None)
   else:st[k][p]=a

def met(rows,key):
 y=[int(r['y']) for r in rows]; p=[int(r[key]) for r in rows]; tp=sum(a and b for a,b in zip(y,p)); fp=sum((not a) and b for a,b in zip(y,p)); fn=sum(a and (not b) for a,b in zip(y,p)); tn=len(y)-tp-fp-fn
 prec=tp/(tp+fp) if tp+fp else None; rec=tp/(tp+fn) if tp+fn else None; f1=2*prec*rec/(prec+rec) if prec is not None and rec is not None and prec+rec else None
 return {'n':len(y),'positives':sum(y),'positiveRate':sum(y)/len(y) if y else None,'triggerRate':sum(p)/len(p) if p else None,'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':prec,'recall':rec,'f1':f1}

def main():
 c=sqlite3.connect(BOOK); c.row_factory=sqlite3.Row
 mids=[int(r[0]) for r in c.execute('''select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null and first_target_ms is not null order by market_id desc limit 350''')]
 rows=[]; market_count=0
 for ii,mid in enumerate(sorted(mids),1):
  ps=[dict(r) for r in c.execute('''select parent_id,target_side,target_price,placement_first_ms,first_target_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null and first_target_ms is not null and first_target_ms-placement_first_ms>=? order by placement_first_ms,parent_id''',(mid,REST_MS))]
  if not ps:continue
  updates=list(c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
  if not updates:continue
  market_count+=1; st={'bids':{},'asks':{}}; pi=0; active=[]
  for u in updates:
   t=int(u['source_timestamp_ms'])
   # add placements strictly before/equal this update using current strict-past state
   while pi<len(ps) and int(ps[pi]['placement_first_ms'])<=t:
    p=ps[pi]; side=str(p['target_side']); q=float(p['target_price']); native_side='bids' if side=='UP' else 'asks'; native_price=q if side=='UP' else round(1.0-q,10)
    depth=float(st[native_side].get(native_price,0.0)); active.append({'p':p,'native_side':native_side,'native_price':native_price,'initial_depth':depth,'ask_touch':False,'pass_through':False,'any_depletion':False,'level_zero':False,'queue_cleared':False,'cum_depletion':0.0}); pi+=1
   ch=dec(u['changes_z']) or {} if not int(u['is_checkpoint']) else None
   if int(u['is_checkpoint']): st={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
   else: apply(st,ch)
   bb=max(st['bids']) if st['bids'] else None; ba=min(st['asks']) if st['asks'] else None
   keep=[]
   for a in active:
    p=a['p']; start=int(p['placement_first_ms']); end=start+HORIZON_MS
    if t<start+REST_MS: keep.append(a); continue
    if t<=end:
     side=str(p['target_side']); q=float(p['target_price'])
     if bb is not None and ba is not None:
      obid=bb if side=='UP' else 1.0-ba; oask=ba if side=='UP' else 1.0-bb
      if oask<=q+EPS:a['ask_touch']=True
      if obid<q-EPS:a['pass_through']=True
     if ch:
      for z in ch.get(a['native_side'],[]) or []:
       if abs(float(z['price'])-float(a['native_price']))<=1e-9 and float(z['delta'])<0:
        a['any_depletion']=True; a['cum_depletion']+=-float(z['delta'])
        if float(z['after'])<=1e-12:a['level_zero']=True
     if a['cum_depletion']>=float(a['initial_depth'])-EPS and a['initial_depth']>EPS:a['queue_cleared']=True
     keep.append(a)
    else:
     y=int(int(p['first_target_ms'])<=end)
     rows.append({'market_id':mid,'y':y,'ask_touch':int(a['ask_touch']),'pass_through':int(a['pass_through']),'any_depletion':int(a['any_depletion']),'level_zero':int(a['level_zero']),'queue_cleared':int(a['queue_cleared']),'depletion_or_pass':int(a['any_depletion'] or a['pass_through']),'queueclear_or_pass':int(a['queue_cleared'] or a['pass_through']),'initial_depth':a['initial_depth'],'cum_depletion':a['cum_depletion']})
   active=keep
  # finalize parents whose horizon ends after last update only if enough tape exists
  last_t=int(updates[-1]['source_timestamp_ms'])
  for a in active:
   p=a['p']; end=int(p['placement_first_ms'])+HORIZON_MS
   if last_t>=end:
    y=int(int(p['first_target_ms'])<=end); rows.append({'market_id':mid,'y':y,'ask_touch':int(a['ask_touch']),'pass_through':int(a['pass_through']),'any_depletion':int(a['any_depletion']),'level_zero':int(a['level_zero']),'queue_cleared':int(a['queue_cleared']),'depletion_or_pass':int(a['any_depletion'] or a['pass_through']),'queueclear_or_pass':int(a['queue_cleared'] or a['pass_through']),'initial_depth':a['initial_depth'],'cum_depletion':a['cum_depletion']})
  if ii%50==0: print(json.dumps({'progressMarkets':ii,'rows':len(rows)}),flush=True)
 keys=['ask_touch','pass_through','any_depletion','level_zero','queue_cleared','depletion_or_pass','queueclear_or_pass']
 rep={'reportVersion':'PUBLIC_MAKER_FILL_PROXY_AUDIT_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'definition':{'sample':'high-confidence anchored Target Maker parents, rest>=250ms','label':'actual Target first fill occurs by placement+1s','proxies':'public-book future path only; no Target future value enters proxy'},'coverage':{'marketsRequested':len(mids),'markets':market_count,'rows':len(rows)},'metrics':{k:met(rows,k) for k in keys},'guards':['This uses Target actual fill only as teacher evaluation truth.','Negative public depth change mixes trades and cancels; depletion is not a venue-confirmed execution tape.','Do not promote a proxy solely on recall; false-positive rate matters for PnL replay.']}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
 c.close()
if __name__=='__main__':main()
