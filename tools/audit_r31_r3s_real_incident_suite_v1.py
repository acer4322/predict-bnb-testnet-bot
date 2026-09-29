from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
from tools.audit_r31_r3s_response_real_echtgeld_v1 import classify
DB=ROOT/'data/echtgeld_engine_v1.db';OUT=ROOT/'data/research/r3_v0/r31_r3s_real_incident_suite_v1.json';SRC='R2_R21_8789'
def replay_market(con,mid):
 orders=[dict(r) for r in con.execute("select * from engine_cap100_orders where upper(source_id)=? and source_market_id=? order by created_at_ms,client_order_id",(SRC,mid))]
 events=[dict(r) for r in con.execute("select * from engine_cap100_events where source_market_id=? order by occurred_at_ms,seq",(mid,))]
 om={o['client_order_id']:o for o in orders};b=R31EchtgeldStateBridgeV1(SRC);b.reset_market(mid);up=dn=0.;snaps=[]
 tl=sorted([(int(o['created_at_ms'] or 0),0,'O',o) for o in orders]+[(int(e['occurred_at_ms'] or 0),1,'E',e) for e in events])
 for t,_,k,z in tl:
  if k=='O':b.register_intent(client_order_id=z['client_order_id'],market_id=mid,role=z['role'],side=z['side'],requested_shares=z['requested_shares'],created_at_ms=t,reason='REAL_INCIDENT_REPLAY',requested_price=z['requested_price'])
  else:
   e=dict(z);o=om.get(e.get('client_order_id'),{});e['source_id']=SRC;e.setdefault('requested_shares',o.get('requested_shares',0));e.setdefault('created_at_ms',o.get('created_at_ms',t));e.setdefault('requested_price',o.get('requested_price',0))
   if str(e.get('event_type') or '').upper()=='FILL_DELTA':
    sh=float(e.get('delta_shares') or 0);side=str(e.get('side') or '').upper();up+=sh if side=='UP' else 0;dn+=sh if side=='DOWN' else 0
   b.observe_event(e)
  s=b.snapshot(at_ms=t,actual_inventory={'UP':up,'DOWN':dn},pending_cancels=[],orphan_count=0,engine_state={},portfolio_context={});snaps.append({'atMs':t,'kind':k,'cid':z.get('client_order_id'),'eventType':z.get('event_type') if k=='E' else 'ORDER_INTENT','state':z.get('state'),'response':classify(s),'ownership':s.get('ownershipState'),'situation':s.get('situationCode'),'children':[{k:c.get(k) for k in ('clientOrderId','role','side','state','requestedShares','confirmedFilledShares','terminal')} for c in s.get('children',[]) if not c.get('terminal')]})
 return orders,events,snaps
def main():
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 mids=[r[0] for r in con.execute("select distinct source_market_id from engine_cap100_orders where upper(source_id)=? order by source_market_id",(SRC,))]
 rows=[]
 for mid in mids:
  orders,events,snaps=replay_market(con,mid);by={}
  for e in events:by.setdefault(e['client_order_id'],[]).append(e)
  for o in orders:
   cid=o['client_order_id'];es=by.get(cid,[]);cancel=[e for e in es if str(e.get('state') or '').upper() in {'CANCEL_PENDING','CANCEL_UNKNOWN'} or 'CANCEL' in str(e.get('event_type') or '').upper()];fills=[e for e in es if str(e.get('event_type') or '').upper()=='FILL_DELTA' and float(e.get('delta_shares') or 0)>0]
   if cancel and fills and any(int(f['occurred_at_ms'])>int(c['occurred_at_ms']) for c in cancel for f in fills):
    t=min(int(f['occurred_at_ms']) for f in fills for c in cancel if int(f['occurred_at_ms'])>int(c['occurred_at_ms']));near=[s for s in snaps if abs(s['atMs']-t)<=3000][-6:];pre=[x for x in near if x['atMs']<t];post=[x for x in near if x['atMs']>=t];ok=all(x['response']!='NORMAL_R3S' for x in pre) and any(x['response']=='NORMAL_R3S' and not x['children'] for x in post);rows.append({'type':'CANCEL_THEN_FILL','marketId':mid,'cid':cid,'pass':ok,'samples':near})
   cum=0.;first=None
   for e in es:
    if str(e.get('event_type') or '').upper()=='FILL_DELTA':
     cum+=float(e.get('delta_shares') or 0)
     if first is None and 0<cum<float(o['requested_shares'] or 0)-1e-9:first=int(e['occurred_at_ms'])
   if first and any(int(e['occurred_at_ms'])>first+5000 and str(e.get('event_type') or '').upper()=='FILL_DELTA' and float(e.get('delta_shares') or 0)>0 for e in es):
    t=first;near=[s for s in snaps if t<=s['atMs']<=t+10000][-8:];ok=all(x['response']!='NORMAL_R3S' for x in near if x['children']);rows.append({'type':'PARTIAL_LATE_FILL','marketId':mid,'cid':cid,'pass':ok,'samples':near})
   if str(o.get('state') or '').upper() in {'REJECTED','FAILED','EXPIRED','CANCELED'}:
    later=[x for x in orders if int(x['created_at_ms'] or 0)>int(o.get('completed_at_ms') or o['created_at_ms'] or 0)]
    if later:
     t=int(later[0]['created_at_ms']);near=[s for s in snaps if t-2000<=s['atMs']<=t+3000][-8:];ok=all(x['response']!='NORMAL_R3S' for x in near if x['children']);rows.append({'type':'TERMINAL_FAILURE_WITH_LATER_ORDER','marketId':mid,'cid':cid,'pass':ok,'samples':near})
 rep={'version':'R31_R3S_REAL_INCIDENT_SUITE_V1','rows':rows,'summary':{'cases':len(rows),'passed':sum(x['pass'] for x in rows),'failed':sum(not x['pass'] for x in rows),'byType':{t:{'cases':sum(x['type']==t for x in rows),'passed':sum(x['type']==t and x['pass'] for x in rows)} for t in sorted(set(x['type'] for x in rows))}},'allPass':all(x['pass'] for x in rows)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),**rep['summary'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
