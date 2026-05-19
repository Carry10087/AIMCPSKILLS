# -*- coding: utf-8 -*-
"""Entry point that delegates the verified flow to env_patch_v8.js.

The jsdom driver owns conf/register, image gap detection, trajectory
generation, encryption, and fverify submission. Python only launches the
driver and parses its machine-readable stdout payload.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOG_DIR


ROOT = Path(__file__).resolve().parent
ENV_PATCH_DIR = ROOT / "env_patch"
ENV_PATCH_SCRIPT = ENV_PATCH_DIR / "env_patch_v8.js"
NODE_TIMEOUT_SEC = 130
CONSOLE_LINE_LIMIT = 240
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_SHOW_ATTEMPTS = False


def shorten(value: str, limit: int = CONSOLE_LINE_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... ({len(value)} chars)"


def zh_status(status: str) -> str:
    return {
        "result": "已获取响应",
        "captured": "已捕获请求",
    }.get(status, status)


def zh_source(source: str) -> str:
    return {
        "script_jsonp": "script JSONP 捕获",
        "xhr": "XHR 捕获",
        "artifact": "本地结果文件",
    }.get(source, source)


def is_pass(payload: dict[str, Any]) -> bool:
    result = payload.get("result") or {}
    return result.get("code") == 1100 and result.get("riskLevel") == "PASS"


def result_brief(payload: dict[str, Any]) -> str:
    result = payload.get("result") or {}
    if not result:
        return "未获取响应"
    return (
        f"code={result.get('code')}, "
        f"message={result.get('message')}, "
        f"riskLevel={result.get('riskLevel', 'N/A')}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数美滑块 jsdom 求解入口")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"后台最多尝试次数，默认 {DEFAULT_MAX_ATTEMPTS}",
    )
    parser.add_argument(
        "--show-attempts",
        action="store_true",
        default=DEFAULT_SHOW_ATTEMPTS,
        help="在控制台显示每次尝试的详细诊断",
    )
    return parser.parse_args()


def setup_logging() -> logging.Logger:
    log_dir = ROOT / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"ishumei_slider_{timestamp}.log"

    logger = logging.getLogger("ishumei_slider")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_fmt)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logger.info("日志文件: %s", log_file)
    return logger


def parse_solver_stdout(stdout: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("url"):
            payloads.append(payload)
    return payloads


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def process_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def enrich_from_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("result"):
        result = read_json_file(ENV_PATCH_DIR / "fverify_response.txt")
        if result:
            payload["result"] = result
            payload["status"] = "result"

    if not payload.get("url"):
        url_path = ENV_PATCH_DIR / "fverify_url.txt"
        if url_path.exists():
            try:
                payload["url"] = url_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass

    return payload


def log_interesting_diagnostics(logger: logging.Logger, stderr: str) -> None:
    markers = (
        "[REG DATA]",
        "[DRAG] Python OpenCV gap",
        "[DRAG] targetX",
        "[DIAG] trajectory",
        "[FVERIFY",
        "DONE fverify",
        "TIMEOUT",
        "[FATAL]",
        "[UNCAUGHT]",
        "[UNHANDLED-REJECTION]",
    )
    emitted = 0
    for line in stderr.splitlines():
        if any(marker in line for marker in markers):
            logger.info("jsdom: %s", translate_jsdom_line(line))
            emitted += 1
            if emitted >= 80:
                logger.info("jsdom: ... 控制台诊断已截断，完整内容见诊断文件")
                break


def translate_jsdom_line(line: str) -> str:
    line = line.strip()
    if "[REG DATA] rid:" in line:
        return line.replace("[REG DATA] rid:", "注册成功，rid:")
    if "[DRAG] Python OpenCV gap:" in line:
        return line.replace("[DRAG] Python OpenCV gap:", "OpenCV 识别缺口:")
    if "[DRAG] targetX from external:" in line:
        return line.replace("[DRAG] targetX from external:", "使用外部缺口距离:")
    if "[DIAG] trajectory:" in line:
        return line.replace("[DIAG] trajectory:", "轨迹诊断:")
    if "[FVERIFY JSONP CAPTURED!]" in line:
        return "已捕获 fverify JSONP 请求"
    if "[FVERIFY] Full URL:" in line:
        return shorten(line.replace("[FVERIFY] Full URL:", "fverify URL:"))
    if "[FVERIFY RESPONSE]" in line:
        return (
            line.replace("[FVERIFY RESPONSE]", "fverify 响应")
            .replace("code:", "code:")
            .replace("message:", "message:")
            .replace("riskLevel:", "riskLevel:")
        )
    if "DONE fverify" in line:
        return "fverify 已完成，等待诊断写入..."
    if "TIMEOUT" in line:
        return line.replace("TIMEOUT", "超时")
    if "[FATAL]" in line:
        return line.replace("[FATAL]", "致命错误")
    if "[UNCAUGHT]" in line:
        return line.replace("[UNCAUGHT]", "未捕获异常")
    if "[UNHANDLED-REJECTION]" in line:
        return line.replace("[UNHANDLED-REJECTION]", "未处理 Promise 异常")
    return shorten(line)


def run_jsdom_solver(
    logger: logging.Logger,
    *,
    attempt: int | None = None,
    show_console: bool = True,
) -> dict[str, Any]:
    if not ENV_PATCH_SCRIPT.exists():
        raise FileNotFoundError(f"Missing jsdom driver: {ENV_PATCH_SCRIPT}")

    cmd = ["node", str(ENV_PATCH_SCRIPT)]
    log = logger.info if show_console else logger.debug
    attempt_label = f"第 {attempt} 次" if attempt is not None else "本次"
    log("%s启动 jsdom 求解器: %s", attempt_label, " ".join(cmd))
    start = time.time()

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(ENV_PATCH_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=NODE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = process_stream_text(exc.stderr)
        if stderr:
            if attempt is None:
                stderr_path = ENV_PATCH_DIR / "last_env_patch_stderr.log"
            else:
                stderr_path = ENV_PATCH_DIR / f"last_env_patch_stderr_attempt_{attempt:02d}.log"
            stderr_path.write_text(stderr, encoding="utf-8")
            (ENV_PATCH_DIR / "last_env_patch_stderr.log").write_text(
                stderr, encoding="utf-8"
            )
            log("%s jsdom 诊断文件: %s", attempt_label, stderr_path)
            if show_console:
                log_interesting_diagnostics(logger, stderr)
        raise TimeoutError(
            f"jsdom 求解器超时，已运行 {NODE_TIMEOUT_SEC}s"
        ) from exc

    elapsed_ms = int((time.time() - start) * 1000)
    log("%s Node 退出码: %s，耗时: %sms", attempt_label, completed.returncode, elapsed_ms)

    if completed.stderr:
        if attempt is None:
            stderr_path = ENV_PATCH_DIR / "last_env_patch_stderr.log"
        else:
            stderr_path = ENV_PATCH_DIR / f"last_env_patch_stderr_attempt_{attempt:02d}.log"
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        (ENV_PATCH_DIR / "last_env_patch_stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        log("%s jsdom 诊断文件: %s", attempt_label, stderr_path)
        if show_console:
            log_interesting_diagnostics(logger, completed.stderr)

    if completed.returncode != 0:
        raise RuntimeError(f"jsdom 求解器失败，退出码 {completed.returncode}")

    payloads = parse_solver_stdout(completed.stdout)
    payload = next(
        (item for item in reversed(payloads) if item.get("result")),
        payloads[-1] if payloads else {},
    )
    payload = enrich_from_artifacts(payload)

    if not payload.get("url"):
        raise RuntimeError(
            "jsdom 求解器已结束，但没有输出 fverify URL 或结果"
        )

    payload["elapsed_ms"] = elapsed_ms
    return payload


def solve_with_retries(
    logger: logging.Logger,
    max_attempts: int,
    *,
    show_attempts: bool = False,
) -> dict[str, Any]:
    last_payload: dict[str, Any] | None = None
    if show_attempts:
        logger.info("详细尝试日志已开启")
    else:
        logger.info("正在求解，控制台只显示最终结果（后台最多 %s 次）", max_attempts)

    for attempt in range(1, max_attempts + 1):
        if show_attempts:
            logger.info("-" * 60)
            logger.info("第 %s/%s 次尝试", attempt, max_attempts)
        else:
            logger.debug("第 %s/%s 次后台尝试", attempt, max_attempts)

        payload = run_jsdom_solver(
            logger,
            attempt=attempt,
            show_console=show_attempts,
        )
        payload["attempt"] = attempt
        payload["max_attempts"] = max_attempts
        last_payload = payload

        if is_pass(payload):
            if show_attempts:
                logger.info("第 %s 次尝试验证通过", attempt)
            else:
                logger.info("已获取 PASS 结果")
            return payload

        if show_attempts:
            logger.warning("第 %s 次尝试未通过：%s", attempt, result_brief(payload))
            if attempt < max_attempts:
                logger.info("准备重新获取验证码并重试...")
        else:
            logger.debug("第 %s 次后台尝试未通过：%s", attempt, result_brief(payload))

    if last_payload is None:
        raise RuntimeError("没有完成任何一次 jsdom 尝试")
    logger.warning("已重试 %s 次，仍未得到 PASS 结果", max_attempts)
    return last_payload


def summarize(
    logger: logging.Logger,
    payload: dict[str, Any],
    *,
    show_attempts: bool = False,
) -> None:
    result = payload.get("result") or {}
    reg = payload.get("reg") or {}

    logger.info("=" * 60)
    logger.info("任务总结")
    logger.info("=" * 60)
    logger.info("来源: %s", zh_source(payload.get("source", "artifact")))
    logger.info("状态: %s", zh_status(payload.get("status", "captured")))
    logger.info("耗时: %sms", payload.get("elapsed_ms", "N/A"))
    if payload.get("attempt") and (show_attempts or not is_pass(payload)):
        logger.info("尝试次数: %s/%s", payload.get("attempt"), payload.get("max_attempts"))
    if reg.get("rid"):
        logger.info("rid: %s", reg.get("rid"))
    url = payload.get("url", "")
    logger.info("fverify URL: %s", shorten(url))
    logger.debug("完整 fverify URL: %s", url)

    if result:
        logger.info("code: %s", result.get("code"))
        logger.info("message: %s", result.get("message"))
        logger.info("riskLevel: %s", result.get("riskLevel", "N/A"))
        logger.info("score: %s", result.get("score", "N/A"))
        if result.get("code") == 1100 and result.get("riskLevel") == "PASS":
            logger.info("验证通过")
        else:
            logger.warning("验证请求成功，但结果不是 PASS")
    else:
        logger.warning("仅捕获到 fverify URL，没有解析到响应结果")


def main() -> int:
    args = parse_args()
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("数美滑块求解器 - jsdom 子进程路径")
    logger.info("时间: %s", datetime.now().isoformat())
    logger.info("=" * 60)

    max_attempts = max(1, args.max_attempts)
    payload = solve_with_retries(
        logger,
        max_attempts,
        show_attempts=args.show_attempts,
    )
    summarize(logger, payload, show_attempts=args.show_attempts)
    return 0 if is_pass(payload) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.getLogger("ishumei_slider").exception("致命错误: %s", exc)
        raise SystemExit(1)
