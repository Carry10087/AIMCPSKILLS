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
BIGGO_SEARCH_BASE = "https://biggo.com.tw/s/"
DEFAULT_TIMEOUT = 15
_DEBUG_PORT_CACHE: int | None = None
_DEBUG_PORT_LOCK = threading.Lock()
BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}
BLOCKED_URL_PARTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "facebook.com/tr",
)

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
    const priceRe = /\$\s*([0-9][0-9,]*)(?:\s*-\s*\$?\s*([0-9][0-9,]*))?/;
    const badContextRe = /(優惠券|折抵|運費|補償|低消|最高折抵|領取|配達|免運|折\$)/;
    const matches = [];
    const addMatch = (el, raw, depth) => {
        if (!raw || raw.length > 120 || !raw.includes('$')) return;
        const text = raw.replace(/\s+/g, ' ');
        if (badContextRe.test(text)) return;
        const match = text.match(priceRe);
        if (!match) return;
        const minValue = Number(String(match[1] || '').replace(/,/g, ''));
        const maxValue = Number(String(match[2] || match[1] || '').replace(/,/g, ''));
        if (!Number.isFinite(minValue) || !Number.isFinite(maxValue) || minValue <= 0 || maxValue <= 0) return;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return;
        const fontSize = parseFloat(style.fontSize) || 0;
        const color = style.color || '';
        const isRange = Boolean(match[2]);
        const exactish = text.length <= match[0].length + 8;
        const redish = /rgb\(\s*(?:208|238|255|198|214)\s*,\s*(?:1|87|0|48)\s*,\s*(?:27|34|0|49)\s*\)/.test(color);
        const inMainArea = rect.top >= 180 && rect.top <= 520 && rect.left >= 360;
        const large = fontSize >= 24;
        const score =
            fontSize * 12
            + (redish ? 180 : 0)
            + (large ? 160 : 0)
            + (inMainArea ? 140 : 0)
            + (isRange ? 100 : 0)
            + (exactish ? 90 : 0)
            - depth * 55
            - Math.max(rect.top - 260, 0) * 0.03;
        matches.push({
            text: match[0],
            min: match[1],
            max: match[2] || match[1],
            fontSize,
            color,
            top: rect.top,
            left: rect.left,
            exactish,
            score,
        });
    };

    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const raw = (node.nodeValue || '').trim();
        if (!raw || !raw.includes('$')) continue;
        let el = node.parentElement;
        for (let depth = 0; el && depth < 4; depth++, el = el.parentElement) {
            addMatch(el, raw, depth);
        }
    }

    for (const el of document.querySelectorAll('body *')) {
        const raw = (el.innerText || el.textContent || '').trim();
        addMatch(el, raw, 4);
    }
    matches.sort((a, b) => b.score - a.score);
    return {
        priceText: matches.length ? matches[0].text : null,
        minText: matches.length ? matches[0].min : null,
        maxText: matches.length ? matches[0].max : null,
        fontSize: matches.length ? matches[0].fontSize : null,
        score: matches.length ? matches[0].score : null,
        hits: matches.slice(0, 8).map((match) => match.text),
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


def url_matches_product(value: str, shop_id: int, item_id: int) -> bool:
    text = urllib.parse.unquote(value or "")
    if "/verify/" in text:
        return False
    return any(
        marker in text
        for marker in (
            f"/product/{shop_id}/{item_id}",
            f"i.{shop_id}.{item_id}",
            f"shopid={shop_id}&itemid={item_id}",
            f"itemid={item_id}&shopid={shop_id}",
        )
    )


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


def biggo_search_url(title: str) -> str:
    keyword = re.sub(r"\s+", " ", title or "").strip()
    return f"{BIGGO_SEARCH_BASE}{urllib.parse.quote(keyword, safe='')}"


def fetch_biggo_price(
    title: str,
    shop_id: int,
    item_id: int,
    timeout: float = 12.0,
) -> tuple[dict[str, Any], str | None, int | None, str | None]:
    if not title:
        return {}, None, None, "商品标题为空，无法查询 BigGo"
    url = biggo_search_url(title)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        return {}, url, None, f"BigGo 请求失败: {exc}"
    if response.status_code >= 400:
        return {}, url, response.status_code, f"BigGo HTTP {response.status_code}"

    html_text = response.text
    target = f"id={shop_id}.{item_id}"
    start = html_text.find(target)
    if start < 0:
        encoded_product = urllib.parse.quote(f"https://shopee.tw/product/{shop_id}/{item_id}", safe="")
        start = html_text.find(encoded_product)
    if start < 0:
        return {}, url, response.status_code, "BigGo 结果未找到目标商品 ID"

    price_match = None
    for match in re.finditer(r'data-price="true"[^>]*>\s*\$\s*([0-9][0-9,]*)(?:\s*-\s*\$?\s*([0-9][0-9,]*))?', html_text[start : start + 12000]):
        price_match = match
        break
    if price_match is None:
        return {}, url, response.status_code, "BigGo 目标商品附近未找到价格"

    price_text = price_match.group(0).split(">", 1)[-1].strip()
    min_text = price_match.group(1)
    max_text = price_match.group(2) or min_text
    return {
        "source": "biggo_result_text",
        "priceText": price_text,
        "minText": min_text,
        "maxText": max_text,
        "fontSize": 18,
        "score": 300,
        "href": url,
        "hits": [price_text],
    }, url, response.status_code, None


def _model_id(model: dict[str, Any]) -> int | None:
    return _int_or_none(model.get("model_id") or model.get("modelid"))


def _remove_removed_field(store: dict[str, Any], field_name: str) -> None:
    removed_fields = store.get("removed_fields")
    if not isinstance(removed_fields, list):
        return
    store["removed_fields"] = [field for field in removed_fields if field != field_name] or None


def _valid_price_units(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def _valid_price_box(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_valid_price_units(value.get(key)) for key in ("single_value", "range_min", "range_max"))


def _final_price_discount_units(product_price: dict[str, Any]) -> int:
    final_price_info = product_price.get("final_price_info")
    if not isinstance(final_price_info, dict):
        return 0
    vouchers = final_price_info.get("final_price_vouchers")
    if not isinstance(vouchers, dict):
        return 0
    discount = 0
    for voucher_key in ("platform_voucher", "shop_voucher"):
        voucher = vouchers.get(voucher_key)
        if isinstance(voucher, dict):
            voucher_discount = voucher.get("voucher_discount")
            if _valid_price_units(voucher_discount):
                discount += voucher_discount
    return discount


def normalize_price_fields(store: dict[str, Any]) -> None:
    store.pop("_dom_price_fallback", None)
    item = store.get("item")
    if not isinstance(item, dict):
        return

    item_price = item.get("price")
    min_units = item.get("price_min")
    max_units = item.get("price_max")
    single_units = item_price if _valid_price_units(item_price) else None
    if not _valid_price_units(min_units):
        min_units = single_units
    if not _valid_price_units(max_units):
        max_units = single_units
    if not _valid_price_units(min_units) or not _valid_price_units(max_units):
        return
    if min_units == max_units and single_units is None:
        single_units = min_units
        item["price"] = single_units

    product_price = store.get("product_price")
    if not isinstance(product_price, dict):
        product_price = {}
        store["product_price"] = product_price
    product_price.setdefault("hide_price", False)
    product_price.setdefault("has_final_price", True)

    if not _valid_price_box(product_price.get("price")):
        final_discount = _final_price_discount_units(product_price)
        final_single_units = None
        if single_units is not None and final_discount > 0:
            final_single_units = max(single_units - final_discount, 1)
        product_price["price"] = {
            "single_value": final_single_units if final_single_units is not None else (single_units if single_units is not None else -1),
            "range_min": -1 if single_units is not None else min_units,
            "range_max": -1 if single_units is not None else max_units,
            "price_mask": None,
        }
    _remove_removed_field(store, "product_price.price")

    models = item.get("models")
    price_model = product_price.get("price_model")
    if not isinstance(models, list):
        return
    if single_units is not None:
        for model in models:
            if isinstance(model, dict) and model.get("price") in (None, -1):
                model["price"] = single_units
                if model.get("price_before_discount") in (None, -1):
                    model["price_before_discount"] = 0
    elif isinstance(price_model, dict):
        min_model_id = _int_or_none(price_model.get("price_min_model_id"))
        max_model_id = _int_or_none(price_model.get("price_max_model_id"))
        for model in models:
            if not isinstance(model, dict):
                continue
            current_model_id = _model_id(model)
            if current_model_id == min_model_id and model.get("price") in (None, -1):
                model["price"] = min_units
            elif current_model_id == max_model_id and model.get("price") in (None, -1):
                model["price"] = max_units
            if model.get("price") not in (None, -1) and model.get("price_before_discount") in (None, -1):
                model["price_before_discount"] = 0


def dom_price_ready(dom_price: Any) -> bool:
    if not isinstance(dom_price, dict):
        return False
    min_twd = _parse_price_int(dom_price.get("minText"))
    max_twd = _parse_price_int(dom_price.get("maxText"))
    if min_twd is None or max_twd is None:
        return False
    if min_twd <= 0 or max_twd <= 0:
        return False
    try:
        font_size = float(dom_price.get("fontSize") or 0)
        score = float(dom_price.get("score") or 0)
    except (TypeError, ValueError):
        font_size = 0
        score = 0
    return min_twd != max_twd or font_size >= 24 or score >= 220


def apply_dom_price_fallback(store: dict[str, Any], dom_price: Any) -> dict[str, Any] | None:
    if not isinstance(dom_price, dict):
        return None
    item = store.get("item")
    if not isinstance(item, dict):
        return None

    min_twd = _parse_price_int(dom_price.get("minText"))
    max_twd = _parse_price_int(dom_price.get("maxText"))
    if min_twd is None or max_twd is None:
        return None

    store.pop("_dom_price_fallback", None)
    min_units = _shopee_price_units(min_twd)
    max_units = _shopee_price_units(max_twd)
    single_units = min_units if min_twd == max_twd else None
    item["price_min"] = min_units
    item["price_max"] = max_units
    item["price"] = single_units

    normalize_price_fields(store)

    return {
        "source": dom_price.get("source") or "dom_text",
        "price_text": dom_price.get("priceText"),
        "price_min_twd": min_twd,
        "price_max_twd": max_twd,
        "href": dom_price.get("href"),
    }


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


def _first_price_value(*values: Any) -> Any:
    for value in values:
        if _valid_price_units(value):
            return value
    return None


def _any_response_price(store: dict[str, Any]) -> bool:
    item = store.get("item") or {}
    pp = store.get("product_price") or {}
    price_breakdown = store.get("price_breakdown") or {}
    models = item.get("models") or []

    candidates: list[Any] = []
    if isinstance(item, dict):
        candidates.extend(
            item.get(key)
            for key in (
                "price",
                "price_min",
                "price_max",
                "price_before_discount",
                "price_min_before_discount",
                "price_max_before_discount",
            )
        )
    if isinstance(pp, dict):
        for field in ("price", "price_before_discount", "lowest_past_price"):
            value = pp.get(field)
            candidates.extend(
                (
                    _price_field(value, "single_value", "range_min", "range_max"),
                    value,
                )
            )
    if isinstance(price_breakdown, dict):
        for field in ("price", "price_before_discount"):
            value = price_breakdown.get(field)
            candidates.extend(
                (
                    _price_field(value, "single_value", "range_min", "range_max"),
                    value,
                )
            )
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict):
                candidates.extend((model.get("price"), model.get("price_before_discount")))
    return any(_valid_price_units(value) for value in candidates)


def summarize(
    store: dict[str, Any],
    *,
    price_source: str | None = None,
    price_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = store.get("item") or {}
    pp = store.get("product_price") or {}
    review = store.get("product_review") or {}
    shop = store.get("shop_detailed") or {}
    images_data = store.get("product_images") or {}
    images = images_data.get("images") or []
    models = item.get("models") or []
    pm = pp.get("price_model") or {}
    price_box = pp.get("price")
    model_prices = [
        model.get("price")
        for model in models
        if isinstance(model, dict) and _valid_price_units(model.get("price"))
    ]
    price_twd = _price(
        _first_price_value(
            item.get("price"),
            _price_field(price_box, "single_value", "range_min", "range_max"),
            pp.get("price"),
            model_prices[0] if model_prices else None,
        )
    )
    price_min_twd = _price(
        _first_price_value(
            item.get("price_min"),
            _price_field(price_box, "range_min", "single_value"),
            pp.get("price_min"),
            min(model_prices) if model_prices else None,
        )
    )
    price_max_twd = _price(
        _first_price_value(
            item.get("price_max"),
            _price_field(price_box, "range_max", "single_value"),
            pp.get("price_max"),
            max(model_prices) if model_prices else None,
        )
    )
    has_price = any((price_twd, price_min_twd, price_max_twd)) or _any_response_price(store)
    price_meta = price_meta if isinstance(price_meta, dict) else {}
    resolved_price_source = price_source or "ssr_cachedMap"

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
        price_source=resolved_price_source,
        dom_price_text=price_meta.get("price_text") if price_meta else None,
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
        self._page_prepared = False
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
            self._page_prepared = False

    def reopen_page(self, page_url: str) -> None:
        self._run(self._reopen_page_async(page_url))

    async def _reopen_page_async(self, page_url: str) -> None:
        await self._disconnect_async()
        page = await self._ensure_page()
        await self._goto_for_extract(page, page_url)

    def return_home(self, home_url: str) -> None:
        self._run(self._return_home_async(home_url))

    async def _return_home_async(self, home_url: str) -> None:
        page = await self._ensure_page()
        await self._goto_for_extract(page, home_url)

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
            if not self._page_prepared:
                await self._prepare_page(self._page)
            return self._page

        port = find_debug_port(self._debug_port)
        if not port:
            raise ProbeError("未找到可用浏览器 CDP 调试端口。")
        self._debug_port = port
        info_url = f"http://127.0.0.1:{port}"
        version = requests.get(f"{info_url}/json/version", timeout=5).json()
        ws_url = version["webSocketDebuggerUrl"]
        connect_timeout = max(5, min(int(self.timeout or DEFAULT_TIMEOUT), 15))
        browser = await asyncio.wait_for(
            connect(browserWSEndpoint=ws_url),
            timeout=connect_timeout,
        )
        page = await asyncio.wait_for(browser.newPage(), timeout=connect_timeout)
        await self._prepare_page(page)
        self._browser = browser
        self._page = page
        return page

    async def _ensure_product_page(
        self, shop_id: int, item_id: int,
    ) -> tuple[Any, bool]:
        from pyppeteer import connect

        if self._browser is None:
            port = find_debug_port(self._debug_port)
            if not port:
                raise ProbeError("未找到可用浏览器 CDP 调试端口。")
            self._debug_port = port
            info_url = f"http://127.0.0.1:{port}"
            version = requests.get(f"{info_url}/json/version", timeout=5).json()
            ws_url = version["webSocketDebuggerUrl"]
            connect_timeout = max(5, min(int(self.timeout or DEFAULT_TIMEOUT), 15))
            self._browser = await asyncio.wait_for(
                connect(browserWSEndpoint=ws_url),
                timeout=connect_timeout,
            )

        try:
            pages = await self._browser.pages()
        except Exception:
            pages = []

        for candidate in pages:
            try:
                candidate_url = str(getattr(candidate, "url", "") or "")
            except Exception:
                candidate_url = ""
            if not url_matches_product(candidate_url, shop_id, item_id):
                continue
            self._page = candidate
            self._page_prepared = False
            return candidate, True

        connect_timeout = max(5, min(int(self.timeout or DEFAULT_TIMEOUT), 15))
        page = await asyncio.wait_for(self._browser.newPage(), timeout=connect_timeout)
        await self._prepare_page(page)
        self._page = page
        return page, False

    async def _prepare_page(self, page: Any) -> None:
        nav_timeout_ms = max(5000, int(self.timeout or DEFAULT_TIMEOUT) * 1000)
        page.setDefaultNavigationTimeout(nav_timeout_ms)
        set_default_timeout = getattr(page, "setDefaultTimeout", None)
        if set_default_timeout:
            set_default_timeout(nav_timeout_ms)
        try:
            await page.setCacheEnabled(True)
        except Exception:
            pass
        try:
            await page.setViewport({"width": 1200, "height": 800})
        except Exception:
            pass

        async def handle_request(request: Any) -> None:
            try:
                resource_type = getattr(request, "resourceType", "") or ""
                url = getattr(request, "url", "") or ""
                if resource_type in BLOCKED_RESOURCE_TYPES or any(
                    marker in url for marker in BLOCKED_URL_PARTS
                ):
                    await request.abort()
                else:
                    await request.continue_()
            except Exception:
                pass

        try:
            await page.setRequestInterception(True)
            page.on("request", lambda request: asyncio.ensure_future(handle_request(request)))
        except Exception:
            pass
        self._page_prepared = True

    async def _goto_for_extract(self, page: Any, url: str) -> bool:
        nav_timeout_ms = max(5000, int(self.timeout or DEFAULT_TIMEOUT) * 1000)
        try:
            await page.goto(url, waitUntil="domcontentloaded", timeout=nav_timeout_ms)
            return True
        except Exception:
            try:
                await page._client.send("Page.stopLoading")
            except Exception:
                pass
            return False

    async def _fetch_ssr(
        self, shop_id: int, item_id: int, referer: str, *, full_page: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        page, reused_existing_page = await self._ensure_product_page(shop_id, item_id)
        product = referer or product_url(shop_id, item_id)
        navigation_ok = True
        if reused_existing_page:
            try:
                await page.bringToFront()
            except Exception:
                pass
        else:
            navigation_ok = await self._goto_for_extract(page, product)
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
        price_meta: dict[str, Any] | None = None
        price_source = "ssr_cachedMap"
        dom_price_applied = False
        biggo_url: str | None = None
        biggo_status: int | None = None
        biggo_error: str | None = None

        def extract_store(raw_value: dict[str, Any]) -> dict[str, Any]:
            if raw_value.get("cachedMap"):
                raw_store = raw_value["cachedMap"].get(f"{shop_id}/{item_id}", {})
                if isinstance(raw_store, dict):
                    return raw_store
            if raw_value.get("rawText"):
                try:
                    parsed = json.loads(raw_value["rawText"])
                except json.JSONDecodeError:
                    return {}
                if isinstance(parsed, dict):
                    raw_store = parsed.get(f"{shop_id}/{item_id}", {})
                    if isinstance(raw_store, dict):
                        return raw_store
            return {}

        store = extract_store(raw)

        if not store and reused_existing_page and dom_price_ready(dom_price):
            fallback_page = await self._browser.newPage()
            await self._prepare_page(fallback_page)
            navigation_ok = await self._goto_for_extract(fallback_page, product)
            fallback_raw = await fallback_page.evaluate(SSR_EXTRACT_JS)
            fallback_store = extract_store(fallback_raw)
            if fallback_store:
                raw = fallback_raw
                source = raw.get("source", "none")
                store = fallback_store
                self._page = fallback_page
                self._page_prepared = True

        store_has_price = bool(summarize(store).get("has_price")) if store else False
        if store and not store_has_price:
            item = store.get("item")
            title = str(item.get("title") or "") if isinstance(item, dict) else ""
            biggo_price, biggo_url, biggo_status, biggo_error = await asyncio.to_thread(
                fetch_biggo_price,
                title,
                shop_id,
                item_id,
            )
            if dom_price_ready(biggo_price):
                price_meta = apply_dom_price_fallback(store, biggo_price)
                dom_price_applied = price_meta is not None
                if dom_price_applied:
                    price_source = str(price_meta.get("source") or "biggo_result_text")
        if store and not store_has_price and not dom_price_applied and dom_price_ready(dom_price):
            price_meta = apply_dom_price_fallback(store, dom_price)
            dom_price_applied = price_meta is not None
            if dom_price_applied:
                price_source = str(price_meta.get("source") or "dom_text")
        if store and not dom_price_applied:
            normalize_price_fields(store)

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

        summary = summarize(store, price_source=price_source, price_meta=price_meta) if store else {"has_real_data": False}
        attempt = {
            "endpoint": "ssr_cachedMap",
            "http_status": 200 if store else 500,
            "shopee_error": body.get("error"),
            "shopee_error_msg": body.get("error_msg"),
            "has_real_data": bool(summary.get("has_real_data")),
            "url": product,
            "source": source,
            "navigation_ok": navigation_ok,
            "reused_existing_page": reused_existing_page,
            "dom_price_text": price_meta.get("price_text") if price_source == "dom_text" and price_meta else None,
            "dom_price_applied": dom_price_applied,
            "biggo_url": biggo_url,
            "biggo_http_status": biggo_status,
            "biggo_error": biggo_error,
            "biggo_price_text": price_meta.get("price_text") if price_source == "biggo_result_text" and price_meta else None,
        }

        selected = {
            "body": body,
            "http_status": attempt["http_status"],
            "endpoint": attempt["endpoint"],
            "url": attempt["url"],
            "summary": summary,
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
                self._page_prepared = False
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
