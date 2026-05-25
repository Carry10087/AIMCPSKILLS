#!/usr/bin/env python3
"""Task worker for the Shopee pull/crawl/submit flow.

Reads API settings from command-line arguments, environment variables, or a
local config.json file.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from shopee_probe import (
    ENDPOINTS,
    ProbeError,
    ShopeeProbe,
    configure_stdout,
    find_debug_port,
    parse_product_ids,
    public_product_url,
    summarize_get_pc,
)


DEFAULT_CONCURRENCY = 10
EMPTY_PULL_SLEEP_SECONDS = 0.2
DEFAULT_TASK_PULL_QPS = 0.0
DEFAULT_SUCCESS_LIMIT = 0
DEFAULT_MAX_CONSECUTIVE_SHOPEE_ERRORS = 2
DEFAULT_VERIFY_RECOVERY_ATTEMPTS = 1
DEFAULT_VERIFY_COOLDOWN_SECONDS = 120.0
DEFAULT_API_TIMEOUT_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 25
SHOPEE_HOME_URL = "https://shopee.tw"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_dir()
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_OUT_DIR = APP_DIR / "out" / "tasks"
DEFAULT_LOG_DIR = APP_DIR / "out" / "logs"
DEFAULT_RECORD_DIR = APP_DIR / "out" / "task_records"
DEFAULT_RISK_DIR = APP_DIR / "out" / "profile_risk"
DEFAULT_RISK_BLOCK_FILE = DEFAULT_RISK_DIR / "blocked_profiles.json"
DEFAULT_PID_FILE = DEFAULT_LOG_DIR / "worker.pid"
PRINT_LOCK = threading.Lock()
FILE_LOCK = threading.Lock()
PROFILE_BLOCK_MARKERS = (
    "Shopee 当前会话进入验证页",
    "Shopee 当前会话返回验证页",
    "/verify/traffic",
    "/verify/captcha",
    "SHOPEE_VERIFY_PAGE",
    "90309999",
)


class WorkerError(RuntimeError):
    pass


@dataclass
class Task:
    task_url: str
    shop_id: int
    item_id: int
    raw: dict[str, Any]


class TaskApiClient:
    def __init__(self, base_url: str, token: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request_json(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any, str]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"X-Api-Token": self.token, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
                return response.status, parse_json_or_text(raw_text), raw_text
        except urllib.error.HTTPError as exc:
            raw_text = exc.read().decode("utf-8", errors="replace")
            return exc.code, parse_json_or_text(raw_text), raw_text
        except TimeoutError as exc:
            raise WorkerError(f"请求任务接口超时（{self.timeout} 秒）: {url}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if "timed out" in reason.lower():
                raise WorkerError(f"请求任务接口超时（{self.timeout} 秒）: {url}") from exc
            raise WorkerError(f"无法连接任务接口 {url}: {exc.reason}") from exc

    def pull(self) -> tuple[int, Any, str]:
        return self.request_json("GET", "/api/task/pull")

    def submit(self, task_url: str, task_result: str) -> tuple[int, Any, str]:
        return self.request_json(
            "POST",
            "/api/task/submit",
            {"taskUrl": task_url, "taskResult": task_result},
        )


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
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"配置文件不是有效 JSON: {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise WorkerError(f"配置文件必须是 JSON 对象: {path}")
    return config


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def today_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process = kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            exit_code = wintypes.DWORD()
            try:
                if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(process)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_background_worker() -> int:
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    if DEFAULT_PID_FILE.exists():
        try:
            old_pid = int(DEFAULT_PID_FILE.read_text(encoding="ascii").strip())
        except ValueError:
            old_pid = 0
        if old_pid and process_is_running(old_pid):
            print(json.dumps({
                "ok": True,
                "message": "后台任务已经在运行",
                "pid": old_pid,
                "pid_file": str(DEFAULT_PID_FILE.resolve()),
            }, ensure_ascii=False, indent=2))
            return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stdout_path = DEFAULT_LOG_DIR / f"worker-{stamp}.out.log"
    stderr_path = DEFAULT_LOG_DIR / f"worker-{stamp}.err.log"
    if getattr(sys, "frozen", False):
        cwd = app_dir()
        command = [sys.executable, "--loop"]
    else:
        script_path = Path(__file__).resolve()
        cwd = script_path.parent
        command = [sys.executable, "-u", str(script_path), "--loop"]

    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        start_new_session = True

    with stdout_path.open("a", encoding="utf-8") as stdout_log, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr_log:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )

    DEFAULT_PID_FILE.write_text(str(process.pid), encoding="ascii")
    print(json.dumps({
        "ok": True,
        "message": "后台任务已启动",
        "pid": process.pid,
        "pid_file": str(DEFAULT_PID_FILE.resolve()),
        "stdout_log": str(stdout_path.resolve()),
        "stderr_log": str(stderr_path.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


def stop_background_worker() -> int:
    if not DEFAULT_PID_FILE.exists():
        print(json.dumps({"ok": True, "message": "后台任务未运行"}, ensure_ascii=False))
        return 0
    try:
        pid = int(DEFAULT_PID_FILE.read_text(encoding="ascii").strip())
    except ValueError as exc:
        raise WorkerError(f"PID 文件格式错误: {DEFAULT_PID_FILE}") from exc

    if process_is_running(pid):
        os.kill(pid, signal.SIGTERM)
    DEFAULT_PID_FILE.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "message": "后台任务已停止", "pid": pid}, ensure_ascii=False))
    return 0


def show_background_status() -> int:
    if not DEFAULT_PID_FILE.exists():
        print(json.dumps({
            "ok": True,
            "running": False,
            "message": "后台任务未运行",
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        pid = int(DEFAULT_PID_FILE.read_text(encoding="ascii").strip())
    except ValueError:
        pid = 0
    running = bool(pid and process_is_running(pid))
    if not running:
        DEFAULT_PID_FILE.unlink(missing_ok=True)
    print(json.dumps({
        "ok": True,
        "running": running,
        "message": "后台任务正在运行" if running else "后台任务未运行，已清理失效 PID",
        "pid": pid or None,
        "pid_file": str(DEFAULT_PID_FILE.resolve()),
        "log_dir": str(DEFAULT_LOG_DIR.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


def first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def int_or_zero(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_debug_ports(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [port for port in (int_or_zero(item) for item in value) if port > 0]
    if isinstance(value, int):
        return [value] if value > 0 else []
    text = str(value).replace(";", ",").replace(" ", ",")
    return [port for port in (int_or_zero(part) for part in text.split(",")) if port > 0]


def bool_from_config(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return False


def has_cli_option(argv: Sequence[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in argv)


def configured_value(
    config: dict[str, Any],
    keys: Sequence[str],
    env_name: str | None = None,
) -> Any:
    if env_name:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return value
    return None


def apply_float_config(
    args: argparse.Namespace,
    attr: str,
    option: str,
    raw_argv: Sequence[str],
    config: dict[str, Any],
    keys: Sequence[str],
    env_name: str | None = None,
) -> None:
    if has_cli_option(raw_argv, option):
        return
    value = configured_value(config, keys, env_name)
    if value is not None:
        setattr(args, attr, float(value))


def apply_int_config(
    args: argparse.Namespace,
    attr: str,
    option: str,
    raw_argv: Sequence[str],
    config: dict[str, Any],
    keys: Sequence[str],
    env_name: str | None = None,
) -> None:
    if has_cli_option(raw_argv, option):
        return
    value = configured_value(config, keys, env_name)
    if value is not None:
        setattr(args, attr, int(value))


def task_pull_sleep_seconds(args: argparse.Namespace, worker_count: int = 1) -> float:
    pull_qps = float(getattr(args, "task_pull_qps", 0) or 0)
    if pull_qps <= 0:
        return EMPTY_PULL_SLEEP_SECONDS
    return max(0.0, worker_count / pull_qps)


NO_TASK_MARKERS = (
    "no task",
    "empty",
    "none",
    "not found",
    "no data",
    "no_task",
    "暂无任务",
    "无任务",
    "没有任务",
    "没任务",
    "任务为空",
    "已抢完",
    "抢完",
)


def looks_like_no_task(payload: Any) -> bool:
    if payload is None:
        return True
    combined = json.dumps(payload, ensure_ascii=False).lower()
    return any(marker in combined for marker in NO_TASK_MARKERS)


def unwrap_task(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload[0] if payload else None
    if not isinstance(payload, dict):
        raise WorkerError(f"拉任务接口返回类型异常: {type(payload).__name__}")

    if any(key in payload for key in ("taskUrl", "task_url", "url", "shop_id", "item_id")):
        return payload

    for key in ("data", "task", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value[0] if value else None
        if isinstance(value, dict):
            return value

    if looks_like_no_task(payload):
        return None

    return payload


def parse_task(payload: Any) -> Task | None:
    if isinstance(payload, dict) and "code" in payload:
        code = payload.get("code")
        if code not in (None, 0, 200):
            if code == 300 or looks_like_no_task(payload):
                return None
            raise WorkerError(
                f"拉任务接口返回 code={code}: {payload.get('msg') or payload}"
            )

    task = unwrap_task(payload)
    if task is None:
        return None

    task_url = first_present(
        task,
        (
            "taskUrl",
            "task_url",
            "url",
            "productUrl",
            "product_url",
            "goodsUrl",
            "goods_url",
            "itemUrl",
            "item_url",
        ),
    )
    shop_id = first_present(task, ("shop_id", "shopId", "shopid"))
    item_id = first_present(task, ("item_id", "itemId", "itemid", "goodsId", "goods_id"))

    if task_url and (not shop_id or not item_id):
        shop_id, item_id = parse_product_ids(str(task_url))
    elif shop_id and item_id and not task_url:
        task_url = public_product_url(int(shop_id), int(item_id))
    elif not task_url:
        raise WorkerError(f"任务中找不到 taskUrl 或 shop_id/item_id: {task}")

    return Task(
        task_url=str(task_url),
        shop_id=int(shop_id),
        item_id=int(item_id),
        raw=task,
    )


def status_dir(base_dir: Path, ok: bool) -> Path:
    return base_dir / ("success" if ok else "failed")


def save_artifact(out_dir: Path, task: Task, value: Any, ok: bool) -> Path:
    target_dir = status_dir(out_dir, ok)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{now_tag()}_{time.time_ns()}_{task.shop_id}_{task.item_id}.json"
    with FILE_LOCK:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_task_record(record_dir: Path, record: dict[str, Any], ok: bool) -> Path:
    target_dir = status_dir(record_dir, ok)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{today_tag()}.jsonl"
    with FILE_LOCK:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            fp.write("\n")
    return path


def record_worker_error(args: argparse.Namespace, exc: WorkerError) -> Path:
    output = {
        "ok": False,
        "failure_stage": "任务接口",
        "failure_reason": str(exc),
        "dry_run": args.dry_run,
        "source": None,
        "taskUrl": None,
        "summary": None,
        "attempts": [],
        "elapsed_seconds": 0,
        "artifact_path": None,
        "submitted": None,
    }
    record = {"time": now_tag(), **output}
    record_path = save_task_record(Path(args.record_dir), record, ok=False)
    output["record_path"] = str(record_path.resolve())
    with PRINT_LOCK:
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return record_path


def print_worker_event(message: str, **extra: Any) -> None:
    payload = {
        "ok": True,
        "message": message,
        "time": now_tag(),
        **extra,
    }
    with PRINT_LOCK:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def is_environment_error(message: str) -> bool:
    markers = (
        "Shopee 当前会话进入验证页",
        "Shopee 当前会话返回验证页",
        "/verify/traffic",
        "/verify/captcha",
        "SHOPEE_VERIFY_PAGE",
        "AdsPower",
        "未找到 AdsPower",
        "csrftoken",
        "未找到 csrftoken",
    )
    return any(marker in message for marker in markers)


def is_verify_page_error(message: str) -> bool:
    markers = (
        "Shopee 当前会话进入验证页",
        "Shopee 当前会话返回验证页",
        "/verify/traffic",
        "/verify/captcha",
        "SHOPEE_VERIFY_PAGE",
    )
    return any(marker in message for marker in markers)


def recover_probe_from_verify(
    probe: ShopeeProbe,
    page_url: str,
    cooldown_seconds: float,
    recovery_round: int,
) -> None:
    cooldown = max(0.0, cooldown_seconds)
    if cooldown:
        print_worker_event(
            "检测到验证页，冷却后重新打开页面",
            cooldown_seconds=round(cooldown, 3),
            recovery_round=recovery_round,
            url=page_url,
        )
        time.sleep(cooldown)
    else:
        print_worker_event(
            "检测到验证页，重新打开页面",
            recovery_round=recovery_round,
            url=page_url,
        )
    probe.reopen_page(page_url)


def pause_worker_after_risk(
    args: argparse.Namespace,
    probe: ShopeeProbe,
    reason: str,
    risk_path: Path,
    stage: str,
) -> None:
    cooldown = max(0.0, float(getattr(args, "verify_cooldown", DEFAULT_VERIFY_COOLDOWN_SECONDS)))
    print_worker_event(
        "环境异常，worker 进入冷却等待",
        stage=stage,
        cooldown_seconds=round(cooldown, 3),
        risk_record=str(risk_path.resolve()),
        reason=reason,
    )
    try:
        probe.reset_connection()
    except Exception:
        pass
    if cooldown:
        time.sleep(cooldown)


def should_block_profile(reason: str) -> bool:
    return any(marker in reason for marker in PROFILE_BLOCK_MARKERS)


def risk_identity(args: argparse.Namespace) -> str | None:
    profile_id = str(getattr(args, "profile_id", "") or "").strip()
    if profile_id:
        return f"profile:{profile_id}"
    debug_port = int_or_zero(getattr(args, "debug_port", 0))
    if debug_port:
        return f"debug_port:{debug_port}"
    return None


def risk_block_path(args: argparse.Namespace) -> Path:
    return Path(args.risk_dir) / DEFAULT_RISK_BLOCK_FILE.name


def load_risk_blocks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def save_risk_blocks(path: Path, blocks: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(blocks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def blocked_profile_record(args: argparse.Namespace) -> dict[str, Any] | None:
    identity = risk_identity(args)
    if identity is None:
        return None
    blocks = load_risk_blocks(risk_block_path(args))
    record = blocks.get(identity)
    return record if isinstance(record, dict) else None


def block_current_profile(args: argparse.Namespace, reason: str, **extra: Any) -> Path:
    event_path = write_risk_event(args, reason, **extra)
    identity = risk_identity(args)
    if identity is None:
        return event_path
    path = risk_block_path(args)
    blocks = load_risk_blocks(path)
    blocks[identity] = {
        "blocked_at": now_tag(),
        "reason": reason,
        "debug_port": args.debug_port or None,
        "profile_id": str(getattr(args, "profile_id", "") or ""),
        "risk_event": str(event_path.resolve()),
        **extra,
    }
    save_risk_blocks(path, blocks)
    return event_path


def has_shopee_error(output: dict[str, Any] | None, code: int) -> bool:
    if not output:
        return False
    expected = str(code)
    summary = output.get("summary")
    if isinstance(summary, dict) and str(summary.get("shopee_error")) == expected:
        return True
    attempts = output.get("attempts")
    if isinstance(attempts, list):
        return any(
            isinstance(item, dict) and str(item.get("shopee_error")) == expected
            for item in attempts
        )
    return False


def write_risk_event(args: argparse.Namespace, reason: str, **extra: Any) -> Path:
    target_dir = Path(args.risk_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{today_tag()}.jsonl"
    event = {
        "time": now_tag(),
        "reason": reason,
        "debug_port": args.debug_port or None,
        **extra,
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        fp.write("\n")
    return path


def extract_response_message(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("msg", "message", "error_msg", "error", "reason"):
            message = value.get(key)
            if message:
                return str(message)
    elif value:
        return str(value)
    return None


def describe_failure(
    result: dict[str, Any], submit_info: dict[str, Any] | None
) -> tuple[str | None, str | None]:
    if result["ok"]:
        return None, None

    if submit_info:
        if submit_info.get("skipped"):
            return "提交", str(submit_info.get("reason") or "已跳过提交")
        if submit_info.get("ok") is False:
            message = extract_response_message(submit_info.get("response"))
            if not message:
                message = str(submit_info.get("raw") or "提交接口拒绝了任务")
            return "提交", f"HTTP {submit_info.get('http_status')}: {message}"

    summary = result.get("summary")
    if isinstance(summary, dict):
        message = extract_response_message(summary)
        if message:
            return "抓取", message
        if summary.get("has_real_data") is False:
            return "抓取", "Shopee 返回中没有真实商品数据。"
        if result.get("require_price_for_success") and summary.get("has_price") is False:
            return "抓取", "SSR 数据缺少价格字段，未提交。"

    attempts = result.get("attempts")
    if isinstance(attempts, list) and attempts:
        last_attempt = attempts[-1]
        if isinstance(last_attempt, dict):
            message = extract_response_message(last_attempt)
            if message:
                return "抓取", message

    return "抓取", "任务失败，但未识别到具体原因。"


def build_error_result(task: Task, message: str) -> dict[str, Any]:
    return {
        "error": "WORKER_ERROR",
        "error_msg": message,
        "data": None,
        "task": {
            "taskUrl": task.task_url,
            "shop_id": task.shop_id,
            "item_id": task.item_id,
        },
    }


def process_task(
    task: Task,
    endpoints: Sequence[str],
    timeout: int,
    no_warmup: bool,
    out_dir: Path,
    probe: ShopeeProbe | None = None,
    verify_recovery_attempts: int = DEFAULT_VERIFY_RECOVERY_ATTEMPTS,
    verify_cooldown_seconds: float = DEFAULT_VERIFY_COOLDOWN_SECONDS,
    require_price_for_success: bool = False,
) -> dict[str, Any]:
    owns_probe = probe is None
    if probe is None:
        probe = ShopeeProbe(timeout=timeout)
    else:
        probe.timeout = timeout
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    recovery_attempts: list[dict[str, Any]] = []
    response_body = build_error_result(task, "任务尚未执行。")
    summary = summarize_get_pc(response_body)
    ok = False
    source = "worker_error"

    try:
        full_page = False
        for recovery_round in range(max(0, verify_recovery_attempts) + 1):
            try:
                if not no_warmup and not probe.has_session:
                    probe.warmup(task.task_url, full_page=full_page)
                fetched, attempts = probe.fetch_first_usable(
                    endpoints, task.shop_id, task.item_id, task.task_url,
                    full_page=full_page,
                )
                response_body = fetched["body"]
                fetched_summary = fetched.get("summary")
                summary = fetched_summary if isinstance(fetched_summary, dict) else summarize_get_pc(response_body)
                has_required_price = (
                    not require_price_for_success
                    or summary.get("has_price") is not False
                )
                ok = (
                    bool(summary["has_real_data"])
                    and fetched["http_status"] < 400
                    and bool(has_required_price)
                )
                source = fetched["endpoint"]
                verify_message = str(summary.get("shopee_error_msg") or summary.get("shopee_error") or "")
                if not ok and is_verify_page_error(verify_message):
                    can_recover = recovery_round < max(0, verify_recovery_attempts)
                    if can_recover:
                        recovery_attempts.append(
                            {
                                "endpoint": "page_recovery",
                                "http_status": fetched["http_status"],
                                "shopee_error": "VERIFY_PAGE_RECOVERY",
                                "shopee_error_msg": verify_message,
                                "has_real_data": False,
                                "recovery_round": recovery_round + 1,
                                "url": task.task_url,
                            }
                        )
                        try:
                            recover_probe_from_verify(
                                probe,
                                task.task_url,
                                verify_cooldown_seconds,
                                recovery_round + 1,
                            )
                        except (ProbeError, WorkerError) as recover_exc:
                            response_body = build_error_result(task, str(recover_exc))
                            summary = summarize_get_pc(response_body)
                            ok = False
                            source = "worker_error"
                            break
                        continue
                break
            except (ProbeError, WorkerError) as exc:
                message = str(exc)
                can_recover = (
                    recovery_round < max(0, verify_recovery_attempts)
                    and is_verify_page_error(message)
                )
                if can_recover:
                    recovery_attempts.append(
                        {
                            "endpoint": "page_recovery",
                            "http_status": None,
                            "shopee_error": "VERIFY_PAGE_RECOVERY",
                            "shopee_error_msg": message,
                            "has_real_data": False,
                            "recovery_round": recovery_round + 1,
                            "url": task.task_url,
                        }
                    )
                    try:
                        recover_probe_from_verify(
                            probe,
                            task.task_url,
                            verify_cooldown_seconds,
                            recovery_round + 1,
                        )
                    except (ProbeError, WorkerError) as recover_exc:
                        response_body = build_error_result(task, str(recover_exc))
                        summary = summarize_get_pc(response_body)
                        ok = False
                        source = "worker_error"
                        break
                    continue
                response_body = build_error_result(task, message)
                summary = summarize_get_pc(response_body)
                ok = False
                source = "worker_error"
                break
            except Exception as exc:
                if not owns_probe:
                    probe.reset_connection()
                response_body = build_error_result(task, f"抓取任务时发生异常: {exc}")
                summary = summarize_get_pc(response_body)
                ok = False
                source = "worker_error"
                break
        else:
            response_body = build_error_result(task, "抓取任务时发生未知异常。")
            summary = summarize_get_pc(response_body)
            ok = False
            source = "worker_error"
    finally:
        if owns_probe:
            probe.close()

    artifact_path = save_artifact(out_dir, task, response_body, ok)
    return {
        "ok": ok,
        "source": source,
        "taskUrl": task.task_url,
        "shop_id": task.shop_id,
        "item_id": task.item_id,
        "taskResult": json.dumps(response_body, ensure_ascii=False, separators=(",", ":")),
        "summary": summary,
        "attempts": recovery_attempts + attempts,
        "require_price_for_success": require_price_for_success,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "artifact_path": str(artifact_path.resolve()),
    }


def run_once(
    args: argparse.Namespace,
    api: TaskApiClient | None,
    probe: ShopeeProbe | None = None,
) -> tuple[int, dict[str, Any] | None]:
    if args.task_url:
        shop_id, item_id = parse_product_ids(args.task_url)
        task = Task(args.task_url, shop_id, item_id, {"taskUrl": args.task_url})
    else:
        if api is None:
            raise WorkerError("未指定 --task-url 时必须配置 BASE_URL 和 API_TOKEN。")
        pull_status, pull_payload, pull_raw = api.pull()
        if pull_status >= 400:
            raise WorkerError(f"拉任务失败，HTTP {pull_status}: {pull_raw[:500]}")
        task = parse_task(pull_payload)
        if task is None:
            message = extract_response_message(pull_payload) or "暂无任务或本轮未抢到任务"
            print_worker_event(message)
            return 0, None

    endpoints = list(ENDPOINTS) if args.endpoint == "all" else [args.endpoint]
    result = process_task(
        task=task,
        endpoints=endpoints,
        timeout=args.timeout,
        no_warmup=args.no_warmup,
        out_dir=Path(args.out_dir),
        probe=probe,
        verify_recovery_attempts=args.verify_recovery_attempts,
        verify_cooldown_seconds=args.verify_cooldown,
        require_price_for_success=args.require_price,
    )

    submit_info: dict[str, Any] | None = None
    if api is not None and not args.dry_run and (result["ok"] or args.submit_failures):
        submit_status, submit_payload, submit_raw = api.submit(
            result["taskUrl"], result["taskResult"]
        )
        submit_ok = submit_status < 400
        if (
            isinstance(submit_payload, dict)
            and submit_payload.get("code") not in (None, 0, 200)
        ):
            submit_ok = False
        submit_info = {
            "ok": submit_ok,
            "http_status": submit_status,
            "response": submit_payload,
            "raw": submit_raw[:500],
        }
        if not submit_ok:
            result["ok"] = False
    elif api is not None and not args.dry_run and not result["ok"]:
        skipped_reason = "抓取失败，未提交无效的 taskResult"
        result_summary = result.get("summary")
        if (
            result.get("require_price_for_success")
            and isinstance(result_summary, dict)
            and result_summary.get("has_price") is False
        ):
            skipped_reason = "SSR 数据缺少价格字段，未提交。"
        submit_info = {
            "ok": False,
            "skipped": True,
            "reason": skipped_reason,
        }

    returned_home: dict[str, Any] | None = None
    if args.return_home_after_task and result["ok"] and probe is not None:
        try:
            probe.return_home(SHOPEE_HOME_URL)
            returned_home = {"ok": True, "url": SHOPEE_HOME_URL}
        except (ProbeError, WorkerError) as exc:
            returned_home = {"ok": False, "reason": str(exc), "url": SHOPEE_HOME_URL}

    failure_stage, failure_reason = describe_failure(result, submit_info)
    output = {
        "ok": result["ok"],
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "dry_run": args.dry_run or api is None,
        "source": result["source"],
        "taskUrl": result["taskUrl"],
        "summary": result["summary"],
        "attempts": result["attempts"],
        "elapsed_seconds": result["elapsed_seconds"],
        "artifact_path": result["artifact_path"],
        "submitted": submit_info,
        "returned_home": returned_home,
    }
    record = {"time": now_tag(), **output}
    record_path = save_task_record(Path(args.record_dir), record, ok=bool(output["ok"]))
    output["record_path"] = str(record_path.resolve())
    with PRINT_LOCK:
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return (0 if result["ok"] or submit_info else 2), output


def make_api_client(args: argparse.Namespace) -> TaskApiClient | None:
    if args.base_url or args.api_token:
        if not args.base_url or not args.api_token:
            raise WorkerError("BASE_URL 和 API_TOKEN 必须同时配置。")
        return TaskApiClient(args.base_url, args.api_token, args.api_timeout)
    return None


def run_concurrent(args: argparse.Namespace) -> int:
    stop_event = threading.Event()
    state = {"processed": 0, "success_count": 0, "last_code": 0}
    state_lock = threading.Lock()
    worker_count = max(1, int_or_zero(args.concurrency) or DEFAULT_CONCURRENCY)
    empty_pull_sleep = task_pull_sleep_seconds(args, worker_count)
    debug_ports = list(getattr(args, "debug_ports", []) or [])
    browser_concurrency = max(1, int_or_zero(getattr(args, "browser_concurrency", 0)) or worker_count)

    def debug_port_for_worker(worker_id: int) -> int:
        if not debug_ports:
            return int_or_zero(getattr(args, "debug_port", 0))
        browser_index = ((worker_id - 1) // browser_concurrency) % len(debug_ports)
        return debug_ports[browser_index]

    def reserve_round() -> int | None:
        with state_lock:
            if stop_event.is_set():
                return None
            if args.max_tasks and state["processed"] >= args.max_tasks:
                stop_event.set()
                return None
            state["processed"] += 1
            return state["processed"]

    def record_result(code: int, output: dict[str, Any] | None) -> None:
        with state_lock:
            state["last_code"] = code
            if output is not None and output.get("ok"):
                state["success_count"] += 1
                if args.success_limit and state["success_count"] >= args.success_limit:
                    print_worker_event(
                        "达到本轮成功上限，worker 已停止",
                        success_count=state["success_count"],
                    )
                    stop_event.set()

    def worker(worker_id: int) -> None:
        api = make_api_client(args)
        debug_port = debug_port_for_worker(worker_id)
        probe = ShopeeProbe(
            timeout=args.timeout,
            debug_port=debug_port or None,
        )
        print_worker_event("并发槽已启动", worker=worker_id, debug_port=debug_port or None)
        try:
            while not stop_event.is_set():
                round_no = reserve_round()
                if round_no is None:
                    break
                print_worker_event("开始拉取任务", worker=worker_id, round=round_no)
                try:
                    code, output = run_once(args, api, probe)
                except WorkerError as exc:
                    record_worker_error(args, exc)
                    code, output = 1, None
                except Exception as exc:
                    record_worker_error(args, WorkerError(f"并发槽 {worker_id} 异常: {exc}"))
                    try:
                        probe.reset_connection()
                    except Exception:
                        pass
                    code, output = 1, None
                record_result(code, output)
                if output is None and not stop_event.is_set() and empty_pull_sleep > 0:
                    time.sleep(empty_pull_sleep)
        finally:
            probe.close()
            print_worker_event("并发槽已停止", worker=worker_id)

    print_worker_event(
        "worker 已启动",
        base_url=args.base_url,
        concurrency=worker_count,
        browser_concurrency=browser_concurrency,
        debug_ports=debug_ports or None,
        task_pull_qps=args.task_pull_qps,
        api_timeout_seconds=args.api_timeout,
        timeout_seconds=args.timeout,
        success_limit=args.success_limit,
        require_price_for_success=args.require_price,
        return_home_after_task=args.return_home_after_task,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker, idx + 1) for idx in range(worker_count)]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        except KeyboardInterrupt:
            stop_event.set()
            print_worker_event("已停止")
            return 0
    return int(state["last_code"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="拉取 Shopee 任务，抓取结果并提交。")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="配置文件路径，默认使用脚本同目录下的 config.json。",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--api-token")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只处理一个任务后退出。默认会连续拉取任务。",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="持续轮询任务。没有指定 --task-url 时这是默认行为。",
    )
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"并发处理任务数量。无参数运行时读取 config.json，默认 {DEFAULT_CONCURRENCY}。",
    )
    parser.add_argument(
        "--task-pull-qps",
        type=float,
        default=DEFAULT_TASK_PULL_QPS,
        help="拉任务接口空轮询 QPS 上限；0 表示使用默认空轮询间隔。",
    )
    parser.add_argument(
        "--success-limit",
        type=int,
        default=DEFAULT_SUCCESS_LIMIT,
        help="本轮最多成功提交多少个任务后自动停止；0 表示不限制。",
    )
    parser.add_argument(
        "--max-consecutive-shopee-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_SHOPEE_ERRORS,
        help="连续 Shopee 90309999 次数达到该值后停止；0 表示不限制。",
    )
    parser.add_argument(
        "--verify-recovery-attempts",
        type=int,
        default=DEFAULT_VERIFY_RECOVERY_ATTEMPTS,
        help="遇到验证页时重新打开页面做健康恢复的次数；恢复后仍异常会停止。",
    )
    parser.add_argument(
        "--verify-cooldown",
        type=float,
        default=DEFAULT_VERIFY_COOLDOWN_SECONDS,
        help="遇到验证页后，重新打开页面前先冷却多少秒。",
    )
    parser.add_argument(
        "--api-timeout",
        type=int,
        default=DEFAULT_API_TIMEOUT_SECONDS,
        help="拉取/提交任务接口超时时间，单位秒。",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--debug-port",
        type=int,
        default=0,
        help="浏览器 CDP 调试端口。默认自动发现，也可在 config.json 里配置 adspower_debug_port。",
    )
    parser.add_argument(
        "--debug-ports",
        default="",
        help="多个浏览器 CDP 调试端口，逗号分隔；也可在 config.json 里配置 adspower_debug_ports。",
    )
    parser.add_argument(
        "--browser-concurrency",
        type=int,
        default=0,
        help="每个浏览器端口分配的并发数；配置多个端口时会自动计算总并发。",
    )
    parser.add_argument(
        "--profile-id",
        default="",
        help="当前 AdsPower profile 标识。可写入 config.json 的 adspower_profile_id，用于风险环境锁定。",
    )
    parser.add_argument(
        "--allow-risk-profile",
        action="store_true",
        help="允许继续使用已被标记为风险的 profile。默认不允许。",
    )
    parser.add_argument(
        "--stop-on-risk",
        action="store_true",
        help="遇到验证页、连续 903 或风险锁时直接退出。默认保持 worker 常驻冷却恢复。",
    )
    parser.add_argument(
        "--return-home-after-task",
        action="store_true",
        help="每个成功任务提交后把当前页面导航回 Shopee 首页。也可在 config.json 里配置 return_home_after_task。",
    )
    parser.add_argument(
        "--require-price",
        action="store_true",
        help="要求采集结果必须包含价格才算成功。默认关闭，SSR 缺价格但有商品数据时仍可提交。",
    )
    parser.add_argument(
        "--endpoint",
        choices=[*ENDPOINTS, "all"],
        default="all",
        help="Shopee 接口选择。",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--record-dir", default=str(DEFAULT_RECORD_DIR))
    parser.add_argument("--risk-dir", default=str(DEFAULT_RISK_DIR))
    parser.add_argument("--dry-run", action="store_true", help="只抓取不提交。")
    parser.add_argument(
        "--submit-failures",
        action="store_true",
        help="抓取失败时也提交失败响应。默认关闭，避免提交错误数据。",
    )
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument(
        "--task-url",
        help="直接处理这个商品链接，不从任务接口拉取。",
    )
    parser.add_argument("--background", action="store_true", help="在后台启动任务。")
    parser.add_argument("--stop", action="store_true", help="停止后台任务。")
    parser.add_argument("--status", action="store_true", help="查看后台任务状态。")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    raw_argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(raw_argv)

    try:
        if args.stop:
            return stop_background_worker()
        if args.status:
            return show_background_status()
        if args.background:
            return start_background_worker()

        config = load_config(Path(args.config)) if args.config else {}
        args.base_url = (
            args.base_url
            or os.getenv("BASE_URL")
            or config.get("base_url")
            or config.get("BASE_URL")
        )
        args.api_token = (
            args.api_token
            or os.getenv("API_TOKEN")
            or config.get("api_token")
            or config.get("API_TOKEN")
        )
        args.debug_port = (
            args.debug_port
            or int_or_zero(os.getenv("ADSPOWER_DEBUG_PORT"))
            or int_or_zero(config.get("adspower_debug_port") or config.get("ADSPOWER_DEBUG_PORT"))
        )
        args.debug_ports = parse_debug_ports(
            args.debug_ports
            or os.getenv("ADSPOWER_DEBUG_PORTS")
            or config.get("adspower_debug_ports")
            or config.get("ADSPOWER_DEBUG_PORTS")
        )
        if not has_cli_option(raw_argv, "--require-price"):
            require_price_config = configured_value(
                config,
                ("require_price_for_success", "require_price"),
                "REQUIRE_PRICE_FOR_SUCCESS",
            )
            if require_price_config is not None:
                args.require_price = bool_from_config(require_price_config)
        if not args.debug_ports and args.debug_port:
            args.debug_ports = [args.debug_port]
        args.browser_concurrency = (
            int_or_zero(args.browser_concurrency)
            or int_or_zero(os.getenv("BROWSER_CONCURRENCY"))
            or int_or_zero(config.get("browser_concurrency"))
        )
        apply_float_config(
            args,
            "verify_cooldown",
            "--verify-cooldown",
            raw_argv,
            config,
            ("verify_cooldown_seconds", "verify_cooldown"),
            "VERIFY_COOLDOWN_SECONDS",
        )
        apply_int_config(
            args,
            "concurrency",
            "--concurrency",
            raw_argv,
            config,
            ("concurrency", "worker_concurrency"),
            "WORKER_CONCURRENCY",
        )
        if args.debug_ports and args.browser_concurrency and not has_cli_option(raw_argv, "--concurrency"):
            args.concurrency = len(args.debug_ports) * args.browser_concurrency
        args.concurrency = max(1, int_or_zero(args.concurrency) or DEFAULT_CONCURRENCY)
        apply_float_config(
            args,
            "task_pull_qps",
            "--task-pull-qps",
            raw_argv,
            config,
            ("task_pull_qps", "pull_qps"),
            "TASK_PULL_QPS",
        )
        apply_int_config(
            args,
            "success_limit",
            "--success-limit",
            raw_argv,
            config,
            ("success_limit",),
            "SUCCESS_LIMIT",
        )
        apply_int_config(
            args,
            "max_consecutive_shopee_errors",
            "--max-consecutive-shopee-errors",
            raw_argv,
            config,
            ("max_consecutive_shopee_errors",),
            "MAX_CONSECUTIVE_SHOPEE_ERRORS",
        )
        apply_int_config(
            args,
            "verify_recovery_attempts",
            "--verify-recovery-attempts",
            raw_argv,
            config,
            ("verify_recovery_attempts",),
            "VERIFY_RECOVERY_ATTEMPTS",
        )
        apply_int_config(
            args,
            "api_timeout",
            "--api-timeout",
            raw_argv,
            config,
            ("api_timeout_seconds", "api_timeout"),
            "API_TIMEOUT_SECONDS",
        )
        apply_int_config(
            args,
            "timeout",
            "--timeout",
            raw_argv,
            config,
            ("timeout_seconds", "timeout"),
            "TIMEOUT_SECONDS",
        )
        if not args.debug_ports:
            auto_port = find_debug_port() or 0
            args.debug_port = auto_port
            args.debug_ports = [auto_port] if auto_port else []
        elif not args.debug_port:
            args.debug_port = args.debug_ports[0]

        api = make_api_client(args)

        if args.once and args.loop:
            raise WorkerError("--once 和 --loop 不能同时使用。")
        if args.task_url and not args.loop:
            args.once = True
        if not args.task_url and not args.once and args.concurrency > 1:
            return run_concurrent(args)

        processed = 0
        success_count = 0
        consecutive_shopee_errors = 0
        empty_pull_sleep = task_pull_sleep_seconds(args, 1)
        shared_probe = ShopeeProbe(
            timeout=args.timeout,
            debug_port=args.debug_port or None,
        )
        print_worker_event(
            "worker 已启动",
            base_url=args.base_url,
            concurrency=1,
            task_pull_qps=args.task_pull_qps,
            api_timeout_seconds=args.api_timeout,
            timeout_seconds=args.timeout,
            debug_port=args.debug_port or None,
            profile_id=args.profile_id or None,
            success_limit=args.success_limit,
            max_consecutive_shopee_errors=args.max_consecutive_shopee_errors,
            verify_recovery_attempts=args.verify_recovery_attempts,
            verify_cooldown_seconds=args.verify_cooldown,
            return_home_after_task=args.return_home_after_task,
            require_price_for_success=args.require_price,
            stop_on_risk=args.stop_on_risk,
        )
        try:
            while True:
                if args.task_url:
                    print_worker_event("开始处理指定任务", round=processed + 1)
                else:
                    print_worker_event("开始拉取任务", round=processed + 1)
                try:
                    code, output = run_once(args, api, shared_probe)
                except WorkerError as exc:
                    message = str(exc)
                    if args.once or is_environment_error(message):
                        if should_block_profile(message):
                            risk_path = block_current_profile(args, message, stage="pre_pull")
                        else:
                            risk_path = write_risk_event(args, message, stage="pre_pull")
                        if args.once or args.stop_on_risk:
                            print_worker_event("环境异常，worker 已停止", risk_record=str(risk_path.resolve()))
                            raise
                        pause_worker_after_risk(
                            args,
                            shared_probe,
                            message,
                            risk_path,
                            stage="pre_pull",
                        )
                        code = 1
                        output = None
                        processed += 1
                        continue
                    record_worker_error(args, exc)
                    code = 1
                    output = None
                processed += 1

                if output is not None and output.get("ok"):
                    success_count += 1
                    consecutive_shopee_errors = 0
                elif output is not None:
                    reason = str(output.get("failure_reason") or "")
                    raw_summary = output.get("summary")
                    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
                    error_msg = str(summary.get("shopee_error_msg") or "")
                    if is_environment_error(reason) or is_environment_error(error_msg):
                        block_reason = reason or error_msg
                        if should_block_profile(block_reason):
                            risk_path = block_current_profile(
                                args,
                                block_reason,
                                stage="crawl",
                                taskUrl=output.get("taskUrl"),
                                source=output.get("source"),
                            )
                        else:
                            risk_path = write_risk_event(
                                args,
                                block_reason,
                                stage="crawl",
                                taskUrl=output.get("taskUrl"),
                                source=output.get("source"),
                            )
                        if args.stop_on_risk:
                            print_worker_event("环境进入验证页，worker 已停止", risk_record=str(risk_path.resolve()))
                            return 1
                        pause_worker_after_risk(
                            args,
                            shared_probe,
                            block_reason,
                            risk_path,
                            stage="crawl",
                        )
                        consecutive_shopee_errors = 0
                        continue
                    if has_shopee_error(output, 90309999):
                        consecutive_shopee_errors += 1
                        print_worker_event(
                            "检测到 Shopee 90309999",
                            consecutive_shopee_errors=consecutive_shopee_errors,
                        )
                        if (
                            args.max_consecutive_shopee_errors
                            and consecutive_shopee_errors >= args.max_consecutive_shopee_errors
                        ):
                            risk_path = block_current_profile(
                                args,
                                "连续 Shopee 90309999，停止 worker",
                                stage="crawl",
                                consecutive_shopee_errors=consecutive_shopee_errors,
                                taskUrl=output.get("taskUrl"),
                            )
                            if args.stop_on_risk:
                                print_worker_event("连续 Shopee 异常，worker 已停止", risk_record=str(risk_path.resolve()))
                                return 1
                            pause_worker_after_risk(
                                args,
                                shared_probe,
                                "连续 Shopee 90309999，worker 冷却后继续",
                                risk_path,
                                stage="crawl",
                            )
                            consecutive_shopee_errors = 0
                            continue
                    else:
                        consecutive_shopee_errors = 0

                if output is not None:
                    raw_returned_home = output.get("returned_home")
                    if isinstance(raw_returned_home, dict) and raw_returned_home.get("ok") is False:
                        home_reason = str(raw_returned_home.get("reason") or "采集后回首页失败")
                        if is_environment_error(home_reason):
                            risk_path = block_current_profile(
                                args,
                                home_reason,
                                stage="return_home",
                                taskUrl=output.get("taskUrl"),
                            )
                            if args.stop_on_risk:
                                print_worker_event("采集后回首页进入异常，worker 已停止", risk_record=str(risk_path.resolve()))
                                return 1
                            pause_worker_after_risk(
                                args,
                                shared_probe,
                                home_reason,
                                risk_path,
                                stage="return_home",
                            )
                            consecutive_shopee_errors = 0
                            continue
                        print_worker_event("采集后回首页失败", reason=home_reason)

                if args.success_limit and success_count >= args.success_limit:
                    print_worker_event("达到本轮成功上限，worker 已停止", success_count=success_count)
                    return code
                if args.once or (args.max_tasks and processed >= args.max_tasks):
                    return code
                if output is None and empty_pull_sleep > 0:
                    time.sleep(empty_pull_sleep)
        finally:
            shared_probe.close()
    except KeyboardInterrupt:
        print(json.dumps({"ok": True, "message": "已停止"}, ensure_ascii=False))
        return 0
    except WorkerError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
