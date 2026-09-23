#!/usr/bin/env python3
"""Read-only HTTP bridge to Grafana / Sentry for subagents that cannot see MCP servers.

Credentials are read at runtime from ~/.claude.json (mcpServers.<name>.env) and are
never printed. Only GET requests are issued, plus Grafana's read-only /api/ds/query.

Usage:
  obs_http.py grafana get  <endpoint>                      # e.g. /api/datasources
  obs_http.py grafana prom <ds_uid> '<promql>' [--start ISO --end ISO --step 60]
  obs_http.py grafana loki <ds_uid> '<logql>'  [--start ISO --end ISO --limit 200]
  obs_http.py sentry  get  <path>                          # e.g. issues/453296/
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "rca-skill-obs-http/1.0 (+claude-code)"  # default urllib UA is blocked by CF Access (error 1010)
TIMEOUT_S = 60
MAX_OUTPUT_CHARS = 60_000


def load_env(server: str) -> dict:
    config = json.loads((Path.home() / ".claude.json").read_text())
    return config["mcpServers"][server]["env"]


def to_epoch(value: str) -> str:
    if value is None or value.replace(".", "").isdigit():
        return value
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp()))


def request(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def grafana_headers(env: dict) -> dict:
    extra = json.loads(env.get("GRAFANA_EXTRA_HEADERS") or "{}")
    return {**extra, "Authorization": f"Bearer {env['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"}


def grafana(args) -> str:
    env = load_env("grafana")
    base = env["GRAFANA_URL"].rstrip("/")
    headers = grafana_headers(env)
    if args.mode == "get":
        return request(base + args.target, headers)
    params = {"query": args.query, "start": to_epoch(args.start), "end": to_epoch(args.end)}
    if args.mode == "prom":
        path = "api/v1/query_range" if args.start else "api/v1/query"
        params["step"] = args.step
    else:
        path = "loki/api/v1/query_range"
        params["limit"] = args.limit
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return request(f"{base}/api/datasources/proxy/uid/{args.target}/{path}?{qs}", headers)


def sentry(args) -> str:
    env = load_env("sentry")
    base = env["SENTRY_URL"].rstrip("/") + "/api/0/"
    path = args.target.lstrip("/").replace("{org}", env["SENTRY_ORG_SLUG"])
    return request(base + path, {"Authorization": f"Bearer {env['SENTRY_AUTH_TOKEN']}"})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("server", choices=["grafana", "sentry"])
    p.add_argument("mode", choices=["get", "prom", "loki"])
    p.add_argument("target", help="endpoint/path for get, datasource uid for prom/loki")
    p.add_argument("query", nargs="?")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--step", default="60")
    p.add_argument("--limit", default="200")
    args = p.parse_args()
    if args.server == "sentry" and args.mode != "get":
        p.error("sentry supports only 'get'")
    try:
        out = grafana(args) if args.server == "grafana" else sentry(args)
    except urllib.error.HTTPError as err:
        print(f"HTTP {err.code}: {err.read().decode('utf-8', errors='replace')[:2000]}", file=sys.stderr)
        return 1
    print(out[:MAX_OUTPUT_CHARS])
    if len(out) > MAX_OUTPUT_CHARS:
        print(f"\n[truncated: {len(out)} chars total]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
