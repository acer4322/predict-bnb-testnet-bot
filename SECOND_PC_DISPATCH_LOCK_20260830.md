# Second PC Dispatch Status — 2026-08-30

Current status: **UNLOCKED BY EXPLICIT USER AUTHORIZATION**.

The user explicitly re-authorized use of the second PC for BTC 5M Lab research on 2026-08-30 after previously disabling it.

Operational rule now:
- Heavy HFT / batch simulation / training MAY be dispatched to the second PC.
- Main host should still prefer lightweight orchestration, synthesis, and small diagnostics.
- If the user later revokes permission again, stop all second-PC dispatch immediately.
- Existing research boundaries remain unchanged: strict-past, realistic-HFT/no dream fill, Protection deterministic, no live R3/R3.1/8781 mutation without explicit authorization.
