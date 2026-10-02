from __future__ import annotations
from dataclasses import dataclass

EPS = 1e-9

@dataclass
class ResponsibilityGenerationEpochState:
    parent_id: int
    parent_side: str
    epoch: int = 0
    armed: bool = False
    arm_fill_base: float = 0.0
    arm_churn_base: int = 0
    attached_debt: float = 0.0
    paid_at_attach: float = 0.0
    active_owned: bool = False

    def _start_generation(
        self,
        *,
        debt: float,
        parent_fill_now: float,
        churn_now: int,
        paid_total_now: float,
    ) -> int:
        if debt <= EPS:
            raise ValueError("debt must be positive")
        self.epoch += 1
        self.armed = True
        self.arm_fill_base = float(parent_fill_now)
        self.arm_churn_base = int(churn_now)
        self.attached_debt = float(debt)
        self.paid_at_attach = float(paid_total_now)
        self.active_owned = False
        return self.epoch

    def start_new_parent_responsibility(
        self,
        *,
        initial_debt: float,
        parent_fill_now: float,
        churn_now: int,
        paid_total_now: float,
    ) -> int:
        """Start the execution epoch for a newly born authoritative Repair parent.

        This may be called exactly once for the physical parent.  The evidence
        bases are captured at responsibility birth, so pre-birth fill/churn/payment
        cannot satisfy later escalation.  A repeated birth call is an ownership
        error rather than an implicit evidence reset.
        """
        if self.epoch != 0 or self.armed:
            raise ValueError("new parent responsibility already started")
        return self._start_generation(
            debt=initial_debt,
            parent_fill_now=parent_fill_now,
            churn_now=churn_now,
            paid_total_now=paid_total_now,
        )

    def attach_existing_parent_responsibility(
        self,
        *,
        added_debt: float,
        parent_fill_now: float,
        churn_now: int,
        paid_total_now: float,
    ) -> int:
        """Attach a new responsibility generation to the same physical parent.

        This is ownership-neutral: parent identity is preserved.  The new epoch
        rebases fill/churn/payment evidence so pre-generation observations cannot
        satisfy the new generation's execution escalation trigger.
        """
        return self._start_generation(
            debt=added_debt,
            parent_fill_now=parent_fill_now,
            churn_now=churn_now,
            paid_total_now=paid_total_now,
        )

    def observe(self, *, parent_fill_now: float, churn_now: int, paid_total_now: float) -> dict:
        paid_since_epoch = max(0.0, float(paid_total_now) - self.paid_at_attach)
        fill_progress = max(0.0, float(parent_fill_now) - self.arm_fill_base)
        post_epoch_churn = max(0, int(churn_now) - self.arm_churn_base)
        payment_progress = paid_since_epoch > EPS or fill_progress > EPS
        return {
            "parentId": self.parent_id,
            "parentSide": self.parent_side,
            "epoch": self.epoch,
            "armed": self.armed,
            "postEpochChurn": post_epoch_churn,
            "paymentProgress": payment_progress,
            "paidSinceEpoch": paid_since_epoch,
            "fillProgressSinceEpoch": fill_progress,
            "activeOwned": self.active_owned,
        }

    def hard_active_evidence(self, *, parent_fill_now: float, churn_now: int, paid_total_now: float, required_disconnects: int) -> bool:
        obs = self.observe(parent_fill_now=parent_fill_now, churn_now=churn_now, paid_total_now=paid_total_now)
        if not self.armed or self.active_owned or obs["paymentProgress"]:
            return False
        return obs["postEpochChurn"] >= int(required_disconnects)
