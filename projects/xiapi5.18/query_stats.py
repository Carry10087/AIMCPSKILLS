#!/usr/bin/env python3
"""查询任务平台每日统计接口。"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_TIMEOUT_SECONDS = 8
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def configure_stdout() -> None:
    reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
    if reconfigure_stdout is not None:
        reconfigure_stdout(encoding="utf-8")


def parse_json_or_text(raw_text: str) -> Any:
    if not raw_text.strip():
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"配置文件必须是 JSON 对象: {path}")
    return data


def tcp_check(base_url: str, timeout: int) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return {"ok": False, "error": f"无法解析 BASE_URL: {base_url}"}

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "ok": True,
                "host": host,
                "port": port,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": str(exc),
        }


def query_daily_stats(
    base_url: str,
    api_token: str,
    day: str,
    timeout: int,
    token_in_query: bool,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"time": day})
    if token_in_query:
        query = f"{query}&{urllib.parse.urlencode({'token': api_token})}"
        headers = {"Accept": "application/json"}
    else:
        headers = {"Accept": "application/json", "X-Api-Token": api_token}

    url = f"{base_url.rstrip('/')}/api/stats/daily?{query}"
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with DIRECT_OPENER.open(request, timeout=timeout) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
            return {
                "ok": response.status < 400,
                "date": day,
                "http_status": response.status,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "response": parse_json_or_text(raw_text),
                "raw": raw_text[:1000],
            }
    except urllib.error.HTTPError as exc:
        raw_text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "date": day,
            "http_status": exc.code,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "response": parse_json_or_text(raw_text),
            "raw": raw_text[:1000],
            "error": f"查询接口返回 HTTP {exc.code}",
        }
    except TimeoutError:
        return {
            "ok": False,
            "date": day,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"查询接口超时（{timeout} 秒）",
        }
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "timed out" in reason.lower():
            reason = f"查询接口超时（{timeout} 秒）"
        return {
            "ok": False,
            "date": day,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": reason,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="专门查询 /api/stats/daily 每日统计接口。")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径。")
    parser.add_argument("--base-url", help="任务平台 BASE_URL。")
    parser.add_argument("--api-token", help="任务平台 API_TOKEN。")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="查询日期，格式 YYYY-MM-DD，默认今天。",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="超时时间，单位秒。")
    parser.add_argument(
        "--token-in-query",
        action="store_true",
        help="把 token 放到查询参数里，而不是 X-Api-Token 请求头。",
    )
    parser.add_argument("--no-tcp-check", action="store_true", help="跳过 TCP 连通性检查。")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = build_parser().parse_args(argv)

    try:
        config = load_config(Path(args.config)) if args.config else {}
        base_url = (
            args.base_url
            or os.getenv("BASE_URL")
            or config.get("base_url")
            or config.get("BASE_URL")
        )
        api_token = (
            args.api_token
            or os.getenv("API_TOKEN")
            or config.get("api_token")
            or config.get("API_TOKEN")
        )
        if not base_url or not api_token:
            raise RuntimeError("必须提供 BASE_URL 和 API_TOKEN。")

        output: dict[str, Any] = {
            "ok": True,
            "message": "开始查询每日统计接口",
            "date": args.date,
            "base_url": base_url,
        }
        if not args.no_tcp_check:
            output["tcp_check"] = tcp_check(base_url, min(args.timeout, 5))

        output["stats"] = query_daily_stats(
            base_url=base_url,
            api_token=api_token,
            day=args.date,
            timeout=args.timeout,
            token_in_query=args.token_in_query,
        )
        output["ok"] = bool(output["stats"].get("ok"))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["ok"] else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
