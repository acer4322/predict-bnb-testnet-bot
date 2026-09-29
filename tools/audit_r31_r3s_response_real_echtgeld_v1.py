from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
OUT=ROOT/'data/research/r3_v0/r31_r3s_response_real_echtgeld_v1.json';DB=ROOT/'data/echtgeld_engine_v1.db'

def classify(s):
 f=s['formationExecutionContext'];live=f['liveBySide']; unresolved=sum(live[x][r]['unresolvedQty'] for x in ('UP','DOWN') for r in ('maker','taker'));unknown=sum(live[x][r]['unknownCount'] for x in ('UP','DOWN') for r in ('maker','taker'));maxage=max(live[x][r]['maxAgeMs'] for x in ('UP','DOWN') for r in ('maker','taker'));stalls=f['recent15sStallCount'];fills=f['recent15sFillDeltaCount']
 if unknown:return 'WAIT_EXECUTION_CERTAINTY'
 if s.get('situationCode')=='CANCEL_PENDING':return 'WAIT_CANCEL_TERMINAL_ACK'
 if unresolved<=1e-9:return 'NORMAL_R3S'
 if fills>0 and stalls==0:return 'KEEP_AND_OBSERVE_RECOVERY'
 if maxage>=15000 and stalls>0:return 'REASSESS_OLDEST_BLOCKER'
 return 'WAIT_PENDING_CHILDREN'

def main():
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 mids=[r[0] for r in con.execute("select distinct source_market_id from engine_cap100_orders where source_id='R2_R21_8789' and source_market_id is not null order by source_market_id")]
 rows=[]
 for mid in mids:
  orders=[dict(r) for r in con.execute("select * from engine_cap100_orders where source_id='R2_R21_8789' and source_market_id=? order by created_at_ms,client_order_id",(mid,))]
  events=[dict(r) for r in con.execute("select * from engine_cap100_events where source_market_id=? order by occurred_at_ms,seq",(mid,))]
  b=R31EchtgeldStateBridgeV1('R2_R21_8789');b.reset_market(mid);registered=set();up=dn=0.;responses=[];seen_middle_blocker=False
  timeline=sorted([(int(o['created_at_ms'] or 0),'O',o) for o in orders]+[(int(e['occurred_at_ms'] or 0),'E',e) for e in events],key=lambda x:(x[0],0 if x[1]=='O' else 1))
  for t,k,z in timeline:
   if k=='O':
    cid=z['client_order_id'];registered.add(cid);b.register_intent(client_order_id=cid,market_id=mid,role=z['role'],side=z['side'],requested_shares=z['requested_shares'],created_at_ms=t,reason='REAL_REPLAY',requested_price=z['requested_price'])
   else:
    # attach source_id for historical events if absent
    e=dict(z);e['source_id']='R2_R21_8789';e.setdefault('requested_shares',next((o['requested_shares'] for o in orders if o['client_order_id']==e.get('client_order_id')),0));e.setdefault('created_at_ms',next((o['created_at_ms'] for o in orders if o['client_order_id']==e.get('client_order_id')),t));e.setdefault('requested_price',next((o['requested_price'] for o in orders if o['client_order_id']==e.get('client_order_id')),0))
    if str(e.get('event_type') or '').upper()=='FILL_DELTA':
     sh=float(e.get('delta_shares') or 0);side=str(e.get('side') or '').upper();up+=sh if side=='UP' else 0;dn+=sh if side=='DOWN' else 0
    b.observe_event(e)
   s=b.snapshot(at_ms=t,actual_inventory={'UP':up,'DOWN':dn},pending_cancels=[],orphan_count=0,engine_state={},portfolio_context={});resp=classify(s)
   live=[c for c in s.get('children',[]) if not c.get('terminal')]
   if len(live)>=2:
    oldest=sorted(live,key=lambda c:int(c.get('createdAtMs') or t))[0];latest=sorted(live,key=lambda c:int(c.get('createdAtMs') or t))[-1]
    if oldest.get('clientOrderId')!=latest.get('clientOrderId') and float(oldest.get('requestedShares') or 0)-float(oldest.get('confirmedFilledShares') or 0)>1e-9:
     seen_middle_blocker=True;responses.append({'atMs':t,'response':resp,'oldest':oldest.get('clientOrderId'),'latest':latest.get('clientOrderId'),'ownership':s.get('ownershipState'),'situation':s.get('situationCode')})
  rows.append({'marketId':mid,'seenEarlierUnresolvedWhileLaterChildExists':seen_middle_blocker,'responseSamples':responses[-8:],'pass':not seen_middle_blocker or all(x['response']!='NORMAL_R3S' and not (x['situation']=='CANCEL_PENDING' and x['response']=='REASSESS_OLDEST_BLOCKER') for x in responses)})
 rep={'version':'R31_R3S_RESPONSE_REAL_ECHTGELD_V1','markets':len(rows),'rows':rows,'passed':sum(x['pass'] for x in rows),'allPass':all(x['pass'] for x in rows)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'markets':len(rows),'passed':rep['passed'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
