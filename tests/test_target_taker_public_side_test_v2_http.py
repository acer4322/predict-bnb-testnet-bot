from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from predict_bot import target_taker_public_side_test_v2 as v2


class _DummyTest:
    def health_snapshot(self):
        return {"ok": True, "version": v2.VERSION, "port": v2.PORT}

    def snapshot(self):
        return {"ok": True, "version": v2.VERSION, "state": "TEST"}


def test_v2_handler_binds_runtime_under_test_attribute() -> None:
    handler = type("TargetTakerPublicSideTestV2HttpRegressionHandler", (v2.Handler,), {"test": _DummyTest()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        with urlopen(f"http://{host}:{port}/state", timeout=2) as response:
            state = json.loads(response.read().decode("utf-8"))

        assert health["ok"] is True
        assert health["version"] == v2.VERSION
        assert state["ok"] is True
        assert state["version"] == v2.VERSION
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
