from pathlib import Path
p=Path('start-unified-controller-r2-r21-echtgeld-v1.ps1')
s=p.read_text(encoding='utf-8')
s=s.replace('$ExpectedVersion = "UNIFIED_R2_R21_SEMANTIC_COOPERATION_ECHTGELD_V2"','$ExpectedVersion = "UNIFIED_R2_R21_V33_INFORMATION_ONLY_ECHTGELD_10SHARE_V3"')
s=s.replace('if ([string]$State.r2r21Bundle.semanticAcceptance -ne "PASS_CURRENT_R21_R2_SEMANTIC_COOPERATION_RETEST") {','if ([string]$State.r2r21Bundle.semanticAcceptance -ne "PASS_V33_L1_L11_AND_RANDOMIZED_CHAOS_INFORMATION_ONLY") {')
s=s.replace('Write-Host "  Quantity       : 10 shares; maker/taker minimum notional enforced by 8781"','Write-Host "  Quantity       : exactly 10 shares; no strategy notional cap; maker/taker minimum notional enforced by 8781"')
p.write_text(s,encoding='utf-8')
print('patched launcher')