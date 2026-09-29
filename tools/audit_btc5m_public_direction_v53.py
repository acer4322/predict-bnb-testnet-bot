"""Read-only full receipt, public authority, plan and physical-bank audit."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
import sys
from prepare_btc5m_public_direction_v53 import PACKAGE, R, STEM, dump, sha
from run_btc5m_public_direction_v53 import jobs,w
from verify_btc5m_active_repair_opportunity_v1 import receipt_auditor,canonical_legs
from verify_btc5m_transfer_components_v1 import same
from hft244_pair_route_legality_v1 import crossing_owners
import btc5m_dynamic_inventory_native_v1 as compiler


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text(encoding='utf-8'))


def audit(index):
    j=jobs()[index];folder=R/'lan_worker_returns'/j['job_id']
    n=read(folder/'result.json')
    if n['status']!='COMPLETE':
        out=dict(execution_status='FAIL',economic_status='UNKNOWN',error=n.get('error'),failure_capture=n.get('failure_capture'))
        w.save(j,'AUDIT',out);return out
    tr=read(folder/'clock_trace.json.gz');m=read(PACKAGE/'manifest.json');clock=n['clock_smoke']
    assert all(sha(PACKAGE/k)==h for k,h in m['files'].items())
    assert clock['manifest_sha256']==sha(PACKAGE/'manifest.json')
    assert hashlib.sha256(gzip.decompress((folder/'clock_trace.json.gz').read_bytes())).hexdigest()==clock['trace_payload_sha256']
    assert n['worker']=='DESKTOP-JIERAGF' and n['safety_gate']['pass'] and n['execution_accounting_valid'] and n['unresolved_owners']==0
    assert not any(n[k] for k in ('target_runtime_access','target_direction_input','oracle','cash_budget_enabled','runtime_eligible'))
    assert n['capital_cap'] is None and n['theta']==m['theta'] and clock['amplitude_mode']=='CURRENT_PUBLIC_BOOK' and clock['held_amplitude'] is None
    assert read(w.artifact(j,'POSTCHECK'))['status']=='PASS'
    c=compiler.compile_policy(PACKAGE,'NO_DIRECTION','PUBLIC_MARKET',True);src=c['source']
    remote='C:\\BTC5M-worker\\.lan_worker_v1\\staging\\'+PACKAGE.name
    for a,b in [(str(PACKAGE).replace('\\','\\\\'),remote.replace('\\','\\\\')),(str(PACKAGE),remote),(PACKAGE.as_posix(),remote.replace('\\','/'))]:src=src.replace(a,b)
    assert hashlib.sha256(src.encode()).hexdigest()==clock['transformed_source_sha256']
    receipt=receipt_auditor()(n,tr);legs=canonical_legs(n,tr)
    from verify_btc5m_dynamic_inventory_native_v1 import bank_audit
    physical_repair_audit=bank_audit(tr,n,c)
    inp=read(PACKAGE/f'inputs/public_{j["market"]}.json.gz');start=inp['market']['window_start_ms'];end=inp['market']['window_end_ms']
    times=[b['received_ms'] for b in inp['books']]
    assert clock['actual_replay_frames']==len(times)
    assert [p['t'] for p in tr['plans'][:len(times)]]==times
    assert all(p['t']>=max(end,times[-1]) and not any(o['kind']=='NEW' for o in p['operations']) for p in tr['plans'][len(times):])
    assert len(tr['plans'])==len(tr['bridge_frames'])==len(tr['direction_decisions'])
    intents={r['index']:r for r in tr['intent']};previous=None;births={};switches=[];mismatch=0;directional_new=Counter()
    for ordinal,(f,p,r) in enumerate(zip(tr['bridge_frames'],tr['plans'],tr['direction_decisions'])):
        assert f['t']==p['t']==r['t'] and f['index']==r['index']
        book=f['book'];bid=max(map(float,book['bids'])) if book['bids'] else None;ask=min(map(float,book['asks'])) if book['asks'] else None
        # Reconstruct from current book, independent of the imported actor helper.
        valid=bid is not None and ask is not None and 0<bid<ask<1
        score=0.
        if valid:
            bq=float(next(v for k,v in book['bids'].items() if float(k)==bid));aq=float(next(v for k,v in book['asks'].items() if float(k)==ask))
            score=m['theta'][1]*(bid+ask-1)+m['theta'][2]*(bq-aq)/max(1.,bq+aq)
        side=('UP' if score>1e-12 else 'DOWN' if score<-1e-12 else previous) if valid else previous
        amplitude=math.tanh(abs(score)) if valid else 0.
        if not ordinal:side=None
        assert side==r['side']==f['selected'] and r['previous']==previous
        state=f['state'];inv=state['inv'];majority='UP' if inv['UP']>inv['DOWN']+1e-8 else 'DOWN' if inv['DOWN']>inv['UP']+1e-8 else None
        mismatch+=side is not None and majority is not None and side!=majority
        if previous is not None and side!=previous:
            switches.append(dict(seconds=(r['t']-start)/1000,index=r['index'],previous=previous,side=side,inventory=inv,
                owners=len(state['owners']),pending_qty=state['pending_qty'],new_sides=[o['side'] for o in p['operations'] if o['kind']=='NEW']))
        previous=side
        if r['index'] in intents:
            it=intents[r['index']];same(it['applied_exposure'],amplitude)
            same(it['physical_signed_exposure'],amplitude*(1 if side=='UP' else -1))
        owners=[dict(key=o['key'],side=o['side'],price=o['limit']) for o in state['owners']]
        for s in ('UP','DOWN'):
            same(sum(o['qty'] for o in state['owners'] if o['side']==s),state['pending_qty'][s])
            same(sum(o['qty']*o['limit'] for o in state['owners'] if o['side']==s),state['pending_cash'][s])
        for o in p['operations']:
            if o['kind']=='CANCEL':
                assert f['cancellable'].get(o['key'])
                assert next(z for z in state['owners'] if z['key']==o['key'])['state'] not in ('UNKNOWN','CANCEL_PENDING','TERMINAL')
                continue
            assert o['kind']=='NEW' and o['key'] not in births and start<=p['t']<end
            assert o['parent_id']==(1 if o['side']=='UP' else 2) and o['qty']*o['price']>=1-1e-8
            source=inp['books'][ordinal];assert source['source_ms']<=p['t'] and source['received_ms']==p['t']
            physical_ask=source['best_ask'] if o['side']=='UP' else round(1-source['best_bid'],10)
            if o['route']=='PASSIVE':assert o['qty']==15. and o['price']<physical_ask-1e-10
            else:assert o['price']>=physical_ask-1e-8
            assert not crossing_owners(o['side'],o['price'],owners)
            owners.append(dict(key=o['key'],side=o['side'],price=o['price']))
            birth=tr['birth_provenance'][o['key']]
            for k,v in o.items():same(v,birth[k])
            assert birth['bank']==side
            births[o['key']]=birth
            if birth['purpose']=='ADD':directional_new[o['side']]+=1
    assert set(births)==set(tr['birth_provenance'])
    active=[b for b in births.values() if b['route']=='ACTIVE'];assert len(active)==n['active_native_submits']<=5
    roles=sys.modules['roles_runtime'].roles
    bank_counts={}
    for side,bank in tr['banks'].items():
        roles.configure('KNOWN_FINAL_DIRECTION',side)
        weak=roles.weak
        for sub in bank['general_finite_active_submissions']:
            assert sub['side']==weak and births[sub['key']]['bank']==side
        served=set();prev=None
        for row in bank['general_finite_active_rows']:
            progress=dict(work_id=row['work_id'],status='ACTIVE' if row['work_id'] is not None else None,
                acquired_since_birth=row['acquired_since_birth'],remaining_confirmed=row['remaining_confirmed'])
            expected=c['general_finite_active'].decide(row['state'],progress,row['original_operations'],row['active_ask'],row['visible_depth'],prev,crossing_owners)
            for k in ('same_work','released_reservation','unreserved_after_plan','pending_qty_after_plan','quantity','conflicts'):
                same(row[k],expected[k])
            prev=dict(work_id=row['work_id'],pending_weak=row['state']['pending_qty'][weak],acquired=row['acquired_since_birth'])
        for work in bank['demand_final']['works']:
            assert work['status']!='ACTIVE'
        bank_counts[side]=dict(works=len(bank['demand_final']['works']),general_active=len(bank['general_finite_active_submissions']))
    payoff={s:n['final_inventory'][s]-n['final_cost'] for s in ('UP','DOWN')}
    out=dict(execution_status='PASS',market=j['market'],job_id=j['job_id'],receipt_audit=receipt,
        public_direction_changes=len(switches),public_direction_disagrees_with_held_majority_frames=mismatch,
        switches=switches,directional_new=dict(directional_new),physical_banks=bank_counts,physical_repair_audit=physical_repair_audit,
        conditional_pnl=payoff,final_inventory=n['final_inventory'],final_cost=n['final_cost'],active_count=len(active),
        source_frames=len(times),raw_fill_legs=len(legs),elapsed_seconds=n['elapsed_seconds'],
        manifest_sha256=sha(PACKAGE/'manifest.json'),result_sha256=sha(folder/'result.json'),
        economic_status='CONSUMED_DIAGNOSTIC_NOT_GENERALIZATION',terminal_transport_time='NOT_INFERRED_FROM_EOF')
    w.save(j,'AUDIT',out)
    print(json.dumps({k:v for k,v in out.items() if k not in ('switches','receipt_audit')}))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',type=int,default=0);a=p.parse_args();audit(a.index)
