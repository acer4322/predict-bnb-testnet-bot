"""Fresh-process bootstrap checks: no services or real ledgers."""
from __future__ import annotations
import json,os,subprocess,sys
from pathlib import Path
import pytest
SRC=Path(__file__).resolve().parents[1]/"src"
KEY="PREDICT_BOT_BOOTSTRAP_PROFILE"


def child(code,profile="collector"):
    env=os.environ.copy()
    env["PYTHONPATH"]=str(SRC)
    env["PYTHONDONTWRITEBYTECODE"]="1"
    if profile is None: env.pop(KEY,None)
    else: env[KEY]=profile
    return subprocess.run([sys.executable,"-c",code],env=env,capture_output=True,text=True,timeout=30)


@pytest.mark.parametrize("module",["target_wallet_official_v1","target_wallet_official_v2","predict_wallet_shadow_observer_v4_23"])
def test_light_import(module):
    p=child("import importlib,json,sys; importlib.import_module('predict_bot."+module+"'); print(json.dumps(sorted(n for n in sys.modules if n.startswith('predict_bot'))))")
    assert p.returncode==0,p.stderr
    loaded=json.loads(p.stdout)
    assert len(loaded)<=5
    assert not any(n in loaded for n in ["predict_bot.live_trading","predict_bot.server","predict_bot._legacy_bootstrap"])


@pytest.mark.parametrize("module",["live_trading","server","_legacy_bootstrap","research_forward","target_taker_public_side_echtgeld_bridge_v1","unknown_module"])
def test_unaudited_module_blocked(module):
    p=child("import predict_bot,importlib; importlib.import_module('predict_bot."+module+"')")
    assert p.returncode!=0
    assert "Collector bootstrap refuses" in p.stderr


@pytest.mark.parametrize("profile",["","research","collectorr","LEGACY"])
def test_typo_rejected(profile):
    p=child("import predict_bot",profile)
    assert p.returncode!=0
    assert "Invalid PREDICT_BOT_BOOTSTRAP_PROFILE" in p.stderr


def test_reload_idempotent():
    p=child("import predict_bot,importlib,sys; n=len(sys.meta_path); importlib.reload(predict_bot); assert len(sys.meta_path)==n")
    assert p.returncode==0,p.stderr


@pytest.mark.parametrize("before,after",[("collector","legacy"),("legacy","collector")])
def test_mode_switch_rejected(before,after):
    p=child("import predict_bot,os,importlib; os.environ['"+KEY+"']='"+after+"'; importlib.reload(predict_bot)",before)
    assert p.returncode!=0
    assert "cannot change" in p.stderr


def test_environment_change_keeps_guard():
    p=child("import predict_bot,os,importlib; os.environ.pop('"+KEY+"'); importlib.import_module('predict_bot.live_trading')")
    assert p.returncode!=0
    assert "Collector bootstrap refuses" in p.stderr


def test_inherited_profile_blocks_trading():
    p=child("import predict_bot,subprocess,sys; p=subprocess.run([sys.executable,'-c','import predict_bot.live_trading'],capture_output=True,text=True); assert p.returncode!=0; assert 'Collector bootstrap refuses' in p.stderr")
    assert p.returncode==0,p.stderr


@pytest.mark.parametrize("profile",[None,"legacy"])
def test_legacy_still_loads_protections(profile):
    p=child("import predict_bot,sys; assert 'predict_bot.live_trading' in sys.modules; assert 'predict_bot.maximum_net_loss_guard_v2_patch' in sys.modules; assert 'predict_bot.research_strategy_registry_patch' in sys.modules",profile)
    assert p.returncode==0,p.stderr


def test_retired_stub_is_compatible_but_not_a_launcher_target():
    p=child("from predict_bot import predict_wallet_taker_signal_collector as stub; "
            "from predict_bot._bootstrap_profiles import COLLECTOR_ENTRYPOINTS; "
            "assert stub.state()['dataCollectionEnabled'] is False; "
            "assert stub.__name__ not in COLLECTOR_ENTRYPOINTS")
    assert p.returncode==0,p.stderr


def test_launcher_refuses_live_target():
    root=SRC.parent
    p=subprocess.run([sys.executable,str(root/'tools/run_predict_bot_collector_light_v1.py'),
                      '--module','predict_bot.live_trading','--check-import'],
                      capture_output=True,text=True,timeout=20)
    assert p.returncode!=0
    assert 'invalid choice' in p.stderr
