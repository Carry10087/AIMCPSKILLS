# ============================================================
# 数美滑块验证码 - JS 参数名提取 & DES 加密 & fverify 参数构造
#
# 核心逻辑（基于公开逆向分析）：
#   1. 从 JS SDK 中提取 k1~k15 映射到实际的 2 字符参数名
#   2. register 返回的 k 字段需要先用 'sshummei' 做 DES 解密
#   3. 解密后的 k 才是真正的 DES 密钥
#   4. fverify 中所有 2 字符的参数名都需要用此密钥 DES 加密
#   5. 参数值类型为 dict/list 时先做 json.dumps 再加密
# ============================================================

import base64
import json
import random
import re
import time
from typing import cast
import requests
from pyDes import des, ECB

from config import (
    PROTOCOL,
    CAPTCHA_DISPLAY_WIDTH,
    CAPTCHA_DISPLAY_HEIGHT,
    CAPTCHA_IMAGE_WIDTH,
    HEADERS,
)


# ================================================================
# 留痕点 A：JS SDK 参数名提取
# ================================================================

def split_args(s: str) -> list:
    """
    分割 JS 函数参数列表（处理嵌套字符串中的逗号）。
    """
    r = []
    a = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == "," and (a[0] != "'" or (len(a) >= 2 and a[-1] == "'")):
            r.append(a)
            a = ""
        elif c:
            a += c
        i += 1
    r.append(a)
    return r


def find_arg_names(script: str) -> dict:
    """
    从数美 JS SDK 中提取参数名映射。
    
    返回:
        { 'k1': 'xx', 'k2': 'yy', ... , 'k15': 'zz' }
        其中 k1-k15 是 JS 内部的索引名，
        'xx'/'yy' 是对应 fverify 请求中的实际参数名。
    """
    names = {}
    a = []
    
    # 步骤1：找到参数最多的函数（>100 个参数），获取参数名列表 a
    for r in re.findall(r"function\((.*?)\)", script):
        if len(r.split(",")) > 100:
            a = split_args(r)
            break

    # 步骤2：在反转的脚本中找到值列表 v
    r = re.search(r";\)(.*?)\(}", script[::-1])
    if not r:
        raise ValueError("无法在 JS SDK 中找到值列表（反转匹配失败）")
    v = split_args(r.group(1)[::-1])

    # 步骤3：用正则匹配 k1~k15 对应的变量名
    d = r"{%s}" % "".join(
        [
            ("," if i else "") + f"'k{i + 1}':([_x0-9a-z]*)"
            for i in range(15)
        ]
    )
    k = []
    r = re.search(d, script)
    if not r:
        raise ValueError("无法在 JS SDK 中找到 k1~k15 映射")
    for i in range(15):
        k.append(r.group(i + 1))

    # 步骤4：获取序列长度 n
    arg_match = re.search(r"arguments;.*?,(.*?)\);", script)
    if not arg_match:
        raise ValueError("无法找到 arguments 参数")
    n = int(v[a.index(arg_match.group(1))], base=16)

    # 步骤5：将 v 的 [0, n//2) 与 [n-1, n//2) 翻转
    for i in range(n // 2):
        v[i], v[n - 1 - i] = v[n - 1 - i], v[i]

    # 步骤6：构建映射
    for i, b in enumerate(k):
        t = v[a.index(b)].strip("'")
        # 长度 > 2 的保留原样，<=2 的可能是倒序（JS 中常见的混淆手法）
        names[f"k{i + 1}"] = t if len(t) > 2 else t[::-1]

    return names


def fetch_and_parse_js_sdk(js_url: str, domains: list) -> dict:
    """
    下载 JS SDK 并提取参数名映射。
    
    参数:
        js_url: conf 接口返回的 JS 路径
        domains: conf 接口返回的 CDN 域名列表
    
    返回:
        { 'k1': 'xx', 'k2': 'yy', ... }
    """
    # 构建完整 URL
    domain = domains[0] if domains else "castatic.fengkongcloud.com"
    full_url = f"https://{domain}{js_url}"
    
    resp = requests.get(full_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    
    names = find_arg_names(resp.text)
    return names


# ================================================================
# 留痕点 B：DES 加密 / 解密工具
# ================================================================

def pad_zeros(data: bytes, block_size: int = 8) -> bytes:
    """零字节填充（数美 JS SDK 使用零填充）"""
    remainder = len(data) % block_size
    if remainder == 0:
        return data
    return data + b"\x00" * (block_size - remainder)


def des_encrypt(plaintext: str, key: str) -> str:
    """
    DES-ECB 加密 -> base64。
    非字符串类型会自动 json.dumps 处理。
    """
    # 支持 dict/list 类型
    if not isinstance(plaintext, str):
        plaintext = json.dumps(plaintext, separators=(",", ":"), ensure_ascii=False)

    # 确保密钥 8 字节
    key_bytes = key.encode("utf-8")
    if len(key_bytes) > 8:
        key_bytes = key_bytes[:8]
    elif len(key_bytes) < 8:
        key_bytes = key_bytes.ljust(8, b"\x00")

    des_obj = des(key_bytes, mode=ECB)
    # 去除空格（JS SDK 行为）
    content = plaintext.replace(" ", "")
    padded = pad_zeros(content.encode("utf-8"))
    cipher = cast(bytes, des_obj.encrypt(padded))
    return base64.b64encode(cipher).decode("utf-8")



def des_decrypt(b64_cipher: str, key: str) -> str:
    """
    DES-ECB 解密 base64 密文 -> 明文。
    用于解密 register 返回的 k 字段。
    """
    key_bytes = key.encode("utf-8")
    if len(key_bytes) > 8:
        key_bytes = key_bytes[:8]
    elif len(key_bytes) < 8:
        key_bytes = key_bytes.ljust(8, b"\x00")

    des_obj = des(key_bytes, mode=ECB)
    cipher_bytes = base64.b64decode(b64_cipher)
    plain_bytes = des_obj.decrypt(cipher_bytes)
    # 去除零填充
    return plain_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")


# ================================================================
# 留痕点 C：轨迹生成（物理模型）
# ================================================================

def generate_track_physics(distance: int) -> list:
    """
    使用物理模型生成滑块轨迹（模拟加速->匀速->减速）。
    
    参数:
        distance: 滑块目标偏移量（原图像素）
    
    返回:
        轨迹列表 [[x, y, dt], ...]
        x: 累积水平位移
        y: 累积垂直位移
        dt: 当前点的时间戳（毫秒，从起始累加）
    """
    ge = []
    y = 0
    v = 0
    t = 1
    current = 0
    mid = distance * 3 / 4  # 3/4 处为分界点
    exceed = 20  # 超过目标再滑一点
    z = t

    # 起始点
    ge.append([0, 0, 1])

    while current < (distance + exceed):
        if current < mid / 2:
            a = 15  # 前半段加速
        elif current < mid:
            a = 20  # 中段加速
        else:
            a = -30  # 后段减速
        a /= 2
        v0 = v
        s = v0 * t + 0.5 * a * (t * t)
        current += int(s)
        v = v0 + a * t

        y += random.randint(-5, 5)
        z += 100 + random.randint(0, 10)

        ge.append([min(current, distance + exceed), y, z])

    # 超过目标后缓慢回退
    while exceed > 0:
        exceed -= random.randint(0, 5)
        y += random.randint(-5, 5)
        z += 100 + random.randint(0, 10)
        ge.append([min(current, distance + exceed), y, z])

    return ge


# ================================================================
# 留痕点 D：fverify 参数构造
# ================================================================

def build_fverify_params(
    rid: str,
    k: str,
    distance: int,
    param_names: dict,
    protocol: str = PROTOCOL,
) -> dict:
    """
    构造 fverify 请求参数。
    
    参数:
        rid:          register 返回的请求 ID
        k:            register 返回的密钥（base64，需先 DES 解密）
        distance:     滑块偏移量（原图像素，600px 宽）
        param_names:  find_arg_names() 提取的参数名映射
        protocol:     协议版本号
    
    返回:
        fverify 请求参数字典（2 字符参数已 DES 加密）
    """
    # ----- 步骤1：用 'sshummei' 解密 k，得到真正的 DES 密钥 -----
    real_key = des_decrypt(k, "sshummei")

    # ----- 步骤2：将原图距离缩放到显示距离（310px 宽）-----
    # JS SDK 中所有鼠标坐标均基于显示像素计算
    display_distance = int(distance / CAPTCHA_IMAGE_WIDTH * CAPTCHA_DISPLAY_WIDTH)

    # ----- 步骤3：生成轨迹（基于显示距离）-----
    ge = generate_track_physics(display_distance)

    # ----- 步骤3：构建参数（明文）-----
    # 注意：k5-k13 的参数值需要与 JS SDK 的输出格式一致
    # k6 (nj): 轨迹数据 -> DES 加密前先 json.dumps
    # k5 (mn): 滑动距离比例 -> 保留 2 位小数
    # k7 (zl): 轨迹总耗时（毫秒）
    args = {
        "organization": "RlokQwRlVjUrTUlkIqOg",
        param_names["k1"]: "default",
        param_names["k2"]: "YingYongBao",
        param_names["k3"]: "zh-cn",
        "rid": rid,
        "rversion": "1.0.3",
        "sdkver": "1.1.3",
        "protocol": protocol,
        "ostype": "web",
        # 鼠标行为参数（k5-k13，全部需要 DES 加密）
        param_names["k5"]: round(display_distance / CAPTCHA_DISPLAY_WIDTH, 2),
        param_names["k6"]: ge,  # 轨迹列表 -> json.dumps 后加密
        param_names["k7"]: ge[-1][-1] + random.randint(0, 100),
        param_names["k8"]: CAPTCHA_DISPLAY_WIDTH,
        param_names["k9"]: CAPTCHA_DISPLAY_HEIGHT,
        param_names["k11"]: 1,
        param_names["k12"]: 0,
        param_names["k13"]: -1,
        "act.os": "web_pc",
    }

    # ----- 步骤4：对所有 2 字符参数名做 DES 加密 -----
    encrypted_args = {}
    for key, value in args.items():
        if len(key) == 2:
            # 2 字符参数名 -> DES 加密值
            encrypted_args[key] = des_encrypt(value, real_key)
        else:
            encrypted_args[key] = value

    return encrypted_args


# ================================================================
# 留痕点 E：调试辅助
# ================================================================

def debug_print_params(params: dict, param_names: dict, real_key: str):
    """打印参数详情用于调试。"""
    print("\n" + "=" * 60)
    print("[fverify params detail]")
    print("=" * 60)
    print(f"  DES key (decrypted): {real_key}")
    print(f"  protocol : {params.get('protocol', 'N/A')}")
    print(f"  rid      : {params.get('rid', 'N/A')}")
    print(f"  param mapping :")
    for k, v in param_names.items():
        print(f"    {k} -> {v}")
    print("-" * 60)
    for key, value in params.items():
        val_str = str(value)
        if len(val_str) > 60:
            val_str = val_str[:60] + "..."
        print(f"  {key:12s}: {val_str}")
    print("=" * 60)
