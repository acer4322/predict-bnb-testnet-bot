from __future__ import annotations
import csv, json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PKG=ROOT/'data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907'
OFF=ROOT/'data/target_wallet_official_v1.db'
MKR=ROOT/'data/wallet_maker_book_inference.db'
SUMMARY=PKG/'24_TARGET_BTC_DIRECTION_CONFIDENCE_MARKET_SUMMARY_V1.csv'
OUT=PKG/'35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv'
AUD=PKG/'36_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_AUDIT_V1.json'

def main():
    with SUMMARY.open(encoding='utf-8-sig', newline='') as f:
        mids=[int(r['market_id']) for r in csv.DictReader(f)]
    co=sqlite3.connect(f'file:{OFF.resolve().as_posix()}?mode=ro',uri=True); co.row_factory=sqlite3.Row
    cm=sqlite3.connect(f'file:{MKR.resolve().as_posix()}?mode=ro',uri=True); cm.row_factory=sqlite3.Row
    fields=['market_id','event_id','event_ms','observed_at_ms','role','side','price','shares','order_hash','parent_found','parent_target_side','parent_native_book_side','parent_target_price','parent_native_price','parent_first_target_ms','parent_last_target_ms','parent_placement_first_ms','parent_placement_last_ms','parent_resting_ms','parent_post_action','parent_post_action_delay_ms','parent_post_action_native_price','parent_target_fill_count','parent_target_filled_shares','parent_expected_parent_shares','parent_allocated_fill_shares','parent_fill_allocation_coverage','parent_placement_allocated_shares','parent_placement_coverage','parent_confidence','placement_precedes_fill','last_target_not_after_fill']
    rows=[]; maker=0; found=0; highq=0; orderhash_missing=0
    for mid in mids:
        parents={}
        for p in cm.execute('select * from maker_book_inference_v21_parent_lifecycles where market_id=?',(mid,)):
            h=str(p['order_hash'] or '')
            if h: parents[h]=dict(p)
        for e in co.execute("select id,event_ms,observed_at_ms,role,side,price,shares,order_hash from wallet_shadow_target_events where market_id=? and asset='BTC' order by event_ms,id",(mid,)):
            if e['role']!='MAKER':
                continue
            maker+=1
            h=str(e['order_hash'] or '')
            if not h: orderhash_missing+=1
            p=parents.get(h)
            if p: found+=1
            if p and (p.get('placement_coverage') or 0)>=0.85 and (p.get('fill_allocation_coverage') or 0)>=0.70: highq+=1
            em=int(e['event_ms'])
            row={'market_id':mid,'event_id':e['id'],'event_ms':em,'observed_at_ms':e['observed_at_ms'],'role':e['role'],'side':e['side'],'price':e['price'],'shares':e['shares'],'order_hash':h,'parent_found':1 if p else 0}
            if p:
                for src,dst in [('target_side','parent_target_side'),('native_book_side','parent_native_book_side'),('target_price','parent_target_price'),('native_price','parent_native_price'),('first_target_ms','parent_first_target_ms'),('last_target_ms','parent_last_target_ms'),('placement_first_ms','parent_placement_first_ms'),('placement_last_ms','parent_placement_last_ms'),('resting_ms','parent_resting_ms'),('post_action','parent_post_action'),('post_action_delay_ms','parent_post_action_delay_ms'),('post_action_native_price','parent_post_action_native_price'),('target_fill_count','parent_target_fill_count'),('target_filled_shares','parent_target_filled_shares'),('expected_parent_shares','parent_expected_parent_shares'),('allocated_fill_shares','parent_allocated_fill_shares'),('fill_allocation_coverage','parent_fill_allocation_coverage'),('placement_allocated_shares','parent_placement_allocated_shares'),('placement_coverage','parent_placement_coverage'),('confidence','parent_confidence')]: row[dst]=p.get(src)
                pf=p.get('placement_first_ms'); lt=p.get('last_target_ms')
                row['placement_precedes_fill']=1 if pf is not None and int(pf)<=em else 0
                row['last_target_not_after_fill']=1 if lt is not None and int(lt)<=em else 0
            else:
                for k in fields:
                    row.setdefault(k,None)
            rows.append(row)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    aud={'version':'TARGET_DIRECTION_CONFIDENCE_ACTION_PARENT_BRIDGE_V1','researchOnly':True,'semantics':'RETROSPECTIVE_EXECUTION_DIAGNOSTIC','markets':len(mids),'makerFillLegs':maker,'parentFound':found,'parentFoundRate':found/maker if maker else None,'highQualityParentLink':highq,'highQualityParentLinkRate':highq/maker if maker else None,'missingOrderHash':orderhash_missing,'guards':['Exact order_hash bridge where available.','Parent placement/cancel inference is retrospective; never treat it as private Target runtime state.','Use bridge to test whether apparent post-conflict continuation was already-working inventory/execution, not to create runtime authority.']}
    AUD.write_text(json.dumps(aud,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(aud,ensure_ascii=False,indent=2))
    co.close(); cm.close()
if __name__=='__main__': main()
