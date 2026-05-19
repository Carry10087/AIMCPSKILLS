# -*- coding: utf-8 -*-
# 提取数美 JS SDK 参数名 - 无 emoji 版本
import sys, os, base64, re, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import HEADERS

OLD_SDK = "https://castatic.fengkongcloud.com/pr/auto-build/v1.0.3-70/captcha-sdk.min.js"

def decode_b64_arr(script):
    arr = re.search(r"var _0x[0-9a-f]+=\[(.*?)\];", script, re.DOTALL)
    if not arr:
        return []
    result = []
    for m in re.finditer(r"'([A-Za-z0-9+/=]+)'", arr.group(0)):
        try:
            result.append(base64.b64decode(m.group(1)).decode())
        except:
            pass
    return result

def split_args(s):
    r, a = [], ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == "," and (a[0] != "'" or (len(a) >= 2 and a[-1] == "'")):
            r.append(a); a = ""
        elif c: a += c
        i += 1
    r.append(a)
    return r

def find_names(script):
    # Method 1: use find_arg_names from blog post
    a = []
    for rf in re.findall(r"function\((.*?)\)", script):
        if len(rf.split(",")) > 100:
            a = split_args(rf)
            break
    if not a:
        return {}

    r = re.search(r";\)(.*?)\(}", script[::-1])
    if not r:
        return {}
    v = split_args(r.group(1)[::-1])

    d = r"{%s}" % "".join(
        [("," if i else "") + f"'k{i + 1}':([_x0-9a-z]*)" for i in range(15)]
    )
    k = []
    r2 = re.search(d, script)
    if not r2:
        return {}
    for i in range(15):
        k.append(r2.group(i + 1))

    arg_match = re.search(r"arguments;.*?,(.*?)\);", script)
    if not arg_match:
        return {}
    n = int(v[a.index(arg_match.group(1))], base=16)

    for i in range(n // 2):
        v[i], v[n - 1 - i] = v[n - 1 - i], v[i]

    names = {}
    for i, b in enumerate(k):
        t = v[a.index(b)].strip("'")
        names[f"k{i + 1}"] = t if len(t) > 2 else t[::-1]

    return names


if __name__ == "__main__":
    print("=" * 60)
    print("Downloading SDK:", OLD_SDK)
    resp = requests.get(OLD_SDK, headers=HEADERS, timeout=30)
    print(f"SDK size: {len(resp.text)} chars")

    print("\n--- Method 1: find_arg_names (blog) ---")
    names = find_names(resp.text)
    if names:
        for k, v in names.items():
            print(f"  {k} -> '{v}'")
        print("\n# Copyable dict:")
        print("PARAM_NAMES = {")
        for k, v in names.items():
            print(f'    "{k}": "{v}",')
        print("}")
    else:
        print("  FAILED - SDK format different from expected")

    print("\n--- Method 2: Decode all base64, find 2-char alpha strings ---")
    decoded = decode_b64_arr(resp.text)
    two_char = []
    for s in decoded:
        s_rev = s[::-1]  # reverse (JS uses reversed strings)
        if len(s_rev) == 2 and s_rev.isalpha() and s_rev.isascii():
            two_char.append(s_rev)
        elif len(s) == 2 and s.isalpha() and s.isascii():
            two_char.append(s)
    seen = set()
    unique = []
    for p in two_char:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    print(f"  Found {len(unique)} unique 2-char params: {unique[:20]}")
