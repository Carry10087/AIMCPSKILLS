# ============================================================
# 数美滑块验证码 - API 请求模块
# 
# 封装与 captcha.fengkongcloud.com 的交互：
#   1. /ca/v1/conf     - 获取验证码配置
#   2. /ca/v1/register - 注册验证码实例，获取滑块图片和 rid
#   3. /ca/v2/fverify  - 提交验证结果
# ============================================================

import time
import json
import random
import requests
from pathlib import Path
from dataclasses import dataclass, field

from config import (
    CONF_URL,
    REGISTER_URL,
    FVERIFY_URL,
    ORGANIZATION,
    APP_ID,
    SDK_VER,
    RVERSION,
    MODEL,
    LANG,
    CHANNEL,
    OSTYPE,
    ACT_OS,
    HEADERS,
    IMAGE_DIR,
)


# ---------- 数据类：存储一次验证会话的完整信息 ----------

@dataclass
class CaptchaSession:
    """一次滑块验证的完整会话状态"""
    # 注册时分配的唯一 ID（fverify 必须携带）
    rid: str = ""
    # 背景图 URL
    bg_url: str = ""
    # 滑块图 URL
    fg_url: str = ""
    # 背景图原图尺寸
    bg_width: int = 600
    bg_height: int = 300
    # 验证码注册时返回的密钥 k（base64 编码，DES 加密需要）
    k: str = ""
    # l 参数（可能是缺口索引或难度等级）
    l: int = 0
    # 本地保存的背景图路径
    bg_path: str = ""
    # 本地保存的滑块图路径
    fg_path: str = ""
    # 计算出的滑块水平偏移（像素）
    slider_x: int = 0
    # 模拟的滑动轨迹
    track_data: list = field(default_factory=list)
    # 验证耗时
    elapsed_ms: int = 0
    # 验证结果
    verify_result: dict = field(default_factory=dict)


# ---------- API 请求函数 ----------

def _generate_callback() -> str:
    """生成 sm_ 前缀的 JSONP callback 名称"""
    return f"sm_{int(time.time() * 1000)}"


def _clean_jsonp(text: str) -> dict:
    """去除 JSONP 包装 (sm_xxx({...})) 返回纯 JSON dict"""
    # 找到第一个 ( 和最后一个 ) 之间的内容
    start = text.find("(")
    end = text.rfind(")")
    if start >= 0 and end > start:
        inner = text[start + 1:end]
    else:
        inner = text
    return json.loads(inner)


def fetch_conf() -> dict:
    """
    【步骤1】获取验证码配置。
    返回 JS SDK URL、CSS URL、domains 列表等。
    """
    params = {
        "organization": ORGANIZATION,
        "model": MODEL,
        "sdkver": SDK_VER,
        "rversion": RVERSION,
        "appId": APP_ID,
        "lang": LANG,
        "channel": CHANNEL,
        "callback": _generate_callback(),
    }
    resp = requests.get(CONF_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = _clean_jsonp(resp.text)
    return data


def fetch_register() -> CaptchaSession:
    """
    【步骤2】注册验证码实例。
    获取背景图、滑块图、rid、密钥 k 等信息。
    返回填充好的 CaptchaSession 对象。
    """
    params = {
        "organization": ORGANIZATION,
        "model": MODEL,
        "sdkver": SDK_VER,
        "rversion": RVERSION,
        "appId": APP_ID,
        "lang": LANG,
        "channel": CHANNEL,
        "callback": _generate_callback(),
    }
    resp = requests.get(REGISTER_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = _clean_jsonp(resp.text)

    detail = data.get("detail", data)

    session = CaptchaSession()
    session.rid = detail.get("rid", "")
    session.bg_url = detail.get("bg", "")
    session.fg_url = detail.get("fg", "")
    session.bg_width = detail.get("bg_width", 600)
    session.bg_height = detail.get("bg_height", 300)
    session.k = detail.get("k", "")
    session.l = detail.get("l", 0)

    return session


def download_image(url: str, filename: str) -> str:
    """
    下载验证码图片到本地 images/ 目录。
    返回本地文件路径。
    """
    # 拼接完整 URL
    if url.startswith("/"):
        full_url = f"https://castatic.fengkongcloud.com{url}"
    else:
        full_url = url

    # 确保目录存在
    image_dir = Path(IMAGE_DIR)
    image_dir.mkdir(parents=True, exist_ok=True)

    filepath = image_dir / filename
    resp = requests.get(full_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(resp.content)

    return str(filepath)


def fetch_verify(session: CaptchaSession, params: dict) -> dict:
    """
    【步骤4】提交验证结果。
    params 是 build_fverify_params() 构造的加密参数字典。
    返回服务端验证结果。
    """
    # 使用 JSONP callback 格式
    params["callback"] = _generate_callback()
    # 只在参数中不存在时设置默认值（避免覆盖 build_fverify_params 的值）
    params.setdefault("organization", ORGANIZATION)
    params.setdefault("rversion", RVERSION)
    params.setdefault("sdkver", SDK_VER)
    params.setdefault("ostype", OSTYPE)
    params.setdefault("act.os", "web_pc")

    resp = requests.get(FVERIFY_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = _clean_jsonp(resp.text)
    return data
