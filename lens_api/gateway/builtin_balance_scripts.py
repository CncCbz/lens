from __future__ import annotations

from dataclasses import dataclass

NEW_API_BALANCE_SCRIPT = """
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

payload = json.load(sys.stdin)
base = str(payload["base_url"]).rstrip("/")
if base.endswith("/v1"):
    base = base[:-3]
params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
token = str(params.get("access_token") or "").strip()
if token.lower().startswith("bearer "):
    token = token[7:].strip()
if not token:
    raise SystemExit("access_token is required")
proxy = str(payload.get("proxy") or "").strip()
opener = build_opener(
    *([ProxyHandler({"http": proxy, "https": proxy})] if proxy else [])
)


def get(url):
    try:
        with opener.open(
            Request(url, headers={"Authorization": token, "Accept": "application/json"}),
            timeout=10,
        ) as response:
            body = json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise SystemExit(detail or str(exc))
    except URLError as exc:
        raise SystemExit(str(exc.reason))
    if isinstance(body, dict) and body.get("success") is False:
        raise SystemExit(str(body.get("message") or "request failed"))
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else body


status = get(base + "/api/status")
user = get(base + "/api/user/self")
if not isinstance(user, dict) or user.get("quota") is None:
    raise SystemExit("New API account balance was not returned")
remaining = float(user["quota"])
used = float(user.get("used_quota") or 0)
per_unit = float(status.get("quota_per_unit") or 500000) or 500000
display = str(status.get("quota_display_type") or "USD").upper()
exchange = float(status.get("usd_exchange_rate") or 1)
custom_rate = float(status.get("custom_currency_exchange_rate") or 1)
custom_symbol = str(status.get("custom_currency_symbol") or "").strip()


def convert(units):
    if display == "TOKENS":
        return units, "TOKENS"
    if display == "CNY":
        return units / per_unit * exchange, "CNY"
    if display == "CUSTOM":
        return units / per_unit * custom_rate, custom_symbol or "CUSTOM"
    return units / per_unit, "USD"


total, currency = convert(remaining + used)
balance, _currency = convert(remaining)
print(json.dumps({"total": f"{total:.4f}", "balance": f"{balance:.4f}", "currency": currency}))
""".strip()

SUB2API_BALANCE_SCRIPT = """
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

payload = json.load(sys.stdin)
base = str(payload["base_url"]).rstrip("/")
root = base if base.endswith("/v1") else base + "/v1"
proxy = str(payload.get("proxy") or "").strip()
opener = build_opener(
    *([ProxyHandler({"http": proxy, "https": proxy})] if proxy else [])
)
try:
    with opener.open(
        Request(
            root + "/usage",
            headers={
                "Authorization": "Bearer " + str(payload["api_key"]),
                "Accept": "application/json",
            },
        ),
        timeout=10,
    ) as response:
        data = json.loads(response.read().decode())
except HTTPError as exc:
    detail = exc.read().decode(errors="replace").strip()
    raise SystemExit(detail or str(exc))
except URLError as exc:
    raise SystemExit(str(exc.reason))
if isinstance(data, dict) and data.get("error"):
    error = data["error"]
    message = error.get("message") if isinstance(error, dict) else str(error)
    raise SystemExit(message or "request failed")

currency = str(data.get("unit") or "USD")
quota = data.get("quota") if isinstance(data.get("quota"), dict) else None
if quota is not None and quota.get("limit") is not None:
    total = float(quota["limit"])
    balance = float(quota.get("remaining") if quota.get("remaining") is not None else data.get("remaining") or 0)
elif data.get("balance") is not None:
    total = balance = float(data["balance"])
else:
    remaining = data.get("remaining")
    subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    windows = []
    for limit_key, used_key in (
        ("daily_limit_usd", "daily_usage_usd"),
        ("weekly_limit_usd", "weekly_usage_usd"),
        ("monthly_limit_usd", "monthly_usage_usd"),
    ):
        limit = subscription.get(limit_key)
        if limit:
            windows.append((float(limit) - float(subscription.get(used_key) or 0), float(limit)))
    if remaining is None or float(remaining) < 0 or not windows:
        print(json.dumps({"total": "unlimited", "balance": "unlimited", "currency": currency}))
        raise SystemExit(0)
    balance, total = min(windows)
print(json.dumps({"total": f"{total:.4f}", "balance": f"{balance:.4f}", "currency": currency}))
""".strip()

SENSEAUDIO_BALANCE_SCRIPT = """
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

payload = json.load(sys.stdin)
params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
token = str(params.get("access_token") or "").strip()
if token.lower().startswith("bearer "):
    token = token[7:].strip()
if not token:
    raise SystemExit("access_token is required")
host = str(payload.get("base_url") or "").rstrip("/")
if host.endswith("/v1"):
    host = host[:-3]
if host.endswith("/api"):
    host = host[:-4]
if "127.0.0.1" not in host and "localhost" not in host and "platform.senseaudio.cn" not in host:
    host = "https://platform.senseaudio.cn"
proxy = str(payload.get("proxy") or "").strip()
opener = build_opener(
    *([ProxyHandler({"http": proxy, "https": proxy})] if proxy else [])
)


def get(path):
    request = Request(
        host + "/api" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "x-platform": "WEB",
            "x-version": "1.0.2",
            "x-language": "zh-cn",
            "x-product": "SenseAudio",
        },
    )
    try:
        with opener.open(request, timeout=10) as response:
            body = json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise SystemExit(detail or str(exc))
    except URLError as exc:
        raise SystemExit(str(exc.reason))
    if isinstance(body, dict) and body.get("code") not in (None, 0, "0", 200):
        raise SystemExit(str(body.get("message") or body.get("code")))
    return body if isinstance(body, dict) else {}


user = get("/user/self")
cycle = get("/user/subscription/current_cycle")
account = user.get("account_info") if isinstance(user.get("account_info"), dict) else {}
windows = []
for item in user.get("usage_infos") or []:
    if not isinstance(item, dict):
        continue
    total_count = item.get("total_count")
    if total_count is None:
        continue
    total_count = float(total_count)
    if total_count < 0:
        continue
    remaining = total_count - float(item.get("used_count") or 0) - float(item.get("pending_count") or 0)
    windows.append((max(remaining, 0), total_count))
if cycle.get("plan_point_remains") is not None:
    remains = float(cycle.get("plan_point_remains") or 0)
    windows.append((max(remains, 0), float(cycle.get("plan_points") or remains)))
if not windows:
    raise SystemExit("SenseAudio balance was not returned")
plan_remaining, plan_total = min(windows)
vouchers = account.get("vouchers") if isinstance(account.get("vouchers"), list) else []
voucher_available = sum(float(item.get("available") or 0) for item in vouchers if isinstance(item, dict))
cash = account.get("balance")
if cash is None:
    cash = user.get("balance") or 0
cash = float(cash)
balance = plan_remaining / 5000
total = plan_total / 5000
if account.get("enable_extra_usage"):
    balance += voucher_available / 1000000 + cash / 1000000
    total += voucher_available / 1000000 + max(cash, 0) / 1000000
print(json.dumps({"total": f"{total:.4f}", "balance": f"{balance:.4f}", "currency": "CNY"}))
""".strip()


@dataclass(frozen=True, slots=True)
class BuiltinBalanceScript:
    name: str
    code: str
    timeout_seconds: int = 10
    parameters: tuple[tuple[str, str], ...] = ()


BUILTIN_BALANCE_SCRIPTS = (
    BuiltinBalanceScript(
        name="New API",
        code=NEW_API_BALANCE_SCRIPT,
        parameters=(("access_token", "访问令牌"),),
    ),
    BuiltinBalanceScript(name="Sub2API", code=SUB2API_BALANCE_SCRIPT),
    BuiltinBalanceScript(
        name="SenseAudio",
        code=SENSEAUDIO_BALANCE_SCRIPT,
        parameters=(("access_token", "访问令牌"),),
    ),
)
