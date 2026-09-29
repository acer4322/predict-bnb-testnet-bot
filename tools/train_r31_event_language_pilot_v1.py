from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/echtgeld_engine_v1.db'
OUT=D/'r31_event_language_pilot_v1_report.json'; MODEL=D/'r31_event_language_pilot_v1.joblib'
F=['role_maker','side_up','requested_qty','filled_qty','unresolved_qty','fill_ratio','age_s','state_partial','state_cancel_pending','state_unknown','event_fill_delta','event_terminal','same_side_live','opp_side_live']
def main():
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 orders={r['client_order_id']:dict(r) for r in con.execute("select * from engine_cap100_orders where source_id='R2_R21_8789'")}
 ev=[dict(r) for r in con.execute("select * from engine_cap100_events order by occurred_at_ms,seq")];con.close()
 live={'UP':set(),'DOWN':set()}; X=[];y=[]
 for e in ev:
  cid=str(e.get('client_order_id') or ''); o=orders.get(cid)
  if not o: continue
  role=str(o.get('role') or e.get('role') or '').upper(); side=str(o.get('side') or e.get('side') or '').upper(); state=str(e.get('state') or o.get('state') or '').upper(); et=str(e.get('event_type') or '').upper(); at=int(e.get('occurred_at_ms') or 0)
  if role not in {'MAKER','TAKER'} or side not in {'UP','DOWN'}: continue
  req=float(o.get('requested_shares') or 0.); filled=max(float(o.get('filled_share_qty') or 0.), float(e.get('delta_shares') or 0.)); un=max(0.,req-filled); fr=filled/req if req>1e-9 else 0.; age=max(0.,at-int(o.get('created_at_ms') or at))/1000.
  terminal=state in {'FILLED','CANCELED','REJECTED','EXPIRED','FAILED'} or et in {'ORDER_FILLED','ORDER_CANCELED','ORDER_REJECTED','ORDER_EXPIRED','ORDER_FAILED'}
  if terminal: live[side].discard(cid)
  else: live[side].add(cid)
  vals=[float(role=='MAKER'),float(side=='UP'),req,filled,un,fr,age,float(state=='PARTIAL_FILL'),float(state in {'CANCEL_PENDING','CANCEL_UNKNOWN'}),float(state in {'UNKNOWN_SUBMISSION','CANCEL_UNKNOWN'}),float(et=='FILL_DELTA'),float(terminal),float(len(live[side])),float(len(live['DOWN' if side=='UP' else 'UP']))]
  # information-important event for R3.1 formation/recovery view; no action label
  lab=int(et=='FILL_DELTA' or state in {'PARTIAL_FILL','CANCEL_PENDING','CANCEL_UNKNOWN','UNKNOWN_SUBMISSION'} or (terminal and un>0.011) or age>=5.0)
  X.append(vals);y.append(lab)
 if len(y)<40 or len(set(y))<2: raise RuntimeError(f'insufficient rows/classes: {len(y)} {set(y)}')
 n=len(y); a=int(n*.7); b=int(n*.85); model=HistGradientBoostingClassifier(max_depth=4,learning_rate=.07,max_iter=120,l2_regularization=1.0,random_state=42); model.fit(np.asarray(X[:a]),np.asarray(y[:a]))
 rep={'version':'R31_EVENT_LANGUAGE_PILOT_V1','researchOnly':True,'source':'8781 engine_cap100_orders/events source_id=R2_R21_8789','rows':n,'positiveRate':float(np.mean(y)),'features':F,'splits':{}}
 for name,i,j in [('train',0,a),('validation',a,b),('test',b,n)]:
  yy=np.asarray(y[i:j]); pp=model.predict_proba(np.asarray(X[i:j]))[:,1]; pr=(pp>=.5).astype(int); rep['splits'][name]={'n':len(yy),'positiveRate':float(yy.mean()),'auc':float(roc_auc_score(yy,pp)) if len(set(yy.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pr))}
 rep['boundary']='Information-only R3.1 event-language pilot. Labels mark lifecycle facts relevant to Formation/Recovery visibility; no strategy/action authority.'
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); joblib.dump({'version':rep['version'],'model':model,'features':F},MODEL); print(json.dumps({'ok':True,'artifact':str(OUT),'model':str(MODEL),'test':rep['splits']['test']}))
if __name__=='__main__': main()
