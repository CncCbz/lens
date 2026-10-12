from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BalanceScriptOutcome:
    ok: bool
    total: str = ""
    balance: str = ""
    currency: str = ""
    error_message: str = ""


def clamp_balance_timeout(timeout_seconds: int) -> int:
    return min(max(int(timeout_seconds), 1), 15)


def run_balance_script(
    code: str,
    *,
    api_key: str,
    base_url: str,
    proxy: str,
    timeout_seconds: int,
    params: dict[str, str] | None = None,
) -> BalanceScriptOutcome:
    timeout = clamp_balance_timeout(timeout_seconds)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        env["SYSTEMROOT"] = system_root
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            input=json.dumps(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "proxy": proxy,
                    "params": params or {},
                }
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BalanceScriptOutcome(ok=False, error_message="Balance script timed out")
    except OSError as exc:
        return BalanceScriptOutcome(ok=False, error_message=str(exc)[:500])
    if completed.returncode != 0:
        detail = (
            completed.stderr or completed.stdout or "Balance script failed"
        ).strip()
        return BalanceScriptOutcome(ok=False, error_message=detail[:500])
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return BalanceScriptOutcome(
            ok=False, error_message="Balance script returned no JSON"
        )
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return BalanceScriptOutcome(
            ok=False, error_message="Balance script returned invalid JSON"
        )
    if not isinstance(payload, dict):
        return BalanceScriptOutcome(
            ok=False, error_message="Balance script returned invalid JSON"
        )
    missing = [key for key in ("total", "balance", "currency") if key not in payload]
    if missing:
        return BalanceScriptOutcome(
            ok=False,
            error_message="Balance script JSON missing " + ", ".join(missing),
        )
    return BalanceScriptOutcome(
        ok=True,
        total=str(payload["total"]),
        balance=str(payload["balance"]),
        currency=str(payload["currency"]),
    )
