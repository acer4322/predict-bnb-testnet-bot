"""Replay recorded route decisions only; never create new simulated market paths."""
import sys
from prepare_btc5m_transfer_structural_v1 import *
from run_btc5m_transfer_structural_worker_v1 import jobs
from verify_btc5m_transfer_components_v1 import same
from hft244_pair_route_legality_v1 import crossing_owners


def main():
    sys.path.insert(0,str(PACKAGE));import roles_runtime
    roles=roles_runtime.roles
    commitment=load('check_role_commitment',PACKAGE/'commitment_repair.py')
    coordination=load('check_role_coordination',PACKAGE/'coordination.py')
    scope=load('check_role_scope',PACKAGE/'maintenance_scope.py')
    counts=dict(commitment=0,coordination=0,extra_maintenance=0,scope_maintenance=0)
    for j in jobs()[1:]:
        folder=R/'lan_worker_returns'/j['job_id'];n=read(folder/'result.json');tr=read(folder/'clock_trace.json.gz')
        strong=n['clock_smoke']['selected_direction'];roles.configure('KNOWN_FINAL_DIRECTION',strong)
        for row in tr['commitment_repair_rows']:
            expected=commitment.decide(row['state'],row['original_operations'],row['quote']['raw_price'],row['ask'],
                row['active_confirmed'],row['outstanding'],crossing_owners)
            for k,v in expected.items():same(v,row[k])
            counts['commitment']+=1
        previous=None;seen=False;episode=None
        for row in tr['coordination_rows']:
            state=row['state'];confirmed=row['active_confirmed']
            if episode is None:episode=coordination.detect(previous,state,confirmed,seen,row['t'])
            if confirmed and previous and state['inv'][roles.weak]>previous['inv'][roles.weak]+1e-8:seen=True
            previous={k:state[k] for k in ('inv','cost','payoff')}
            expected=coordination.decide(state,row['original_operations'],row['active_ask'],row['visible_depth'],episode,confirmed,crossing_owners)
            for k,v in expected.items():same(v,row[k])
            counts['coordination']+=1
        same(episode,tr['coordination_episode'])
        for row in tr['commitment_maintenance_rows']:
            q=commitment.legal_quote(row['quote']['raw_price'],row['quote']['ask'])
            same(q,row['quote']);assert row['new_stale']==(abs(row['limit']-q['price'])>row['threshold']+1e-9)
            counts['extra_maintenance']+=1
        for row in tr['maintenance_scope_rows']:
            expected=scope.decide(row['quote']['raw_price'],row['quote']['ask'],row['limit'],row['threshold'],row['is_extra'],commitment.legal_quote)
            for k,v in expected.items():same(v,row[k])
            counts['scope_maintenance']+=1
        for name in ('opportunity_submissions','coordination_submissions','commitment_repair_submissions'):
            assert all(o['side']==roles.weak and o['parent_id']==roles.pid(roles.weak) for o in tr[name])
    out=dict(status='PASS',rows=counts,side_and_parent_mapping=True,model_fits=0,new_native_jobs=0)
    dump('CONTINUATION_AUDIT',out);print(json.dumps(out))


if __name__=='__main__':main()
