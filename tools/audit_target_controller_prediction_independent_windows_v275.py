from __future__ import annotations

import argparse,csv,json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import audit_target_controller_prediction_8778_v272 as pred

ROOT=Path(__file__).resolve().parents[1]
TAIPEI=ZoneInfo('Asia/Taipei')
VERSION='TARGET_CONTROLLER_PREDICTION_INDEPENDENT_WINDOWS_V275'
STATES=ROOT/'data'/'research'/'target_controller_hazard_v21_states.csv'
BOOK=ROOT/'data'/'wallet_maker_book_inference.db'
OUT=ROOT/'data'/'research'/'target_controller_prediction_independent_windows_v275_report.json'
DEV={1396279,1396303,1396309,1396369}


def _i(x):
    try:return int(float(x))
    except:return 0


def _fmt(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).astimezone(TAIPEI).isoformat() if ms else None


def load_states(path):
    out=[]
    with Path(path).open(encoding='utf-8',newline='') as f:
        r=csv.DictReader(f)
        need={'market_id','regime','sample_ms'}
        if not need<=set(r.fieldnames or []): raise RuntimeError('V2.1 states CSV missing required columns')
        for x in r:
            m,t=_i(x.get('market_id')),_i(x.get('sample_ms'))
            if m>0 and t>0: out.append({'market_id':m,'sample_ms':t,'regime':str(x.get('regime') or 'UNKNOWN')})
    return sorted(out,key=lambda x:(x['sample_ms'],x['market_id']))


def history3(audited):
    by=defaultdict(list)
    for r in audited: by[int(r['market_id'])].append(r)
    out={}
    for m,rows in by.items():
        rows.sort(key=lambda r:int(r['sample_ms']))
        fresh=[int(r['sample_ms']) for r in rows if r.get('fresh_2s')]
        j=0
        for r in rows:
            t=int(r['sample_ms'])
            while j<len(fresh) and fresh[j]<t-3000:j+=1
            out[(m,t)]=bool(r.get('fresh_2s') and j<len(fresh) and fresh[j]<=t and t-fresh[j]>=1950)
    return out


def decide(markets):
    good=[m for m in markets if m['eligible']]
    n=sum(m['rows'] for m in good)
    status='READY_FOR_INDEPENDENT_REPAIR_VALIDATION' if len(good)>=4 and n>=300 else ('PARTIAL_INDEPENDENT_COVERAGE_DO_NOT_MODEL_YET' if len(good)>=2 else 'INSUFFICIENT_INDEPENDENT_8778_COVERAGE')
    return {'status':status,'eligibleIndependentMarkets':len(good),'eligibleIndependentRows':n,'rule':'Per market >=30 rows, fresh receivedStrict mid<=2s on >=80%, fresh 3s history support >=65%; development markets excluded. READY requires >=4 markets and >=300 rows.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--states',type=Path,default=STATES);p.add_argument('--book-db',type=Path,default=BOOK);p.add_argument('--out',type=Path,default=OUT);a=p.parse_args()
    states=load_states(a.states)
    if not states: raise RuntimeError('no V2.1 fixed-grid states found')
    mids=sorted({r['market_id'] for r in states})
    with pred._open_ro(a.book_db) as c:
        pred.bookmod.require_table(c,pred.bookmod.BOOK_TABLE);updates=pred.bookmod.load_updates(c,mids)
    audited=pred.audit_rows([{'market_id':r['market_id'],'sample_ms':r['sample_ms']} for r in states],updates,pred.PRIMARY_MODE)
    sa,aa=defaultdict(list),defaultdict(list)
    for r in states: sa[r['market_id']].append(r)
    for r in audited: aa[int(r['market_id'])].append(r)
    h3=history3(audited);markets=[]
    for m in sorted(sa):
        rows=sa[m]; amap={int(r['sample_ms']):r for r in aa[m]}; n=len(rows)
        fresh=sum(bool(amap.get(r['sample_ms'],{}).get('fresh_2s')) for r in rows)
        hist=sum(bool(h3.get((m,r['sample_ms']))) for r in rows)
        fr,hr=fresh/n,hist/n
        markets.append({'marketId':m,'rows':n,'firstSampleTaipei':_fmt(min(r['sample_ms'] for r in rows)),'lastSampleTaipei':_fmt(max(r['sample_ms'] for r in rows)),'regimes':dict(Counter(r['regime'] for r in rows)),'retained8778Updates':len(updates.get(m,[])),'freshCurrent2sRate':fr,'predictionHistory3sSupportedRate':hr,'developmentMarketExcluded':m in DEV,'eligible':m not in DEV and n>=30 and fr>=.80 and hr>=.65})
    markets.sort(key=lambda x:(not x['eligible'],-x['predictionHistory3sSupportedRate'],-x['freshCurrent2sRate'],x['marketId']))
    d=decide(markets)
    report={'version':VERSION,'policy':{'purpose':'Coverage-only gate before independent validation of RAW Prediction REPAIR signal','primaryClock':'received_at_ms strict-past','developmentMarketsExcluded':sorted(DEV),'noModelFitting':True,'noHyperparameterTuning':True,'noLiveTradingChanges':True},'source':{'fixedGridRowsAll':len(states),'marketsAll':len(sa)},'decision':d,'eligibleMarkets':[m for m in markets if m['eligible']],'allMarkets':markets}
    out=a.out.resolve();out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'version':VERSION,'decision':d,'eligibleMarkets':report['eligibleMarkets'],'report':str(out)},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
