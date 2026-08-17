# V2.7.7 guardrails

- Exclude the original four development markets and every Target market selected by V2.7.6.
- Reuse the frozen V2.7.4 RAW Prediction feature family and frozen EBM parameters.
- Scan only the newest OFFICIAL contiguous Target session; never bridge a >=30 minute hard gap.
- Preserve V2.7.6 joint-support thresholds.
- Gate on distinct strict-next REPAIR event timestamps, not positive fixed-grid seconds.
- Require >=8 unseen eligible markets, >=800 rows, >=10 distinct REPAIR events across >=5 markets before fitting.
- Primary replication evidence is positive-event LOMO macro AUC delta plus paired logloss delta; pooled AUC is secondary.
- Observation/research only. No live trading changes.
