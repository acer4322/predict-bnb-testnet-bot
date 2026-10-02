from __future__ import annotations
import argparse,json,sqlite3,zlib,math,statistics
from pathlib import Path

EPS=1e-9; GRID=.01

def dec(b):
 if not b:return None
 try:return json.loads(zlib.decompress(b).decode('utf-8'))
 except Exception:return None

def apply_changes(book,ch):
 if not isinstance(ch,dict):return
 for key in ('bids','asks'):
  vals=ch.get(key)
  if not isinstance(vals,list):continue
  for x in vals:
   if isinstance(x,dict):
    try:p=float(x.get('price'));after=x.get('after');delta=x.get('delta')
    except Exception:continue
    if after is not None:
     try:a=float(after)
     except Exception:continue
    elif delta is not None:
     try:a=float(book[key].get(p,0.0))+float(delta)
     except Exception:continue
    else:continue
   elif isinstance(x,(list,tuple)) and len(x)>=3:
    try:p=float(x[0]);a=float(x[2])
    except Exception:continue
   else:continue
   if a<=EPS:book[key].pop(p,None)
   else:book[key][p]=a

def load_states(c,mid,start,end):
 anchor=c.execute('select received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms<=? and is_checkpoint=1 order by received_at_ms desc,id desc limit 1',(mid,int(start))).fetchone()
 rows=list(c.execute('select received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and received_at_ms>? and received_at_ms<=? order by received_at_ms,id',(mid,int(anchor[0]) if anchor else int(start)-60000,int(end))))
 stream=[]
 if anchor is not None:stream.append(anchor)
 stream.extend(rows)
 book={'bids':{},'asks':{}};out=[]
 for r in stream:
  t=int(r[0])
  if int(r[1]):
   book={'bids':{float(k):float(v) for k,v in (dec(r[2]) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r[3]) or {}).items()}}
  else:apply_changes(book,dec(r[4]) or {})
  out.append((t,dict(book['bids']),dict(book['asks'])))
 return out

def target_bid(bids,asks,side):
 if not bids or not asks:return None
 return float(max(bids)) if side=='UP' else 1.0-float(min(asks))

def target_ask(bids,asks,side):
 if not bids or not asks:return None
 return float(min(asks)) if side=='UP' else 1.0-float(max(bids))

def level_depth(bids,asks,side,price):
 if side=='UP':return float(bids.get(round(float(price),12),bids.get(float(price),0.0)) or 0.0)
 np=round(1.0-float(price),12);return float(asks.get(np,asks.get(1.0-float(price),0.0)) or 0.0)

def state_metric(bids,asks,side,first_price):
 ceiling=1.0-float(first_price)-0.01;bb=target_bid(bids,asks,side);ba=target_ask(bids,asks,side)
 if bb is None or ba is None:return None
 raw=min(ceiling,float(ba)-0.01);p=math.floor((raw+1e-10)*100.0)/100.0
 if p<=0:return None
 return {'economicCeiling':ceiling,'bestBid':bb,'bestAsk':ba,'legalPassivePrice':p,'ceilingBehindTicks':max(0.0,(bb-ceiling)/GRID),'legalBehindTicks':max(0.0,(bb-p)/GRID)}

def load_result_rows(paths):
 out={}
 for p in paths:
  d=json.load(open(p,encoding='utf-8'))
  for r in d.get('rows',[]):out[int(r['marketId'])]=r
 return out

def match_first_fills(row):
 v=row['V23'];fun=v['functional'];ca=v['causal'];subs=[dict(x) for x in ca.get('submitTrace',[]) if x.get('lane')=='RESERVE_BUILD_FIRST'];fills=[dict(x) for x in ca.get('fillTrace',[])]
 used=set();matches=[]
 for i,s in enumerate(subs):
  nt=int(subs[i+1]['t']) if i+1<len(subs) else 10**30
  cand=[]
  for j,f in enumerate(fills):
   if j in used:continue
   if int(f['t'])<int(s['t']) or int(f['t'])>=nt:continue
   if f.get('side')!=s.get('side'):continue
   if abs(float(f.get('price') or 0)-float(s.get('price') or 0))>1e-8:continue
   cand.append((int(f['t']),j,f))
  if cand:
   _,j,f=min(cand);used.add(j);matches.append({'submit':s,'fill':f})
 expected=int(fun.get('reserveFirstLegActualFill') or 0)
 return matches,expected,subs,fills

def qtile(xs,q):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 z=(len(xs)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return xs[lo]*(1-w)+xs[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def block(rr):
 return {'cycles':len(rr),'markets':len({r['marketId'] for r in rr}),'submitLegalBehindTicks':stats([r.get('submitLegalBehindTicks') for r in rr]),'reserveSubmitToFillMs':stats([r.get('reserveSubmitToFillMs') for r in rr]),'submitHeadroomTicks':stats([r.get('submitHeadroomTicks') for r in rr]),'firstHeadroomDeteriorationMs':stats([r.get('firstHeadroomDeteriorationMs') for r in rr]),'headroomDeteriorationToFillMs':stats([r.get('headroomDeteriorationToFillMs') for r in rr]),'headroomDeteriorationBeforeFirstDepthDropRate':sum(bool(r.get('headroomDeteriorationBeforeFirstDepthDrop')) for r in rr)/len(rr) if rr else None,'firstDepthDropBeforeHeadroomDeteriorationRate':sum(bool(r.get('firstDepthDropBeforeHeadroomDeterioration')) for r in rr)/len(rr) if rr else None,'submitToPathBreakMs':stats([r.get('submitToPathBreakMs') for r in rr]),'pathBreakToFirstFillMs':stats([r.get('pathBreakToFirstFillMs') for r in rr]),'pathBreakBeforeFillRate':sum(r.get('pathBreakToFirstFillMs') is not None for r in rr)/len(rr) if rr else None,'firstLegAtBestReceiptFractionBeforeFill':stats([r.get('firstLegAtBestReceiptFractionBeforeFill') for r in rr]),'firstLegMaxBehindTicksBeforeFill':stats([r.get('firstLegMaxBehindTicksBeforeFill') for r in rr]),'firstLegInitialDepth':stats([r.get('firstLegInitialDepth') for r in rr]),'firstLegDepletionFractionBeforeFill':stats([r.get('firstLegDepletionFractionBeforeFill') for r in rr]),'firstLegFirstDepthDropMs':stats([r.get('firstLegFirstDepthDropMs') for r in rr]),'firstLegDepthDropBeforePathBreakRate':sum(bool(r.get('firstLegDepthDropBeforePathBreak')) for r in rr)/len(rr) if rr else None,'pathBreakBeforeFirstLegDepthDropRate':sum(bool(r.get('pathBreakBeforeFirstLegDepthDrop')) for r in rr)/len(rr) if rr else None,'firstLegalBehindTicks':stats([r['firstLegalBehindTicks'] for r in rr]),'minLegalBehindTicks':stats([r['minLegalBehindTicks'] for r in rr]),'maxLegalBehindTicks':stats([r['maxLegalBehindTicks'] for r in rr]),'within1ReceiptFraction':stats([r['within1ReceiptFraction'] for r in rr]),'within2ReceiptFraction':stats([r['within2ReceiptFraction'] for r in rr]),'firstToWithin1Ms':stats([r['firstToWithin1Ms'] for r in rr]),'firstToFar5Ms':stats([r['firstToFar5Ms'] for r in rr]),'packageRepairFillLagMs':stats([r['packageRepairFillLagMs'] for r in rr]),'eventualRepairFillLagMs':stats([r['eventualRepairFillLagMs'] for r in rr])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inputs',nargs='+',required=True);ap.add_argument('--db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--output',required=True);a=ap.parse_args();by=load_result_rows(a.inputs);c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True)
 rows=[];audit=[]
 try:
  for mid,row in sorted(by.items()):
   matches,expected,subs,fills=match_first_fills(row);audit.append({'marketId':mid,'expectedFirstFills':expected,'matchedFirstFills':len(matches),'reserveFirstSubmits':len(subs),'fillTraceRows':len(fills)})
   for k,m in enumerate(matches):
    ff=m['fill'];s=m['submit'];t=int(ff['t']);first_side=str(ff['side']);opp='DOWN' if first_side=='UP' else 'UP';fp=float(ff['price']);next_first_bound=int(matches[k+1]['fill']['t']) if k+1<len(matches) else 10**30;end=min(t+30000,next_first_bound-1)
    submit_t=int(s['t']);states=load_states(c,mid,min(submit_t,t)-2000,end);pre_submit=[x for x in states if x[0]<=submit_t];pre=[x for x in states if x[0]<=t];path=[x for x in states if t<=x[0]<=end]
    if pre:path=[pre[-1]]+path
    submit_metric=None
    if pre_submit:submit_metric=state_metric(pre_submit[-1][1],pre_submit[-1][2],opp,fp)
    prefill_metrics=[];first_leg_prefill=[];first_leg_depth=[]
    for tt,bids,asks in states:
     if tt<submit_t or tt>t:continue
     mm=state_metric(bids,asks,opp,fp)
     if mm is not None:prefill_metrics.append((tt,mm))
     fbb=target_bid(bids,asks,first_side)
     if fbb is not None:first_leg_prefill.append((tt,max(0.0,(float(fbb)-fp)/GRID)))
     first_leg_depth.append((tt,level_depth(bids,asks,first_side,fp)))
    path_break_times=[tt for tt,mm in prefill_metrics if mm['legalBehindTicks']>EPS]
    first_path_break=min(path_break_times) if path_break_times else None
    submit_headroom=((float(submit_metric['economicCeiling'])-float(submit_metric['bestBid']))/GRID) if submit_metric else None
    headroom_deterioration=[]
    if submit_headroom is not None:
     for tt,mm in prefill_metrics:
      hh=(float(mm['economicCeiling'])-float(mm['bestBid']))/GRID
      if hh<submit_headroom-EPS:headroom_deterioration.append(tt)
    first_headroom_det=min(headroom_deterioration) if headroom_deterioration else None
    init_depth=first_leg_depth[0][1] if first_leg_depth else None
    depth_drop_times=[]
    if first_leg_depth:
     prev=first_leg_depth[0][1]
     for tt,dd in first_leg_depth[1:]:
      if dd<prev-EPS:depth_drop_times.append(tt)
      prev=dd
    first_depth_drop=min(depth_drop_times) if depth_drop_times else None
    min_depth=min((x[1] for x in first_leg_depth),default=None)
    metrics=[]
    for tt,bids,asks in path:
     mm=state_metric(bids,asks,opp,fp)
     if mm is not None:metrics.append((tt,mm))
    if not metrics:continue
    first=metrics[0][1];beh=[x[1]['legalBehindTicks'] for x in metrics];within1=[tt for tt,x in metrics if x['legalBehindTicks']<=1.0+EPS];far5=[tt for tt,x in metrics if x['legalBehindTicks']>=5.0-EPS]
    # A package completion fill is the next opposite-side actual fill before the next Reserve first fill.
    oppfills_window=[f for f in fills if int(f['t'])>=t and int(f['t'])<=end and f.get('side')==opp and float(f.get('price') or 0)+fp<1.0+1e-8]
    oppfills_eventual=[f for f in fills if int(f['t'])>=t and int(f['t'])<next_first_bound and f.get('side')==opp and float(f.get('price') or 0)+fp<1.0+1e-8]
    rfw=min(oppfills_window,key=lambda x:int(x['t'])) if oppfills_window else None; rfe=min(oppfills_eventual,key=lambda x:int(x['t'])) if oppfills_eventual else None
    rows.append({'marketId':mid,'cycleIndex':k,'reserveFirstSubmitAt':submit_t,'reserveSubmitToFillMs':t-submit_t,'submitOppBestBid':submit_metric['bestBid'] if submit_metric else None,'submitOppBestAsk':submit_metric['bestAsk'] if submit_metric else None,'submitLegalPassivePrice':submit_metric['legalPassivePrice'] if submit_metric else None,'submitLegalBehindTicks':submit_metric['legalBehindTicks'] if submit_metric else None,'submitHeadroomTicks':submit_headroom,'firstHeadroomDeteriorationMs':(first_headroom_det-submit_t) if first_headroom_det is not None else None,'headroomDeteriorationToFillMs':(t-first_headroom_det) if first_headroom_det is not None else None,'headroomDeteriorationBeforeFirstDepthDrop':bool(first_headroom_det is not None and (first_depth_drop is None or first_headroom_det<first_depth_drop)),'firstDepthDropBeforeHeadroomDeterioration':bool(first_depth_drop is not None and (first_headroom_det is None or first_depth_drop<first_headroom_det)),'submitToPathBreakMs':(first_path_break-submit_t) if first_path_break is not None else None,'pathBreakToFirstFillMs':(t-first_path_break) if first_path_break is not None else None,'firstLegAtBestReceiptFractionBeforeFill':sum(x[1]<=EPS for x in first_leg_prefill)/len(first_leg_prefill) if first_leg_prefill else None,'firstLegMaxBehindTicksBeforeFill':max((x[1] for x in first_leg_prefill),default=None),'firstLegInitialDepth':init_depth,'firstLegMinDepth':min_depth,'firstLegDepletionFractionBeforeFill':(max(0.0,init_depth-min_depth)/max(init_depth,EPS)) if init_depth is not None and min_depth is not None else None,'firstLegFirstDepthDropMs':(first_depth_drop-submit_t) if first_depth_drop is not None else None,'firstLegDepthDropBeforePathBreak':bool(first_depth_drop is not None and (first_path_break is None or first_depth_drop<first_path_break)),'pathBreakBeforeFirstLegDepthDrop':bool(first_path_break is not None and (first_depth_drop is None or first_path_break<first_depth_drop)),'firstFillAt':t,'firstSide':first_side,'firstPrice':fp,'oppSide':opp,'economicCeiling':first['economicCeiling'],'firstBestBid':first['bestBid'],'firstBestAsk':first['bestAsk'],'firstLegalPassivePrice':first['legalPassivePrice'],'firstCeilingBehindTicks':first['ceilingBehindTicks'],'firstLegalBehindTicks':first['legalBehindTicks'],'minLegalBehindTicks':min(beh),'maxLegalBehindTicks':max(beh),'within1ReceiptFraction':sum(x<=1.0+EPS for x in beh)/len(beh),'within2ReceiptFraction':sum(x<=2.0+EPS for x in beh)/len(beh),'firstToWithin1Ms':(min(within1)-t) if within1 else None,'firstToFar5Ms':(min(far5)-t) if far5 else None,'packageRepairFillAt':int(rfw['t']) if rfw else None,'packageRepairFillPrice':float(rfw['price']) if rfw else None,'packageRepairFillLagMs':int(rfw['t'])-t if rfw else None,'eventualRepairFillAt':int(rfe['t']) if rfe else None,'eventualRepairFillPrice':float(rfe['price']) if rfe else None,'eventualRepairFillLagMs':int(rfe['t'])-t if rfe else None,'completedWithin30sByTrace':rfw is not None,'eventuallyCompletedByTrace':rfe is not None,'pathReceipts':len(metrics)})
 finally:c.close()
 comp=[r for r in rows if r['completedWithin30sByTrace']];fail=[r for r in rows if not r['completedWithin30sByTrace']];eventual=[r for r in rows if r['eventuallyCompletedByTrace']]
 expected=sum(x['expectedFirstFills'] for x in audit);matched=sum(x['matchedFirstFills'] for x in audit);reportedCycles=sum(int(by[mid]['V23']['functional'].get('reserveCycleCompletion') or 0) for mid in by);traceCycles=sum(r['eventuallyCompletedByTrace'] for r in rows);packageCycles=sum(r['completedWithin30sByTrace'] for r in rows)
 out={'version':'ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1','researchOnly':True,'behaviorChange':False,'coverage':{'markets':len(by),'expectedFirstFills':expected,'matchedFirstFills':matched,'traceMatchedRate':matched/expected if expected else None,'reportedV23Cycles':reportedCycles,'traceEventualCompletedCycles':traceCycles,'tracePackageWindowCompletedCycles':packageCycles},'matchAudit':audit,'summary':{'all':block(rows),'completed':block(comp),'failed':block(fail)},'rows':rows,'boundary':['Uses frozen V23 submitTrace/fillTrace plus ETH receipt-clock public book; no HFT rerun.','Reserve first-leg fills are matched greedily to RESERVE_BUILD_FIRST submits by side+price+chronology and audited against functional reserveFirstLegActualFill count.','Completion-by-trace is next cheap opposite fill before next Reserve first fill / 30s horizon and is audited against reported reserveCycleCompletion.','No behavior change, no PnL tuning, consumed markets only.']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
