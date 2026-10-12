from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from lens_api.gateway.balance_runner import run_balance_script
from lens_api.gateway.builtin_balance_scripts import (
    NEW_API_BALANCE_SCRIPT,
    SENSEAUDIO_BALANCE_SCRIPT,
    SUB2API_BALANCE_SCRIPT,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = self.server.routes.get(self.path.split("?", 1)[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def _serve(routes: dict):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.routes = routes
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_new_api_script_reads_account_balance() -> None:
    server = _serve(
        {
            "/api/status": {
                "success": True,
                "data": {"quota_per_unit": 500000, "quota_display_type": "USD"},
            },
            "/api/user/self": {
                "success": True,
                "data": {"quota": 250000, "used_quota": 250000},
            },
        }
    )
    try:
        outcome = run_balance_script(
            NEW_API_BALANCE_SCRIPT,
            api_key="sk-test",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            proxy="",
            timeout_seconds=10,
            params={"access_token": "access-token"},
        )
    finally:
        server.shutdown()
    assert outcome.ok
    assert outcome.total == "1.0000"
    assert outcome.balance == "0.5000"
    assert outcome.currency == "USD"


def test_senseaudio_script_reads_plan_and_wallet() -> None:
    server = _serve(
        {
            "/api/user/self": {
                "balance": 0,
                "account_info": {
                    "balance": -2198,
                    "enable_extra_usage": True,
                    "vouchers": [{"available": 20000000, "total": 20000000}],
                },
                "usage_infos": [
                    {"total_count": 10000, "used_count": 0, "pending_count": 0},
                    {"total_count": 10000, "used_count": 0, "pending_count": 0},
                ],
            },
            "/api/user/subscription/current_cycle": {
                "plan_points": 10000,
                "plan_point_remains": 10000,
            },
        }
    )
    try:
        outcome = run_balance_script(
            SENSEAUDIO_BALANCE_SCRIPT,
            api_key="sk-test",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            proxy="",
            timeout_seconds=10,
            params={"access_token": "session-token"},
        )
    finally:
        server.shutdown()
    assert outcome.ok, outcome.error_message
    assert outcome.total == "22.0000"
    assert outcome.balance == "21.9978"
    assert outcome.currency == "CNY"


def test_sub2api_script_reads_key_quota() -> None:
    server = _serve(
        {
            "/v1/usage": {
                "mode": "quota_limited",
                "unit": "USD",
                "quota": {"limit": 20, "used": 5, "remaining": 15, "unit": "USD"},
            }
        }
    )
    try:
        outcome = run_balance_script(
            SUB2API_BALANCE_SCRIPT,
            api_key="sk-test",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            proxy="",
            timeout_seconds=10,
        )
    finally:
        server.shutdown()
    assert outcome.ok
    assert outcome.total == "20.0000"
    assert outcome.balance == "15.0000"
    assert outcome.currency == "USD"


def test_sub2api_script_reads_wallet_balance() -> None:
    server = _serve(
        {"/v1/usage": {"mode": "unrestricted", "balance": 3.25, "unit": "USD"}}
    )
    try:
        outcome = run_balance_script(
            SUB2API_BALANCE_SCRIPT,
            api_key="sk-test",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            proxy="",
            timeout_seconds=10,
        )
    finally:
        server.shutdown()
    assert outcome.ok
    assert outcome.balance == "3.2500"
    assert outcome.total == "3.2500"
