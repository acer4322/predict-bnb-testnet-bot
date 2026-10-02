from __future__ import annotations
import json, math, sqlite3, time
from pathlib import Path
import joblib, numpy as np

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_multichild_pairwise_ranker_v3.joblib'
SRC=ROOT/'data/strategy_r2_r21_echtgeld_v1.db'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_multichild_ranker_v3_realmarket_shadow.db'

# This observer is deliberately read-only: no 8781 access and no order mutation.
def f(v,d=0.0):
    try: x=float(v)
    except: return d
    return x if math.isfinite(x) else d

def main():
    art=joblib.load(MODEL); model=art['model']; features=art['features']
    con=sqlite3.connect(SRC); con.row_factory=sqlite3.Row
    out=sqlite3.connect(OUT)
    out.execute('''create table if not exists shadow_scores(
      decision_id text, market_id integer, decision_ms integer, order_id text,
      side text, age_ms real, remaining_qty real, score real,
      rank_in_snapshot integer, child_count integer, desired_action text,
      execution_choice text, observed_payload text,
      primary key(decision_id,order_id))''')
    out.commit()
    last=''
    while True:
      rows=con.execute("select decision_id,market_id,decision_ms,desired_portfolio_action,execution_choice,payload_json from our_decisions where decision_id>? order by decision_id limit 500",(last,)).fetchall()
      if not rows:
        time.sleep(0.5); continue
      for r in rows:
        last=r['decision_id']
        try: p=json.loads(r['payload_json'] or '{}')
        except: continue
        inbox=p.get('r21ExecutionIncidentInbox') or p.get('r21SemanticCooperation') or {}
        children=inbox.get('children') or []
        if len(children)<2: continue
        vecs=[]
        for c in children:
          # Only score when the live payload exposes the trained feature set. Missing fields are zero,
          # and the raw payload is retained so real-market calibration can audit coverage later.
          x=[]
          for k in features:
            if k=='orderAgeMs': v=c.get('ageMs',c.get('orderAgeMs'))
            elif k=='remainingQty': v=c.get('unresolvedQty',c.get('remainingQty'))
            else: v=c.get(k)
            x.append(f(v))
          score=float(model.decision_function(np.asarray([x]))[0])
          vecs.append((score,c))
        vecs.sort(key=lambda z:z[0],reverse=True)
        for rank,(score,c) in enumerate(vecs,1):
          out.execute('insert or ignore into shadow_scores values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(
            r['decision_id'],r['market_id'],r['decision_ms'],str(c.get('clientOrderId') or c.get('orderId') or ''),
            str(c.get('side') or ''),f(c.get('ageMs',c.get('orderAgeMs'))),f(c.get('unresolvedQty',c.get('remainingQty'))),score,
            rank,len(vecs),r['desired_portfolio_action'],r['execution_choice'],json.dumps(c,separators=(',',':'),default=str)))
      out.commit()

if __name__=='__main__': main()
