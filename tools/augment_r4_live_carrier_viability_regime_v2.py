from __future__ import annotations
import json,sqlite3,statistics
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]; IN=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_live_carrier_viability_echtgeld11_v1.json'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_live_carrier_viability_echtgeld11_v2_regime.json'; EDB=ROOT/'data/echtgeld_engine_v1.db'
def main():
 d=json.load(open(IN,encoding='utf-8')); rows=d['rows']; ec=sqlite3.connect(EDB); ec.row_factory=sqlite3.Row; bym=defaultdict(list)
 for r in rows:bym[int(r['marketId'])].append(r)
 for mid,rr in bym.items():
  ev=[dict(x) for x in ec.execute("select occurred_at_ms,event_type,client_order_id,delta_shares from engine_cap100_events where source_market_id=? and role='MAKER' order by occurred_at_ms,seq",(mid,))]
  info=defaultdict(lambda:{'ack':None,'firstFill':None,'fillQty':0.0,'terminal':None,'terminalType':None})
  reject_times=[]
  for e in ev:
   oid=e.get('client_order_id');t=int(e['occurred_at_ms']);et=str(e['event_type'])
   if not oid:continue
   z=info[oid]
   if et in {'ORDER_RESTING','ORDER_ACCEPTED'} and z['ack'] is None:z['ack']=t
   if et=='FILL_DELTA' and float(e.get('delta_shares') or 0)>0:
    if z['firstFill'] is None:z['firstFill']=t
    z['fillQty']+=float(e['delta_shares'])
   if et in {'ORDER_FILLED','ORDER_CANCELED','ORDER_REJECTED'}:
    z['terminal']=t;z['terminalType']=et
    if et=='ORDER_REJECTED':reject_times.append(t)
  for r in rr:
   t=int(r['t']); accepted=[z for z in info.values() if z['ack'] is not None and z['ack']<=t]; filled=[z for z in accepted if z['firstFill'] is not None and z['firstFill']<=t]; terminal=[z for z in accepted if z['terminal'] is not None and z['terminal']<=t]; termfill=[z for z in terminal if z['terminalType']=='ORDER_FILLED']; active=[z for z in accepted if z['terminal'] is None or z['terminal']>t]
   def cnt_ack(w):return sum(1 for z in accepted if t-w<z['ack']<=t)
   def cnt_first(w):return sum(1 for z in filled if t-w<z['firstFill']<=t)
   def qty_fill(w):
    s=0.0
    for e in ev:
     if e['event_type']=='FILL_DELTA' and t-w<int(e['occurred_at_ms'])<=t:s+=float(e.get('delta_shares') or 0)
    return s
   delays=[(z['firstFill']-z['ack'])/1000.0 for z in filled if z['firstFill'] is not None and z['ack'] is not None]
   lastfill=max([z['firstFill'] for z in filled],default=None); stale30=sum(1 for z in active if (t-z['ack'])>=30000); stale60=sum(1 for z in active if (t-z['ack'])>=60000)
   r.update({'venuePriorAcceptedCount':len(accepted),'venuePriorAnyFillRate':len(filled)/max(1,len(accepted)),'venuePriorTerminalCount':len(terminal),'venuePriorTerminalFillRate':len(termfill)/max(1,len(terminal)),'venueAckCount30s':cnt_ack(30000),'venueAckCount60s':cnt_ack(60000),'venueFirstFillCount30s':cnt_first(30000),'venueFirstFillCount60s':cnt_first(60000),'venueFillQty30s':qty_fill(30000),'venueFillQty60s':qty_fill(60000),'venueTimeSinceLastFirstFillS':None if lastfill is None else (t-lastfill)/1000.0,'venueMedianObservedFillDelayS':None if not delays else statistics.median(delays),'venueActiveAcceptedBacklog':len(active),'venueStale30Count':stale30,'venueStale60Count':stale60,'venueStale30Fraction':stale30/max(1,len(active)),'venueStale60Fraction':stale60/max(1,len(active)),'venueRejectCount30s':sum(t-30000<x<=t for x in reject_times),'venueRejectCount60s':sum(t-60000<x<=t for x in reject_times)})
 ec.close(); d['version']='R4_LIVE_CARRIER_VIABILITY_ECHTGELD11_V2_REGIME'; d['sourceBoundary']=d.get('sourceBoundary','')+' + strict-past venue regime summaries computed only from engine events at or before checkpoint'; OUT.write_text(json.dumps(d,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'rows':len(rows),'markets':len(bym),'regimeFeatures':17}))
if __name__=='__main__':main()
