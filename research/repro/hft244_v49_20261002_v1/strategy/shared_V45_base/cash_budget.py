from roles_runtime import roles
'Diagnostic cash budget for weak-side orders, with confirmed profit only.'
import math

def budget(inv, paid, pending_down_cash, price, retention, step):
    assert retention == 0.0, 'Cash gate OFF, not 100% gross allowance'
    return dict(confirmed_up_gross=inv[roles.strong] - paid[roles.strong], paid=dict(paid), pending_down_cash=pending_down_cash, retention=0.0, available_cash=None, quantity_cap=None, cash_budget_enabled=False)

def make_capacity(base):

    class ProfitBudget(base):

        def __init__(self, selection, mode, episode_mode, retention):
            super().__init__(selection, mode, episode_mode)
            assert retention == 0.0
            self.retention = retention
            self.budget_rows = []

        def cap(self, frame, side, price, qty, draft):
            original = super().cap(frame, side, price, qty, draft)
            if side != roles.weak:
                return original
            paid = {s: math.fsum((float(c.payment) + float(c.fees) for c in draft.carriers.values() if draft.grants[c.parent_id].side == s)) for s in (roles.strong, roles.weak)}
            pending = math.fsum((max(0.0, float(c.qty) - float(c.filled)) * float(c.limit) for c in draft.carriers.values() if c.state != 'TERMINAL' and draft.grants[c.parent_id].side == roles.weak))
            inv = {s: math.fsum((float(c.filled) for c in draft.carriers.values() if draft.grants[c.parent_id].side == s)) for s in (roles.strong, roles.weak)}
            info = budget(inv, paid, pending, price, self.retention, float(frame['world_profile']['quantity_step']))
            admitted = original
            self.budget_rows.append(dict(t=int(frame['t']), side=side, price=price, requested=qty, old_quantity_cap=original, admitted=admitted, inv=inv, **info))
            return admitted
    return ProfitBudget

def self_test():
    row = budget(dict(**{roles.strong: 100.0}, **{roles.weak: 40.0}), dict(**{roles.strong: 80.0}, **{roles.weak: 30.0}), 50.0, 0.07, 0.0, 0.01)
    assert row['quantity_cap'] is None and row['available_cash'] is None and (not row['cash_budget_enabled'])
