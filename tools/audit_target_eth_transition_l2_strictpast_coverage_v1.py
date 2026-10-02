from __future__ import annotations
import argparse,json,sqlite3,math
from pathlib import Path
from collections import defaultdict
EPS=1e-9

def econ_role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--target-db',required=True);ap.add_argument('--book-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tc=sqlite3.connect(a.target_db);tc.row_factory=sqlite3.Row;bc=sqlite3.connect(a.book_db);bc.row_factory=sqlite3.Row
    quality={int(r['market_id']):dict(r) for r in bc.execute('select market_id,eligible_execution_training,window_start_ms,window_end_ms,first_source_ms,last_source_ms,tail_gap_ms,max_source_gap_ms,p99_source_gap_ms from maker_execution_market_quality_v1')}
    # Restrict to ETH markets having book inference updates. This creates a later, naturally disjoint chronology from the old transition snapshot.
    bmids={int(r[0]) for r in bc.execute('select distinct market_id from maker_book_inference_updates')}
    raw=list(tc.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));tc.close()
    by=defaultdict(list)
    for r in raw:
        mid=int(r['market_id'])
        if mid in bmids:by[mid].append(r)
    states=[];all_cross=0
    for mid,evs in by.items():
        up=dn=0.
        for i,r in enumerate(evs):
            side=str(r['side']).upper();q=float(r['shares']);t=int(r['first_event_ms']);rel=econ_role(side,up,dn);gap=abs(up-dn);cross=(rel==1 and gap>EPS and q>=gap-EPS);overflow=max(0.,q-gap) if cross else 0.
            if side=='UP':up+=q
            else:dn+=q
            if not cross:continue
            all_cross+=1
            # Runtime-valid frontier: update must have been received no later than Target event time.
            prev=bc.execute('select id,source_timestamp_ms,received_at_ms,order_count,is_checkpoint,bid_level_count,ask_level_count from maker_book_inference_updates where market_id=? and received_at_ms<=? order by received_at_ms desc,id desc limit 1',(mid,t)).fetchone()
            next_parent=evs[i+1] if i+1<len(evs) else None;next_delay=None;next_role=None
            if next_parent is not None:
                next_delay=int(next_parent['first_event_ms'])-t
                if next_delay<=30000:next_role=econ_role(str(next_parent['side']).upper(),up,dn)
            qrow=quality.get(mid) or {}
            rec={'marketId':mid,'parentId':int(r['parent_id']),'t':t,'route':str(r['role']),'side':side,'price':float(r['average_price']),'qty':q,'preGap':gap,'overflow':overflow,'nextDelayMs':next_delay,'nextRoleRepair':None if next_role is None else int(next_role==1),'qualityEligible':bool(qrow.get('eligible_execution_training'))}
            if prev is not None:
                rec.update({'hasStrictPastBook':True,'bookUpdateId':int(prev['id']),'bookSourceMs':int(prev['source_timestamp_ms']),'bookReceivedMs':int(prev['received_at_ms']),'bookReceiptAgeMs':t-int(prev['received_at_ms']),'bookSourceAgeMs':t-int(prev['source_timestamp_ms']),'bookOrderCount':int(prev['order_count'] or 0),'bookCheckpoint':bool(prev['is_checkpoint']),'bidLevels':int(prev['bid_level_count'] or 0),'askLevels':int(prev['ask_level_count'] or 0)})
            else:rec['hasStrictPastBook']=False
            states.append(rec)
    bc.close()
    covered=[x for x in states if x['hasStrictPastBook']];ages=[x['bookReceiptAgeMs'] for x in covered];active=[x for x in covered if x['nextRoleRepair'] is not None]
    def nle(ms):return sum(x['bookReceiptAgeMs']<=ms for x in covered)
    def med(z):
        z=sorted(z);return None if not z else float(z[len(z)//2])
    agg={'marketsWithParentsAndBook':len(by),'crossingRepairStates':all_cross,'states':len(states),'strictPastBookCovered':len(covered),'coverageRate':len(covered)/max(1,len(states)),'ageLe250ms':nle(250),'ageLe500ms':nle(500),'ageLe1000ms':nle(1000),'ageLe2000ms':nle(2000),'ageLe5000ms':nle(5000),'ageLe1000Rate':nle(1000)/max(1,len(covered)),'medianReceiptAgeMs':med(ages),'qualityEligibleStates':sum(bool(x['qualityEligible']) for x in states),'activeLabelStatesCovered':len(active),'firstEventMs':min((x['t'] for x in states),default=None),'lastEventMs':max((x['t'] for x in states),default=None)}
    gates={'coverageAtLeast80Pct':agg['coverageRate']>=.80,'medianReceiptAgeLe1000ms':agg['medianReceiptAgeMs'] is not None and agg['medianReceiptAgeMs']<=1000,'ageLe2000AtLeast80Pct':agg['ageLe2000']/max(1,len(covered))>=.80,'activeLabeledAtLeast1000':agg['activeLabelStatesCovered']>=1000}
    out={'version':'TARGET_ETH_TRANSITION_L2_STRICTPAST_COVERAGE_V1','date':'2026-09-04','researchOnly':True,'aggregate':agg,'gates':gates,'decision':'BUILD_L2_TRANSITION_FEATURE_DATASET' if all(gates.values()) else 'DO_NOT_TRAIN_L2_TRANSITION_YET','rows':states,'boundary':['book feature frontier requires received_at_ms <= Target parent event_ms','source timestamp alone never authorizes a feature','Target next parent role is offline label only','no winner/PnL feature','no observed decrease after current event','later book chronology intentionally disjoint from old transition snapshot']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'aggregate':agg,'gates':gates},ensure_ascii=False))
if __name__=='__main__':main()
