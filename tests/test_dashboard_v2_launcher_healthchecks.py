from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "start-dashboard-v2.ps1"


def _text() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_launcher_health_checks_do_not_use_powershell_webresponse_body():
    text = _text()
    assert "Invoke-WebRequest -Uri" not in text
    assert "Invoke-RestMethod -Uri" not in text
    assert "curl.exe" in text
    assert "--connect-timeout 1" in text
    assert "--max-time $TimeoutSeconds" in text


def test_backend_bridge_failure_does_not_define_vite_staleness():
    text = _text()
    assert "Test-DashboardV2Proxy" not in text
    assert "vite.config.ts is newer than the running Vite process" in text
    assert "backend bridge health is checked separately" in text
    assert "Only after every direct backend is healthy do we test the Vite proxies" in text


def test_launcher_names_8768_as_paper_strategy_service():
    text = _text()
    assert 'Name = "8768 Paper strategies"' in text
    assert "8768 Paper/research strategy state" in text
