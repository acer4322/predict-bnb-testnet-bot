"""Strict complete-cohort audit in addition to the supplied print-only comparer."""
import argparse
import json
import math
from pathlib import Path

STRATEGIES = ('FAV_TAKER','UNDER_TAKER')
FIELDS = ('pnl','cost','up','dn')

def index(rows, expected):
    result={}
    for row in rows:
        mid=int(row['market'])
        assert mid not in result, ('duplicate market',mid)
        assert row.get('inferred',False) is False, ('inferred winner',mid)
        assert all(math.isfinite(float(row[f])) for f in FIELDS), ('nonfinite result',mid)
        result[mid]=row
    assert set(result)==expected, {'missing':sorted(expected-set(result)), 'extra':sorted(set(result)-expected)}
    return result

def compare(cloud, local, expected):
    assert set(cloud)==set(local)==set(STRATEGIES)
    output={}
    for strategy in STRATEGIES:
        c,l=index(cloud[strategy],expected),index(local[strategy],expected)
        rows=[]
        for mid in sorted(expected):
            differences={f:float(l[mid][f])-float(c[mid][f]) for f in FIELDS}
            same=all(abs(d)<1e-6 for d in differences.values())
            rows.append({'market_id':mid,'pass':same,'local_minus_cloud':differences, 'winner_equal':l[mid].get('win')==c[mid].get('win'), 'classification_equal':l[mid].get('cls')==c[mid].get('cls')})
        output[strategy]={'pass_count':sum(r['pass'] for r in rows), 'fail_count':sum(not r['pass'] for r in rows), 'rows':rows, 'max_absolute_difference':{f:max(abs(r['local_minus_cloud'][f]) for r in rows) for f in FIELDS}}
    return {'status':'PASS' if all(r['fail_count']==0 for r in output.values()) else 'FAIL', 'markets':len(expected),'tolerance_exclusive':1e-6,'strategies':output}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('cloud');ap.add_argument('local');ap.add_argument('labels');ap.add_argument('--out',required=True)
    a=ap.parse_args()
    expected={int(r['market_id']) for r in json.loads(Path(a.labels).read_text())['records']}
    assert len(expected)==185
    result=compare(json.loads(Path(a.cloud).read_text()),json.loads(Path(a.local).read_text()),expected)
    Path(a.out).write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':result['status'],'markets':result['markets'], 'strategies':{s:{k:v for k,v in r.items() if k!='rows'} for s,r in result['strategies'].items()}}))

if __name__=='__main__':main()
