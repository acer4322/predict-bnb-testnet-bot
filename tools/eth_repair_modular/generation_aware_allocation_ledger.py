from __future__ import annotations
from dataclasses import dataclass

from tools.allocation_ledger_v2 import EPS, SharedParentDebtAllocationLedgerV2


@dataclass(frozen=True)
class GenerationDebtAttachResult:
    parent_id: int
    token: str
    added_debt: float
    initial_debt_before: float
    initial_debt_after: float
    remaining_debt_before: float
    remaining_debt_after: float
    applied: bool
    reason: str


class GenerationAwareSharedParentDebtAllocationLedgerV3(SharedParentDebtAllocationLedgerV2):
    """V2 Repair-first allocation plus idempotent debt attachment for a new
    responsibility generation on the same physical Repair parent.

    Existing paid debt is never reset. A new generation increases both the
    aggregate initial debt and the still-unpaid remaining debt exactly once.
    Carrier cumulative-fill idempotence and overflow semantics remain V2.
    """

    name = 'generation_aware_shared_parent_debt_allocation_v3'

    def __init__(self):
        super().__init__()
        self.generation_tokens: set[tuple[int, str]] = set()
        self.generation_attachments: list[GenerationDebtAttachResult] = []

    def attach_generation_debt(self, parent_id: int, token: str, added_debt: float) -> GenerationDebtAttachResult:
        pid = int(parent_id); tok = str(token); add = max(0.0, float(added_debt))
        key = (pid, tok)
        st = self.parents.get(pid)
        if st is None:
            out = GenerationDebtAttachResult(pid, tok, add, 0.0, 0.0, 0.0, 0.0, False, 'PARENT_NOT_REGISTERED')
            self.generation_attachments.append(out); return out
        if key in self.generation_tokens:
            out = GenerationDebtAttachResult(pid, tok, add, st.initial_debt, st.initial_debt, st.remaining_debt, st.remaining_debt, False, 'IDEMPOTENT_ALREADY_ATTACHED')
            self.generation_attachments.append(out); return out
        if add <= EPS:
            out = GenerationDebtAttachResult(pid, tok, add, st.initial_debt, st.initial_debt, st.remaining_debt, st.remaining_debt, False, 'NO_POSITIVE_DEBT')
            self.generation_attachments.append(out); return out
        ib=float(st.initial_debt); rb=float(st.remaining_debt)
        st.initial_debt=ib+add; st.remaining_debt=rb+add
        self.generation_tokens.add(key)
        out = GenerationDebtAttachResult(pid, tok, add, ib, st.initial_debt, rb, st.remaining_debt, True, 'GENERATION_DEBT_ATTACHED')
        self.generation_attachments.append(out); return out

    def describe_generation_attachments(self):
        return [a.__dict__.copy() for a in self.generation_attachments]
