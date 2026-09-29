$ErrorActionPreference='Stop'
$env:PYTHONWARNINGS='ignore'
$mids='1735612 1735539 1735536 1734910 1734907 1734644 1734624 1734621 1734563 1734156 1734153 1734149 1734137 1734134 1734066 1734022 1733917 1733780 1733772 1733768 1733763 1733715 1733708 1733611'
python tools/r4_scan_baseline_cross_candidates_checkpoint_v2.py $mids.Split(' ')
python tools/r4_collect_paired_forks_from_scan_v2.py
