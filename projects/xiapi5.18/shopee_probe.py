#!/usr/bin/env python3
"""Small Shopee Taiwan product probe.

This is a single-product verifier for the task flow in Rules.txt. It fetches
public product detail data and saves the raw get_pc response that can be used
as taskResult later.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_HOST = "https://shopee.tw"
GET_PC_ENDPOINT = f"{BASE_HOST}/api/v4/pdp/get_pc"
DEFAULT_TIMEOUT = 20
ENDPOINTS = ("get_pc", "get_rw", "item_get")


class ProbeError(RuntimeError):
    pass


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def parse_product_ids(value: str) -> tuple[int, int]:
    """Return (shop_id, item_id) from common Shopee URL formats."""
    text = urllib.parse.unquote(value.strip())

    patterns = [
        r"(?:^|[^\w])i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)(?:[^\d]|$)",
        r"/product/(?P<shop_id>\d+)/(?P<item_id>\d+)(?:[^\d]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group("shop_id")), int(match.group("item_id"))

    parsed = urllib.parse.urlparse(text)
    params = urllib.parse.parse_qs(parsed.query)
    if "shop_id" in params and "item_id" in params:
        return int(params["shop_id"][0]), int(params["item_id"][0])

    raise ProbeError(
        "Cannot find shop_id/item_id. Use a URL like "
        "https://shopee.tw/product/{shop_id}/{item_id} or pass --shop-id/--item-id."
    )


def public_product_url(shop_id: int, item_id: int) -> str:
    return f"{BASE_HOST}/product/{shop_id}/{item_id}"


def shopee_price(value: Any) -> str | None:
    if value is None or value == -1:
        return None
    try:
        return f"{int(value) / 100000:.2f}"
    except (TypeError, ValueError):
        return None


def compact_text(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


class ShopeeProbe:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.cookiejar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )

    def request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        allow_http_error: bool = False,
    ) -> tuple[int, bytes]:
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Connection": "close",
        }
        if headers:
            request_headers.update(headers)

        request = urllib.request.Request(url, headers=request_headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            error_bytes = exc.read()
            body = error_bytes.decode("utf-8", errors="replace")[:500]
            if allow_http_error:
                return exc.code, error_bytes
            raise ProbeError(f"HTTP {exc.code} for {url}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProbeError(f"Network error for {url}: {exc.reason}") from exc

    def warmup(self, referer: str) -> None:
        """Visit public pages once so Shopee can set ordinary visitor cookies."""
        for url in (BASE_HOST, referer):
            try:
                self.request(url, {"Accept": "text/html,application/xhtml+xml"})
                time.sleep(0.8)
            except ProbeError:
                pass

    def fetch_endpoint(
        self, endpoint: str, shop_id: int, item_id: int, referer: str
    ) -> dict[str, Any]:
        if endpoint == "get_pc":
            path = "/api/v4/pdp/get_pc"
            params = {"shop_id": shop_id, "item_id": item_id}
        elif endpoint == "get_rw":
            path = "/api/v4/pdp/get_rw"
            params = {"shop_id": shop_id, "item_id": item_id}
        elif endpoint == "item_get":
            path = "/api/v4/item/get"
            params = {"shopid": shop_id, "itemid": item_id}
        else:
            raise ProbeError(f"Unknown endpoint: {endpoint}")

        query = urllib.parse.urlencode(params)
        url = f"{BASE_HOST}{path}?{query}"
        status_code, raw = self.request(
            url,
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": referer,
                "X-API-SOURCE": "pc",
                "X-Requested-With": "XMLHttpRequest",
            },
            allow_http_error=True,
        )
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{endpoint} did not return JSON: {text[:500]}") from exc

        return {
            "endpoint": endpoint,
            "url": url,
            "http_status": status_code,
            "body": body,
            "raw_text": text,
        }

    def fetch_first_usable(
        self, endpoints: list[str], shop_id: int, item_id: int, referer: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        attempts = []
        selected = None

        for endpoint in endpoints:
            fetched = self.fetch_endpoint(endpoint, shop_id, item_id, referer)
            summary = summarize_get_pc(fetched["body"])
            attempt = {
                "endpoint": endpoint,
                "http_status": fetched["http_status"],
                "shopee_error": fetched["body"].get("error"),
                "shopee_error_msg": fetched["body"].get("error_msg"),
                "has_real_data": summary["has_real_data"],
                "url": fetched["url"],
            }
            attempts.append(attempt)
            if selected is None:
                selected = fetched
            if summary["has_real_data"] and fetched["http_status"] < 400:
                return fetched, attempts
            time.sleep(0.8)

        assert selected is not None
        return selected, attempts


def summarize_get_pc(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data") or {}
    item = data.get("item") or {}
    product_price = data.get("product_price") or {}
    price_box = product_price.get("price") or {}
    review = data.get("product_review") or {}
    images = (data.get("product_images") or {}).get("images") or []
    shop = data.get("shop_detailed") or {}
    models = item.get("models") or []

    return {
        "has_real_data": bool(item),
        "shopee_error": body.get("error"),
        "shopee_error_msg": body.get("error_msg"),
        "item_id": item.get("item_id"),
        "shop_id": item.get("shop_id"),
        "title": item.get("title"),
        "currency": item.get("currency"),
        "brand": item.get("brand"),
        "price_twd": shopee_price(item.get("price")),
        "price_min_twd": shopee_price(item.get("price_min") or price_box.get("range_min")),
        "price_max_twd": shopee_price(item.get("price_max") or price_box.get("range_max")),
        "stock": item.get("stock"),
        "normal_stock": item.get("normal_stock"),
        "models": len(models),
        "images": len(images),
        "rating_star": review.get("rating_star"),
        "total_rating_count": review.get("total_rating_count"),
        "liked_count": review.get("liked_count"),
        "shop_name": shop.get("name"),
        "shop_location": shop.get("shop_location") or item.get("shop_location"),
        "description_preview": compact_text(item.get("description")),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe one public Shopee Taiwan product detail response."
    )
    parser.add_argument("url", nargs="?", help="Shopee product URL.")
    parser.add_argument("--shop-id", type=int, help="Shopee shop_id.")
    parser.add_argument("--item-id", type=int, help="Shopee item_id.")
    parser.add_argument(
        "--out",
        default="out/last_get_pc.json",
        help="Where to save the raw get_pc JSON response.",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--endpoint",
        choices=[*ENDPOINTS, "all"],
        default="all",
        help="Which public endpoint to try.",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip initial public page visits.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = build_parser().parse_args(argv)

    try:
        if args.shop_id and args.item_id:
            shop_id, item_id = args.shop_id, args.item_id
            referer = args.url or public_product_url(shop_id, item_id)
        elif args.url:
            shop_id, item_id = parse_product_ids(args.url)
            referer = args.url
        else:
            raise ProbeError("Pass a product URL or both --shop-id and --item-id.")

        probe = ShopeeProbe(timeout=args.timeout)
        if not args.no_warmup:
            probe.warmup(referer)

        endpoints = list(ENDPOINTS) if args.endpoint == "all" else [args.endpoint]
        fetched, attempts = probe.fetch_first_usable(endpoints, shop_id, item_id, referer)
        raw_body = fetched["body"]
        summary = summarize_get_pc(raw_body)
        out_path = Path(args.out).resolve()
        write_json(out_path, raw_body)

        result = {
            "ok": (
                bool(summary["has_real_data"])
                and fetched["http_status"] < 400
                and summary["shopee_error"] is None
            ),
            "source": fetched["endpoint"],
            "http_status": fetched["http_status"],
            "input": {
                "shop_id": shop_id,
                "item_id": item_id,
                "referer": referer,
            },
            "attempts": attempts,
            "summary": summary,
            "raw_response_path": str(out_path),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except ProbeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
