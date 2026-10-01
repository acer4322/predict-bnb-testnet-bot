"""Finite continuation of one originally admitted pre-FLIP qualified repair."""
import atexit
from copy import deepcopy
import gzip
import json
import math
import os
from pathlib import Path

EPS = 1e-8


class Service:
    def __init__(self, mode):
        assert mode in ('OFF','ON')
        self.mode=mode
        self.work=None
        self.rows=[]
        self.orders=[]

    def birth(self, frame, state, planned, row, past_flip, key):
        if self.work is not None or past_flip:
            return
        side=row['side'];other='DOWN' if side=='UP' else 'UP'
        assert row['eligible'] and row['needed_qty']>0
        # Frozen proposal admits NEW quantity after all current plan reservations.
        target=state['inv'][side]+planned['pending_qty'][side]+row['needed_qty']
        assert target<=state['inv'][other]+EPS
        self.work=dict(side=side,other=other,status='ACTIVE',born_t=frame['t'],born_index=frame['index'],
            original_key=key,initial_inventory=state['inv'][side],initial_pending=planned['pending_qty'][side],
            admitted_new_quantity=row['needed_qty'],target=target,first_price=row['price'],
            first_full_target_cost=row['full_target_cost'],birth_state=deepcopy(state),birth_plan_state=deepcopy(planned))

    def observe(self, frame, state, weak, past_flip):
        w=self.work
        if w is None:return None
        side,other=w['side'],w['other']
        remaining=max(0.,w['target']-state['inv'][side])
        if w['status']=='ACTIVE':
            if remaining<=EPS:w['status']='CONFIRMED_TARGET_REACHED'
            elif past_flip or weak!=side:w['status']='WITHDRAWN_DIRECTION_CHANGED'
            elif frame['t']>=frame['end']:w['status']='WITHDRAWN_MARKET_END'
            elif state['payoff'][side]>=-EPS:w['status']='WITHDRAWN_WEAK_NONNEGATIVE'
            elif state['payoff'][other]<=EPS:w['status']='WITHDRAWN_FAVORABLE_NONPOSITIVE'
            elif state['inv'][other]<=state['inv'][side]+EPS:w['status']='WITHDRAWN_NET_DIRECTION_GONE'
            if w['status']!='ACTIVE':w.update(stopped_t=frame['t'],stopped_index=frame['index'])
        w.update(last_t=frame['t'],last_index=frame['index'],last_inventory=state['inv'][side],
                 remaining_confirmed=remaining,canonical_pending=state['pending_qty'][side])
        return w

    def at_cap(self, row, frame, state, planned, original_operations, weak, past_flip, original_count, step):
        """Called only when the original five-child count rejects an eligible proposal."""
        w=self.observe(frame,state,weak,past_flip)
        r=deepcopy(row)
        diagnostic=dict(t=frame['t'],index=frame['index'],mode=self.mode,state=deepcopy(state),
            planned=deepcopy(planned),original_operations=deepcopy(original_operations),
            original_count=original_count,original_proposal=deepcopy(row),work=deepcopy(w),
            eligible=False,reason='NO_ORIGINAL_QUALIFIED_WORK')
        if w is not None:
            remaining=max(0.,w['target']-state['inv'][w['side']])
            available=max(0.,remaining-planned['pending_qty'][w['side']])
            diagnostic.update(remaining_confirmed=remaining,all_plan_pending=planned['pending_qty'][w['side']],unreserved_goal=available)
            if w['status']!='ACTIVE':diagnostic['reason']=w['status']
            elif past_flip or row['side']!=w['side']:diagnostic['reason']='WRONG_PHYSICAL_SIDE'
            elif self.mode=='OFF':diagnostic['reason']='INERT'
            else:
                q=max(0.,round(math.floor((min(row['quantity'],available)+1e-9)/step)*step,8))
                if q<=EPS or q*row['price']<1.-EPS:diagnostic['reason']='FINITE_REMAINDER_RESERVED_OR_BELOW_MINIMUM'
                else:
                    cost=q*row['price']
                    assert row['worst_G']-cost>=row['retained_G_floor']-EPS
                    assert state['inv'][w['side']]+planned['pending_qty'][w['side']]+q<=w['target']+EPS
                    r.update(eligible=True,reason='QUALIFIED_FINITE_CONTINUATION',quantity=q,quoted_cost=cost,
                        after_G_if_filled=row['G']-cost,after_H_if_filled=row['H']+q-cost,
                        qualified_continuation=True,finite_target=w['target'],finite_goal_limited=q<row['quantity']-EPS,
                        depth_limited=q<row['needed_qty']-step)
                    diagnostic.update(eligible=True,reason=r['reason'],quantity=q,quoted_cost=cost,
                        retained_after=row['worst_G']-cost)
        self.rows.append(diagnostic)
        if diagnostic['eligible']:return r
        return dict(row,eligible=False,reason='ORIGINAL_COMBINED_ACTIVE_CAP')

    def accepted(self, frame, op, row):
        record=dict(t=frame['t'],index=frame['index'],**op,finite_target=self.work['target'])
        self.orders.append(record)
        self.rows[-1].update(new_key=op['key'],final_eligible=True,final_reason=row['reason'])

    def resolved(self, row):
        if self.rows and self.rows[-1]['index']==row['index'] and self.rows[-1]['t']==row['t']:
            self.rows[-1].update(final_eligible=bool(row['eligible']),final_reason=row['reason'])

    def save(self):
        out=os.environ.get('BTC5M_LAN_RESULT_DIR')
        if out and Path(out).is_dir():
            with gzip.GzipFile(filename=str(Path(out)/'qualified_work_trace.json.gz'),mode='wb',mtime=0) as f:
                f.write(json.dumps(dict(mode=self.mode,work=self.work,rows=self.rows,orders=self.orders),sort_keys=True,separators=(',',':'),allow_nan=False).encode())


SERVICE=Service(os.environ.get('V12G_QUALIFIED_CONTINUATION','OFF'))
atexit.register(SERVICE.save)
