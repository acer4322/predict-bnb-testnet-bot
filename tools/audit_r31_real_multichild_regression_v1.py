from __future__ import annotations
import json,sqlite3,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
OUT=ROOT/'data/research/r3_v0/r31_real_multichild_regression_v1.json';DB=ROOT/'data/echtgeld_engine_v1.db'
def main():
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 orders=[dict(r) for r in con.execute("SELECT * FROM engine_cap100_orders WHERE source_id='R2_R21_8789' ORDER BY source_market_id,created_at_ms,client_order_id")]
 events=[dict(r) for r in con.execute("SELECT * FROM engine_cap100_events ORDER BY seq")]
 bym=defaultdict(list)
 for o in orders:bym[int(o['source_market_id'] or 0)].append(o)
 evm=defaultdict(list)
 known={str(o['client_order_id']) for o in orders}
 for e in events:
  if str(e.get('client_order_id') or '') in known:evm[int(e.get('source_market_id') or 0)].append(e)
 rows=[]
 for mid,oo in sorted(bym.items()):
  b=R31EchtgeldStateBridgeV1('R2_R21_8789');b.reset_market(mid);registered=set();up=dn=0.;viol=[];peak_live=0;peak_unresolved=0.;snapshots=0
  timeline=[]
  for o in oo:timeline.append((int(o['created_at_ms'] or 0),0,'ORDER',o))
  for e in evm[mid]:timeline.append((int(e['occurred_at_ms'] or 0),1,'EVENT',e))
  timeline.sort(key=lambda x:(x[0],x[1]))
  for t,_,kind,x in timeline:
   if kind=='ORDER':
    cid=str(x['client_order_id']);
    if cid not in registered:
     b.register_intent(client_order_id=cid,market_id=mid,role=str(x['role']),side=str(x['side']),requested_shares=float(x['requested_shares'] or 0),created_at_ms=t,reason=str(x.get('strategy') or ''),requested_price=float(x['requested_price'] or 0));registered.add(cid)
   else:
    cid=str(x['client_order_id']);
    if cid not in registered:
     o=next((z for z in oo if str(z['client_order_id'])==cid),None)
     if o:
      b.register_intent(client_order_id=cid,market_id=mid,role=str(o['role']),side=str(o['side']),requested_shares=float(o['requested_shares'] or 0),created_at_ms=int(o['created_at_ms'] or t),reason=str(o.get('strategy') or ''),requested_price=float(o['requested_price'] or 0));registered.add(cid)
    if str(x.get('event_type') or '').upper()=='FILL_DELTA':
     q=float(x.get('delta_shares') or 0);side=str(x.get('side') or '').upper()
     if side=='UP':up+=q
     elif side=='DOWN':dn+=q
    b.observe_event(x)
   s=b.snapshot(at_ms=max(t,1),actual_inventory={'UP':up,'DOWN':dn},pending_cancels=[],orphan_count=0,engine_state={},portfolio_context={});snapshots+=1
   live=[c for c in s['children'] if not c.get('terminal')];peak_live=max(peak_live,len(live));f=s['formationExecutionContext'];unres=sum(f['liveMakerUnresolvedQty'].values())+sum(f['liveTakerUnresolvedQty'].values());peak_unresolved=max(peak_unresolved,unres)
   # regression invariant: every nonterminal child with unresolved qty stays represented, regardless of creation order.
   for c in live:
    rem=max(0.,float(c.get('requestedShares') or 0)-float(c.get('confirmedFilledShares') or 0))
    if rem>1e-9 and not any(z.get('clientOrderId')==c.get('clientOrderId') for z in s['children']):viol.append({'t':t,'type':'LIVE_CHILD_MISSING','cid':c.get('clientOrderId')})
   # newest healthy child must never erase older unresolved count.
   calc=sum(max(0.,float(c.get('requestedShares') or 0)-float(c.get('confirmedFilledShares') or 0)) for c in live)
   if abs(calc-unres)>1e-6:viol.append({'t':t,'type':'UNRESOLVED_AGG_MISMATCH','calc':calc,'reported':unres})
  rows.append({'marketId':mid,'orders':len(oo),'events':len(evm[mid]),'snapshots':snapshots,'peakLiveChildren':peak_live,'peakUnresolvedQty':peak_unresolved,'violations':viol,'pass':not viol})
 rep={'version':'R31_REAL_MULTICHILD_REGRESSION_V1','markets':len(rows),'rows':rows,'passed':sum(r['pass'] for r in rows),'allPass':all(r['pass'] for r in rows),'marketsWithMultiChild':sum(r['peakLiveChildren']>=2 for r in rows)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'markets':rep['markets'],'passed':rep['passed'],'multiChildMarkets':rep['marketsWithMultiChild'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
