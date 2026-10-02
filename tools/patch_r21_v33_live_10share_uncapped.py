from pathlib import Path
p=Path('src/predict_bot/unified_controller_r2_r21_echtgeld_v1.py')
s=p.read_text(encoding='utf-8')
s=s.replace('VERSION = "UNIFIED_R2_R21_SEMANTIC_COOPERATION_ECHTGELD_V2"','VERSION = "UNIFIED_R2_R21_V33_INFORMATION_ONLY_ECHTGELD_10SHARE_V3"')
s=s.replace('R2_R21_LIVE_SHARES = 10.0','R2_R21_LIVE_SHARES = 10.0\nR2_R21_NOTIONAL_CAP_ENABLED = False')
old='''        before_keys = set(self.orders)\n        before_metric = int(self.current_metrics["makerPlacements"])\n        made = super()._add_order(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack=allow_stack, bypass_guard=bypass_guard)'''
new='''        before_keys = set(self.orders)\n        before_metric = int(self.current_metrics["makerPlacements"])\n        # R2+R2.1 V3.3 Echtgeld is quantity-limited (10 shares/order), not CAP100 notional-limited.\n        # Temporarily disable the inherited CAP100 planning gate only while Frozen R2 plans this order.\n        _cap_total, _cap_maker = base.CAP_TOTAL_USDT, base.CAP_MAKER_BUDGET_USDT\n        base.CAP_TOTAL_USDT = float("inf")\n        base.CAP_MAKER_BUDGET_USDT = float("inf")\n        try:\n            made = super()._add_order(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack=allow_stack, bypass_guard=bypass_guard)\n        finally:\n            base.CAP_TOTAL_USDT, base.CAP_MAKER_BUDGET_USDT = _cap_total, _cap_maker'''
if old in s: s=s.replace(old,new)
old2='''        if spent + committed + need > base.CAP_TOTAL_USDT + 1e-9:\n            self.current_metrics["takerCapBlocks"] += 1\n            self.run_metrics["takerCapBlocks"] += 1\n            return False'''
if old2 in s:
 s=s.replace(old2,'''        # No CAP100 notional ceiling in this deployment. Quantity remains exactly 10 shares.\n        if abs(float(base.SHARES) - R2_R21_LIVE_SHARES) > 1e-9:\n            self.last_error = f"R2_R21 live quantity invariant violated: {base.SHARES}"\n            return False''')
s=s.replace('"capitalPolicy": "CAP100_10SHARE_MAKER80_TAKER20",','"capitalPolicy": "R2_R21_10SHARE_NO_NOTIONAL_CAP",')
s=s.replace('candidate="R2_R21_SEMANTIC_COOPERATION_ECHTGELD_10SHARE_V2",','candidate="R2_R21_V33_INFORMATION_ONLY_ECHTGELD_10SHARE_V3",')
s=s.replace('"semanticAcceptance": "PASS_CURRENT_R21_R2_SEMANTIC_COOPERATION_RETEST",','"semanticAcceptance": "PASS_V33_L1_L11_AND_RANDOMIZED_CHAOS_INFORMATION_ONLY",')
s=s.replace('"learnedActiveResponseHead": "NOT_PROMOTED",','"learnedActiveResponseHead": "NOT_PROMOTED",\n            "v33ContextSequence": True,\n            "notionalCapEnabled": False,')
s=s.replace('"totalCapUsdt": base.CAP_TOTAL_USDT,','"totalCapUsdt": None,')
s=s.replace('"makerBudgetUsdt": base.CAP_MAKER_BUDGET_USDT,','"makerBudgetUsdt": None,')
s=s.replace('"totalRemainingUsdt": max(0.0, base.CAP_TOTAL_USDT - spent - committed - orphan_committed),','"totalRemainingUsdt": None,')
s=s.replace('"makerRemainingUsdt": max(0.0, base.CAP_MAKER_BUDGET_USDT - self.maker_spent_notional - committed - orphan_committed),','"makerRemainingUsdt": None,')
s=s.replace('payload={"feeBps": base.FEE_BPS, "paperOnly": False, "liveOrdersAffected": True, "capitalCapUsdt": base.CAP_TOTAL_USDT},','payload={"feeBps": base.FEE_BPS, "paperOnly": False, "liveOrdersAffected": True, "capitalCapUsdt": None, "configuredShares": R2_R21_LIVE_SHARES},')
s=s.replace('result["policy"]["maker"] = "Frozen R2 Maker/passive-repair logic; runtime quantity 10 shares; $80 Maker budget"','result["policy"]["maker"] = "Frozen R2 Maker/passive-repair logic; runtime quantity exactly 10 shares; no strategy notional cap"')
p.write_text(s,encoding='utf-8')

b=Path('src/predict_bot/r21_echtgeld_state_bridge_v1.py')
t=b.read_text(encoding='utf-8')
t=t.replace('VERSION = "R21_ECHTGELD_STATE_BRIDGE_V2"','VERSION = "R21_ECHTGELD_STATE_BRIDGE_V33_CONTEXT_SEQUENCE"')
if 'self.previous_summary' not in t:
 t=t.replace('        self.filtered_foreign_events = 0\n','        self.filtered_foreign_events = 0\n        self.previous_summary: dict[str, Any] | None = None\n')
 t=t.replace('        self.last_event_seq = 0\n\n    def belongs_to_source','        self.last_event_seq = 0\n        self.previous_summary = None\n\n    def belongs_to_source')
 needle='''        return {\n            "version": "R2.1",'''
 insert='''        current_summary = {\n            "asOfMs": int(at_ms),\n            "ownershipState": ownership,\n            "terminalCertainty": not current_children,\n            "situationCode": situation,\n            "remainingObligation": copy.deepcopy(residual),\n            "effectiveTargetRevision": copy.deepcopy(self.target_revision),\n            "latestEventId": None if not visible_incidents else visible_incidents[-1]["eventId"],\n        }\n        previous_summary = copy.deepcopy(self.previous_summary)\n        sequence_summary = {\n            "informationOnly": True,\n            "actionAuthority": False,\n            "previous": previous_summary,\n            "current": copy.deepcopy(current_summary),\n            "elapsedSincePriorObservationMs": None if previous_summary is None else max(0, int(at_ms)-_integer(previous_summary.get("asOfMs"))),\n            "ownershipChanged": previous_summary is not None and previous_summary.get("ownershipState") != ownership,\n            "situationChanged": previous_summary is not None and previous_summary.get("situationCode") != situation,\n            "targetRevisionChanged": previous_summary is not None and previous_summary.get("effectiveTargetRevision") != self.target_revision,\n        }\n        self.previous_summary = copy.deepcopy(current_summary)\n        return {\n            "version": "R2.1",'''
 assert needle in t
 t=t.replace(needle,insert)
 t=t.replace('            "incidents": visible_incidents[-16:],\n','            "incidents": visible_incidents[-16:],\n            "stateSequenceSummary": sequence_summary,\n')
b.write_text(t,encoding='utf-8')
print('patched')