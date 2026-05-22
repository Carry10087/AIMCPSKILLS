#!/usr/bin/env python3
"""Shopee SSR browser probe.

Connects to an already-open Chrome/AdsPower CDP session, opens a product page,
and extracts cachedMap from the rendered page HTML.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

import requests

BASE_HOST = "https://shopee.tw"
DEFAULT_TIMEOUT = 15
_DEBUG_PORT_CACHE: int | None = None
_DEBUG_PORT_LOCK = threading.Lock()

SSR_EXTRACT_JS = """
() => {
    const result = {};

    if (window.DOMAIN_PDP_DATA && window.DOMAIN_PDP_DATA.cachedMap) {
        result.cachedMap = window.DOMAIN_PDP_DATA.cachedMap;
        result.source = 'DOMAIN_PDP_DATA';
        return result;
    }

    const html = document.documentElement.outerHTML;
    const marker = '"cachedMap":';
    const markerPos = html.indexOf(marker);
    if (markerPos >= 0) {
        const braceStart = html.indexOf('{', markerPos + marker.length);
        let depth = 0;
        let inString = false;
        let escaped = false;
        for (let index = braceStart; index < html.length; index++) {
            const char = html[index];
            if (inString) {
                if (escaped) escaped = false;
                else if (char === '\\\\') escaped = true;
                else if (char === '"') inString = false;
                continue;
            }
            if (char === '"') inString = true;
            else if (char === '{') depth++;
            else if (char === '}') {
                depth--;
                if (depth === 0) {
                    try {
                        result.cachedMap = JSON.parse(html.substring(braceStart, index + 1));
                        result.source = 'html_parse';
                    } catch (error) {
                        result.rawText = html.substring(braceStart, index + 1);
                        result.parseError = error.message;
                        result.source = 'html_raw';
                    }
                    return result;
                }
            }
        }
    }

    result.source = 'none';
    result.url = location.href;
    result.textPreview = document.body ? document.body.innerText.slice(0, 500) : '';
    return result;
}
"""

DOM_PRICE_EXTRACT_JS = r"""
() => {
    const priceRe = /\$\s*([0-9][0-9,]*)(?:\s*-\s*\$\s*([0-9][0-9,]*))?/;
    const matches = [];
    for (const el of document.querySelectorAll('body *')) {
        const raw = (el.innerText || el.textContent || '').trim();
        if (!raw || raw.length > 80 || !raw.includes('$')) continue;
        const text = raw.replace(/\s+/g, ' ');
        const match = text.match(priceRe);
        if (!match) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        const fontSize = parseFloat(style.fontSize) || 0;
        const isRange = Boolean(match[2]);
        const exactish = text.length <= match[0].length + 8;
        const score = fontSize * 10 + (isRange ? 120 : 0) + (exactish ? 30 : 0) - Math.max(rect.top, 0) * 0.01;
        matches.push({
            text: match[0],
            min: match[1],
            max: match[2] || match[1],
            fontSize,
            score,
        });
    }
    matches.sort((a, b) => b.score - a.score);
    return {
        priceText: matches.length ? matches[0].text : null,
        minText: matches.length ? matches[0].min : null,
        maxText: matches.length ? matches[0].max : null,
        fontSize: matches.length ? matches[0].fontSize : null,
        score: matches.length ? matches[0].score : null,
        hits: matches.slice(0, 5).map((match) => match.text),
    };
}
"""


def product_url(shop_id: int, item_id: int) -> str:
    return f"{BASE_HOST}/product/{shop_id}/{item_id}"


def parse_product_ids(value: str) -> tuple[int, int]:
    text = urllib.parse.unquote(value.strip())
    for pat in [
        r"(?:^|[^\w])i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)(?:[^\d]|$)",
        r"/product/(?P<shop_id>\d+)/(?P<item_id>\d+)(?:[^\d]|$)",
    ]:
        m = re.search(pat, text)
        if m:
            return int(m.group("shop_id")), int(m.group("item_id"))
    parsed = urllib.parse.urlparse(text)
    q = urllib.parse.parse_qs(parsed.query)
    if "shop_id" in q and "item_id" in q:
        return int(q["shop_id"][0]), int(q["item_id"][0])
    raise RuntimeError("无法从链接中找到 shop_id/item_id")


def _int_or_none(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None


def _devtools_active_ports() -> list[int]:
    if os.name != "nt":
        return []
    script = r"""
$ports = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'chrome|msedge|SunBrowser|browser' -and $_.CommandLine -match 'remote-debugging-port' } |
  ForEach-Object {
    $cmd = $_.CommandLine
    $userDir = $null
    if ($cmd -match '--user-data-dir="([^"]+)"') { $userDir = $matches[1] }
    elseif ($cmd -match '--user-data-dir=([^\s]+)') { $userDir = $matches[1] }
    if ($userDir) {
      $file = Join-Path $userDir 'DevToolsActivePort'
      if (Test-Path -LiteralPath $file) {
        Get-Content -LiteralPath $file -TotalCount 1 -ErrorAction SilentlyContinue
      }
    }
  }
$ports | Sort-Object -Unique
"""
    try:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=4,
            startupinfo=startupinfo,
        )
    except Exception:
        return []
    ports: list[int] = []
    for line in result.stdout.splitlines():
        port = _int_or_none(line.strip())
        if port is not None:
            ports.append(port)
    return ports


def find_debug_port(preferred_port: int | None = None) -> int | None:
    global _DEBUG_PORT_CACHE

    def _check(port: int) -> int | None:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=0.2)
            if response.ok and "webSocketDebuggerUrl" in response.text:
                return port
        except Exception:
            return None
        return None

    candidates: list[int | None] = [
        preferred_port,
        _DEBUG_PORT_CACHE,
        _int_or_none(os.getenv("BROWSER_DEBUG_PORT")),
        _int_or_none(os.getenv("ADSPOWER_DEBUG_PORT")),
    ]
    candidates.extend(_devtools_active_ports())
    candidates.extend(range(9222, 9231))
    checked: set[int] = set()
    for candidate in candidates:
        port = _int_or_none(candidate)
        if port is None or port in checked:
            continue
        checked.add(port)
        result = _check(port)
        if result is not None:
            with _DEBUG_PORT_LOCK:
                _DEBUG_PORT_CACHE = result
            return result
    return None


def _price(v: Any) -> str | None:
    if v is None or v == -1:
        return None
    try:
        return f"{int(v) / 100000:.2f}"
    except Exception:
        return None


def _parse_price_int(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return int(digits)


def _shopee_price_units(value: int | None) -> int | None:
    if value is None:
        return None
    return value * 100000


def dom_price_ready(dom_price: Any) -> bool:
    if not isinstance(dom_price, dict):
        return False
    min_twd = _parse_price_int(dom_price.get("minText"))
    max_twd = _parse_price_int(dom_price.get("maxText"))
    if min_twd is None or max_twd is None:
        return False
    try:
        font_size = float(dom_price.get("fontSize") or 0)
        score = float(dom_price.get("score") or 0)
    except (TypeError, ValueError):
        font_size = 0
        score = 0
    return min_twd != max_twd or font_size >= 24 or score >= 220


def apply_dom_price_fallback(store: dict[str, Any], dom_price: Any) -> bool:
    if not isinstance(dom_price, dict):
        return False
    item = store.get("item")
    if not isinstance(item, dict):
        return False

    min_twd = _parse_price_int(dom_price.get("minText"))
    max_twd = _parse_price_int(dom_price.get("maxText"))
    if min_twd is None or max_twd is None:
        return False

    min_units = _shopee_price_units(min_twd)
    max_units = _shopee_price_units(max_twd)
    item["price_min"] = min_units
    item["price_max"] = max_units
    if min_twd == max_twd:
        item["price"] = min_units
    else:
        item["price"] = None

    store["_dom_price_fallback"] = {
        "source": "dom_text",
        "price_text": dom_price.get("priceText"),
        "price_min_twd": min_twd,
        "price_max_twd": max_twd,
    }
    return True


def _txt(v: Any, n: int = 200) -> str | None:
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v).strip()
    return v if len(v) <= n else v[: n - 3] + "..."


def _price_field(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return value
    for key in keys:
        found = value.get(key)
        if found not in (None, -1):
            return found
    return None


def summarize(store: dict[str, Any]) -> dict[str, Any]:
    item = store.get("item") or {}
    pp = store.get("product_price") or {}
    review = store.get("product_review") or {}
    shop = store.get("shop_detailed") or {}
    images_data = store.get("product_images") or {}
    images = images_data.get("images") or []
    models = item.get("models") or []
    pm = pp.get("price_model") or {}
    price_box = pp.get("price")
    price_twd = _price(item.get("price") or _price_field(price_box, "single_value", "range_min", "range_max") or pp.get("price"))
    price_min_twd = _price(item.get("price_min") or _price_field(price_box, "range_min", "single_value") or pp.get("price_min"))
    price_max_twd = _price(item.get("price_max") or _price_field(price_box, "range_max", "single_value") or pp.get("price_max"))
    has_price = any((price_twd, price_min_twd, price_max_twd))
    dom_price_fallback = store.get("_dom_price_fallback") or {}

    return dict(
        has_real_data=bool(item),
        has_price=has_price,
        shopee_error=None,
        shopee_error_msg=None,
        item_id=item.get("item_id"),
        shop_id=item.get("shop_id"),
        title=item.get("title"),
        currency=item.get("currency"),
        brand=item.get("brand"),
        price_twd=price_twd,
        price_min_twd=price_min_twd,
        price_max_twd=price_max_twd,
        stock=next((m.get("stock") for m in models if m.get("stock")), item.get("stock")),
        normal_stock=next((m.get("normal_stock") for m in models if m.get("normal_stock")), item.get("normal_stock")),
        models=len(models),
        images=len(images),
        rating_star=review.get("rating_star") or (item.get("item_rating") or {}).get("rating_star"),
        total_rating_count=(review.get("total_rating_count") or (item.get("item_rating") or {}).get("total_rating_count")),
        liked_count=review.get("liked_count"),
        shop_name=shop.get("name"),
        shop_location=shop.get("shop_location") or item.get("shop_location"),
        description_preview=_txt(item.get("description") or store.get("product_description", {}).get("description")),
        review_count=review.get("cmt_count") or 0,
        price_model_id=pm.get("price_single_model_id"),
        price_source="dom_text" if dom_price_fallback else "ssr_cachedMap",
        dom_price_text=dom_price_fallback.get("price_text") if isinstance(dom_price_fallback, dict) else None,
        source="ssr_cachedMap",
    )


def write_json(path: str, value: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ProbeError(RuntimeError):
    pass


class ShopeeProbe:
    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        debug_port: int | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.timeout = timeout
        self._debug_port: int | None = debug_port
        self._browser: Any | None = None
        self._page: Any | None = None
        self._loop = loop
        self._own_loop = loop is None
        self._closed = False

    def _run(self, coro):
        if self._closed:
            raise ProbeError("ShopeeProbe 已关闭。")
        if self._own_loop:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            return self._loop.run_until_complete(coro)
        else:
            # Running inside an existing event loop - use the async method directly
            raise ProbeError(
                "请在异步上下文中使用 'await probe.fetch_ssr_async()' 而非 fetch_first_usable()"
            )

    @property
    def has_session(self) -> bool:
        return True  # SSR doesn't need login

    def warmup(self, page_url: str, full_page: bool = False) -> None:
        pass  # SSR doesn't need warmup

    def assert_ready(self) -> None:
        self._run(self._assert_ready_async())

    async def _assert_ready_async(self) -> None:
        await self._ensure_page()

    def reset_connection(self) -> None:
        if self._own_loop:
            self._run(self._disconnect_async())
        else:
            self._browser = None
            self._page = None

    def reopen_page(self, page_url: str) -> None:
        self._run(self._reopen_page_async(page_url))

    async def _reopen_page_async(self, page_url: str) -> None:
        await self._disconnect_async()
        page = await self._ensure_page()
        await page.goto(page_url, waitUntil="domcontentloaded", timeout=15000)

    def return_home(self, home_url: str) -> None:
        self._run(self._return_home_async(home_url))

    async def _return_home_async(self, home_url: str) -> None:
        page = await self._ensure_page()
        await page.goto(home_url, waitUntil="domcontentloaded", timeout=15000)

    def fetch_first_usable(
        self, endpoints: Sequence[str], shop_id: int, item_id: int, referer: str,
        full_page: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._run(self._fetch_ssr(shop_id, item_id, referer, full_page=full_page))

    async def fetch_ssr_async(
        self, shop_id: int, item_id: int, referer: str, *, full_page: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return await self._fetch_ssr(shop_id, item_id, referer, full_page=full_page)

    async def _ensure_page(self) -> Any:
        from pyppeteer import connect

        if self._browser is not None and self._page is not None:
            return self._page

        port = find_debug_port(self._debug_port)
        if not port:
            raise ProbeError("未找到可用浏览器 CDP 调试端口。")
        self._debug_port = port
        info_url = f"http://127.0.0.1:{port}"
        version = requests.get(f"{info_url}/json/version", timeout=5).json()
        ws_url = version["webSocketDebuggerUrl"]
        browser = await connect(browserWSEndpoint=ws_url)
        page = await browser.newPage()
        self._browser = browser
        self._page = page
        return page

    async def _fetch_ssr(
        self, shop_id: int, item_id: int, referer: str, *, full_page: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        page = await self._ensure_page()
        product = referer or product_url(shop_id, item_id)
        await page.goto(product, waitUntil="domcontentloaded", timeout=15000)
        try:
            await page.waitForFunction(
                "document.body && /\\$\\s*\\d/.test(document.body.innerText)",
                timeout=3000,
            )
        except Exception:
            pass

        raw = await page.evaluate(SSR_EXTRACT_JS)
        dom_price: Any = {}
        for _ in range(7):
            dom_price = await page.evaluate(DOM_PRICE_EXTRACT_JS)
            if dom_price_ready(dom_price):
                break
            await asyncio.sleep(0.5)
        source = raw.get("source", "none")
        store: dict[str, Any] = {}
        dom_price_applied = False

        if raw.get("cachedMap"):
            raw_store = raw["cachedMap"].get(f"{shop_id}/{item_id}", {})
            if isinstance(raw_store, dict):
                store = raw_store
        elif raw.get("rawText"):
            try:
                parsed = json.loads(raw["rawText"])
                if isinstance(parsed, dict):
                    raw_store = parsed.get(f"{shop_id}/{item_id}", {})
                    if isinstance(raw_store, dict):
                        store = raw_store
            except json.JSONDecodeError:
                pass

        if store:
            dom_price_applied = apply_dom_price_fallback(store, dom_price)

        if store:
            body = {"bff_meta": None, "error": None, "error_msg": None, "data": store}
        else:
            current_url = str(raw.get("url") or product)
            text_preview = str(raw.get("textPreview") or "")
            error = "SSR_NOT_FOUND"
            message = f"无法从页面 SSR 提取商品数据 (source={source})"
            if "/verify/traffic/error" in current_url:
                error = "SHOPEE_VERIFY_PAGE"
                message = f"Shopee 当前会话进入验证页: {current_url}。页面提示: {text_preview}"
            body = {
                "bff_meta": None,
                "error": error,
                "error_msg": message,
                "data": None,
            }

        summary = summarize(store) if store else {"has_real_data": False}
        attempt = {
            "endpoint": "ssr_cachedMap",
            "http_status": 200 if store else 500,
            "shopee_error": body.get("error"),
            "shopee_error_msg": body.get("error_msg"),
            "has_real_data": bool(summary.get("has_real_data")),
            "url": product,
            "source": source,
            "dom_price_text": dom_price.get("priceText") if isinstance(dom_price, dict) else None,
            "dom_price_applied": dom_price_applied,
        }

        selected = {
            "body": body,
            "http_status": attempt["http_status"],
            "endpoint": attempt["endpoint"],
            "url": attempt["url"],
        }
        return selected, [attempt]

    async def _disconnect_async(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            finally:
                self._page = None
        if self._browser is not None:
            try:
                await self._browser.disconnect()
            finally:
                self._browser = None

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._own_loop and self._browser is not None:
                self._run(self._disconnect_async())
        finally:
            if self._own_loop and self._loop is not None:
                try:
                    self._loop.close()
                except Exception:
                    pass
            self._closed = True
            self._browser = None
            self._page = None
            self._loop = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def summarize_get_pc(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data") if isinstance(body, dict) else None
    store = data if isinstance(data, dict) else body if isinstance(body, dict) else {}
    summary = summarize(store)
    if isinstance(body, dict):
        summary["shopee_error"] = body.get("error")
        summary["shopee_error_msg"] = body.get("error_msg")
        if body.get("error"):
            summary["has_real_data"] = False
    return summary


def public_product_url(shop_id: int, item_id: int) -> str:
    return product_url(shop_id, item_id)


def configure_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8")


ENDPOINTS = ("get_pc",)


async def main_async(args: argparse.Namespace) -> int:
    configure_stdout()
    probe = ShopeeProbe(
        timeout=args.timeout,
        debug_port=args.debug_port or None,
        loop=asyncio.get_running_loop(),
    )
    probe._own_loop = False

    try:
        shop_id, item_id = args.shop_id, args.item_id
        referer = product_url(shop_id, item_id)

        fetched, attempts = await probe._fetch_ssr(
            shop_id, item_id, referer,
            full_page=False,
        )

        body = fetched["body"]
        store = body.get("data") or {}
        s = summarize(store)

        out_path = args.out
        write_json(out_path, body)
        result = dict(
            ok=s["has_real_data"],
            source=attempts[0]["endpoint"] if attempts else "none",
            http_status=fetched["http_status"],
            input=dict(shop_id=shop_id, item_id=item_id),
            attempts=attempts,
            summary=s,
            raw_response_path=out_path,
        )
        write_json(str(Path(out_path).with_name("last_result.json")), result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result["ok"] else 2
    finally:
        await probe._disconnect_async()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Shopee 台湾商品探测 (SSR 无登录)")
    p.add_argument("--shop-id", type=int)
    p.add_argument("--item-id", type=int)
    p.add_argument("--out", default="out/last_get_pc.json")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--endpoint", nargs="+", default=list(ENDPOINTS))
    p.add_argument("--debug-port", type=int, default=0)
    args = p.parse_args(argv)

    try:
        return asyncio.run(main_async(args))
    except RuntimeError as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
