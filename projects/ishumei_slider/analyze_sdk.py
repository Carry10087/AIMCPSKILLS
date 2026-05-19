# -*- coding: utf-8 -*-
# 分析 v206 SDK 格式 — 尝试提取 2 字符参数名
import sys, os, re, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import HEADERS

# 新版 SDK (webpack 格式)
NEW_SDK = "https://castatic.fengkongcloud.com/pr/auto-build/v1.0.3-206/captcha-sdk.min.js"

resp = requests.get(NEW_SDK, headers=HEADERS, timeout=30)
script = resp.text
print(f"SDK size: {len(script)} chars")

# 策略1：直接搜索所有 "xx":"yy" 或 'xx'='yy' 模式中 2 字符的 key
# 在 fverify 相关代码附近
all_2char = set()
for m in re.finditer(r"""[^a-z]([a-z]{2})["'][\s]*:""", script):
    all_2char.add(m.group(1))
print(f"\nAll 2-char keys in object literals ({len(all_2char)}):")
print(sorted(all_2char))

# 策略2：搜索传递给 DES encrypt 的变量名
# 在 JS 中，这些变量通常以数组形式出现
# 搜索形如 ["aa","bb","cc"...] 的数组，其中元素都是 2 字符
for m in re.finditer(r"""\[(["'][a-z]{2}["']\s*,\s*){5,}""", script):
    start = m.start()
    # 抓取完整数组
    depth = 0
    end = start
    for j, c in enumerate(script[start:]):
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                end = start + j + 1
                break
    arr_text = script[start:end]
    # 提取所有 2 字符字符串
    items = re.findall(r"""["']([a-z]{2})["']""", arr_text)
    if len(items) >= 5:
        print(f"\n2-char param array found ({len(items)} items):")
        seen = []
        for item in items:
            if item not in seen:
                seen.append(item)
        print(f"  [{', '.join(repr(x) for x in seen)}]")

# 策略3：搜索包含已知参数名的区域
known_params = ['sq', 'ko', 'vj', 'mn', 'nj', 'zl', 'yv', 'ch', 'ga', 'kh', 'oe']
for p in known_params:
    matches = [(m.start(), m.group()) for m in re.finditer(rf'["\']({p})["\']', script)]
    if matches:
        print(f"\n'{p}' found at positions: {[m[0] for m in matches[:5]]}")

# 策略4：精确搜索 DES encrypt 调用附近的参数名
# 在 v206 SDK 中，搜索所有 function 调用后紧跟 base64 编码的 2-char 字符串
print("\n--- Searching in DES encrypt context ---")
# 先找所有 2 字符的 base64 编码字符串（可能是加密后的参数名）
for m in re.finditer(r'["\']([A-Za-z0-9+/]{4,8}=?)["\']', script):
    s = m.group(1)
    if len(s) in [4, 8] and s[-1] == '=' and len(s) == 4:
        # 可能是 base64 编码的 2 字符字符串
        import base64
        try:
            decoded = base64.b64decode(s).decode('latin-1')
            if len(decoded) == 2:
                pass  # not what we need
        except:
            pass
