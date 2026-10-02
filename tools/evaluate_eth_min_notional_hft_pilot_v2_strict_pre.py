from __future__ import annotations
import argparse,json,statistics,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex
LEADS_MS=[1000,750,500,250,1]
MODELS=['observed_fill','minimum_legal','gap_legal_carrier']

def submit(bt,oid,side,price,qty):
    ns,np=ex.native_order(side,price)
    if ns=='BUY': return int(bt.submit_buy_order(0,oid,np,float(qty),ex.hbt.GTC,ex.LIMIT,False))
    return int(bt.submit_sell_order(0,oid,np,float(qty),ex.hbt.GTC,ex.LIMIT,False))

def run(row,tapes,model,lead):
    feed.ARCHIVE_DIR=tapes; ev,_,meta=feed.build_archive_events(int(row['marketId']),trade_offset='mid')
    bt=ex.new_bt(ev,entry_latency_ms=0,response_latency_ms=0,queue_model='risk')
    obs=float(row['observedFillQty']); gap=float(row['gap']); legal=float(row['minLegalQty'])
    qty=obs if model=='observed_fill' else max(obs,legal) if model=='minimum_legal' else max(gap,legal)
    t=int(row['firstEventMs'])-int(lead)
    try:
        ex.initialize_bt(bt); ex.advance_to(bt,t); rc=submit(bt,1,row['side'],float(row['price']),qty)
        # zero-latency exchange/response processing only; one nanosecond does not advance to later feed data.
        bt.elapse(1)
        s=ex.order_snapshot(bt,1); fill=float(s.get('cumExecQty') or 0.0)
        return {'marketId':row['marketId'],'parentId':row['parentId'],'model':model,'leadMs':lead,'price':row['price'],'observedFillQty':obs,'gap':gap,'minLegalQty':legal,'requestedQty':qty,'immediateFillQty':fill,'absErrorObserved':abs(fill-obs),'signedErrorObserved':fill-obs,'fillToGap':fill/gap if gap>0 else None,'status':s.get('status'),'submitRc':rc,'stateAtMs':t,'tapeUpdates':meta.get('updates')}
    finally: bt.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='eth_strictpre_') as td:
        td=Path(td); zipfile.ZipFile(a.bundle).extractall(td); c=json.loads((td/'cohort.json').read_text(encoding='utf-8')); out=[]
        for i,row in enumerate(c['rows'],1):
            for m in MODELS:
                for lead in LEADS_MS:
                    try: out.append(run(row,td/'tapes',m,lead))
                    except Exception as e: out.append({'marketId':row['marketId'],'parentId':row['parentId'],'model':m,'leadMs':lead,'error':repr(e)})
            print(json.dumps({'progress':i,'total':len(c['rows']),'marketId':row['marketId']}),flush=True)
        by=[]
        for m in MODELS:
            for lead in LEADS_MS:
                rs=[x for x in out if x.get('model')==m and x.get('leadMs')==lead and 'error' not in x]
                by.append({'model':m,'leadMs':lead,'n':len(rs),'maeObserved':statistics.mean(x['absErrorObserved'] for x in rs) if rs else None,'medianAbsErrorObserved':statistics.median(x['absErrorObserved'] for x in rs) if rs else None,'meanSignedErrorObserved':statistics.mean(x['signedErrorObserved'] for x in rs) if rs else None,'within20pctObserved':sum(x['absErrorObserved']<=max(1.0,.2*x['observedFillQty']) for x in rs)/len(rs) if rs else None,'meanImmediateFill':statistics.mean(x['immediateFillQty'] for x in rs) if rs else None,'meanObserved':statistics.mean(x['observedFillQty'] for x in rs) if rs else None})
        env={}
        for m in MODELS:
            per=[]
            for row in c['rows']:
                rs=[x for x in out if x.get('parentId')==row['parentId'] and x.get('model')==m and 'error' not in x]; vals=[x['immediateFillQty'] for x in rs]
                if vals: per.append({'parentId':row['parentId'],'marketId':row['marketId'],'observed':row['observedFillQty'],'gap':row['gap'],'legal':row['minLegalQty'],'min':min(vals),'median':statistics.median(vals),'max':max(vals),'inside':min(vals)-1e-9<=row['observedFillQty']<=max(vals)+1e-9})
            env[m]={'parents':len(per),'observedInsideStrictPreEnvelopeRate':sum(x['inside'] for x in per)/len(per) if per else None,'rows':per}
        payload={'version':'ETH_MIN_NOTIONAL_HFT_PILOT_V2_STRICT_PRE_EVENT','cohortRows':len(c['rows']),'strictBoundary':'entire Target event second forbidden; HFT book state sampled only before event bucket start','leadsMs':LEADS_MS,'models':MODELS,'fixedLeadSummary':by,'envelopes':env,'results':out,'boundaries':['Immediate crossing-capacity probe only; no same-event-second or post-event depth is allowed','zero execution latency is deliberate here: it isolates executable depth already present in strict-pre-event book, not network latency','Observed-fill model remains a capacity sanity baseline']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'output':a.output,'fixedLeadSummary':by,'envelopeRates':{k:v['observedInsideStrictPreEnvelopeRate'] for k,v in env.items()}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
