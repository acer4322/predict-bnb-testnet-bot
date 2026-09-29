"""Research-only R87 extension; inherited active repair and legality unchanged."""
from controller_r87 import Controller as Previous
from net_world import share_fee
from responsibility import prospective
from attempt_memory import AttemptMemory, effective_config, extra_cost


class Controller(Previous):
    def __init__(self, arm, latency_ms):
        self.search_config = effective_config(arm) if isinstance(arm, dict) else None
        super().__init__('OBSERVER_ONLY' if self.search_config is not None else arm, latency_ms)
        self.attempt_memory = AttemptMemory(share_fee)
        self.formula_forecasts = []
        self.last_attempt_view = None

    def record_new(self, key, op, observation):
        super().record_new(key, op, observation)
        self.attempt_memory.register(key, op, observation)

    def observe_receipts(self, rows, now):
        super().observe_receipts(rows, now)
        self.attempt_memory.consume(rows, now)

    def observe_terminal(self, key, status, filled, now):
        self.attempt_memory.terminal(key, status, filled, now)

    def decide(self, observation):
        if self.search_config is not None or getattr(self, 'observer_enabled', False):
            self.last_attempt_view = self.attempt_memory.snapshot(observation, self.cost_model, self.entry_seconds)
        return super().decide(observation)

    def gain(self, observation, side, qty, price, route, exclude=None):
        value = super().gain(observation, side, qty, price, route, exclude)
        if self.search_config is None or route != 'PASSIVE':
            return value
        risk = prospective(observation, side, qty, exclude)['new_unpaired_same_side']
        if risk <= 0:
            return value
        view = self.attempt_memory.snapshot(observation, self.cost_model, self.entry_seconds)
        book = observation['book']
        uncertainty = max(0., observation['age_charge'], (book['best_ask'] - book['best_bid']) / 2.)
        charge = extra_cost(self.search_config, view['sides'][side], uncertainty, risk)
        self.formula_forecasts.append(dict(ms=observation['now_ms'], side=side, qty=qty, price=price,
            excluded_owner=exclude, base_value=value, extra_charge=charge, value=value-charge,
            uncertainty_per_share=uncertainty, new_unpaired_risk=risk, history=view['sides'][side]))
        return value - charge
