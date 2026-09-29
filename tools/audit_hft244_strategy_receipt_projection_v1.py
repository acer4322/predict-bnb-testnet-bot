"""Execute actual legacy process AST on saved synthetic snapshots, no engine/model."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'tools/run_eth_dagger60_smoke_v1.py'
evidence=ROOT/'data/research/lan_worker_returns/hft244-v31-extended-20260910-v1/PHASE.json'


def main():
    text=source.read_text(); tree=ast.parse(text)
    price=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='fill_price')
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Sim')
    process=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='process')
    scope={'EPS':1e-9};exec(compile(ast.Module(body=[price,process],type_ignores=[]),str(source),'exec'),scope)
    data=json.loads(evidence.read_text()); assert data['verdict']=='PHASE_PASS'
    rows=[]
    for r in data['rows']:
        if r['scenario']!='TAKER_TWO_PRICES': continue
        s=r['snapshots'][0]; side='UP' if r['side']=='BUY' else 'DOWN'
        assert s['label']=='ACK_OR_IMMEDIATE_FILL' and s['status']==3
        q=s['qty']-s['leaves']; price=s['lastExecPrice']
        snapshot=dict(cumExecQty=q,execPrice=price,status='FILLED')
        fake=SimpleNamespace(orders={'one':dict(side=side,price=.76,cum=0.)},fills=0,cost=0.,inv=0.)
        fake.snap=lambda order:snapshot
        def record(t,side,qty,px): fake.cost+=qty*px;fake.inv+=qty
        fake.record_fill=record
        scope['process'](fake,1700); once=(fake.cost,fake.inv,fake.fills)
        scope['process'](fake,1700);assert once==(fake.cost,fake.inv,fake.fills)
        expected=s['native']['trading_value'] if side=='UP' else q-s['native']['trading_value']
        assert abs(fake.cost-expected-.006)<1e-12 and abs(fake.inv-q)<1e-12
        rows.append(dict(side=side,qty=q,lastNativePrice=price,legacyCost=fake.cost,nativeDerivedContractCost=expected,costError=fake.cost-expected,repeatInert=True))
    assert len(rows)==2
    print(json.dumps(dict(verdict='LEGACY_CUM_DELTA_LAST_PRICE_MISPRICING_REPRODUCED',method='Actual process and fill_price AST on saved native snapshots; not full strategy replay',sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),evidenceSha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),rows=rows,newEngines=0,marketBE=0,training=False,runtimeChanged=False),indent=2))


if __name__=='__main__': main()
