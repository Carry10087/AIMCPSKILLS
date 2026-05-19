# -*- coding: utf-8 -*-
# 直接使用博客的完整代码测试数美滑块验证
# 来源: http://www.lxspider.com/?p=86
# 已适配当前 API 端点

import base64, json, random, re, time
from io import BytesIO
import cv2
import numpy as np
import requests
from pyDes import des, ECB

CAPTCHA_DISPLAY_WIDTH = 310
CAPTCHA_DISPLAY_HEIGHT = 155

p = {}

def pad(b):
    block_size = 8
    while len(b) % block_size:
        b += b'\0'
    return b

def split_args(s):
    r = []
    a = ''
    i = 0
    while i < len(s):
        c = s[i]
        if c == ',' and (a[0] != "'" or len(a) >= 2 and a[-1] == "'"):
            r.append(a)
            a = ''
        elif c:
            a += c
        i += 1
    r.append(a)
    return r

def find_arg_names(script):
    names = {}
    a = []
    for r_match in re.findall(r'function\((.*?)\)', script):
        if len(r_match.split(',')) > 100:
            a = split_args(r_match)
            break
    if not a:
        raise ValueError("Cannot find function with >100 args")

    r = re.search(r';\)(.*?)\(}', script[::-1])
    if not r:
        raise ValueError("Cannot find value list pattern")
    v = split_args(r.group(1)[::-1])

    d = r'{%s}' % ''.join([((',' if i else '') + "'k{}':([_x0-9a-z]*)".format(i + 1)) for i in range(15)])
    k = []
    r2 = re.search(d, script)
    if not r2:
        raise ValueError("Cannot find k1-k15 mapping")
    for i in range(15):
        k.append(r2.group(i + 1))

    arg_match = re.search(r'arguments;.*?,(.*?)\);', script)
    if not arg_match:
        raise ValueError("Cannot find arguments pattern")
    n = int(v[a.index(arg_match.group(1))], base=16)

    for i in range(n // 2):
        v[i], v[n - 1 - i] = v[n - 1 - i], v[i]

    for i, b in enumerate(k):
        t = v[a.index(b)].strip("'")
        names['k{}'.format(i + 1)] = t if len(t) > 2 else t[::-1]

    return names

def get_encrypt_content(message, key, flag):
    des_obj = des(key.encode(), mode=ECB)
    if flag:
        content = pad(str(message).replace(' ', '').encode())
        return base64.b64encode(des_obj.encrypt(content)).decode('utf-8')
    else:
        return des_obj.decrypt(base64.b64decode(message)).decode('utf-8')

def get_random_ge(distance):
    ge = []
    y = 0
    v = 0
    t = 1
    current = 0
    mid = distance * 3 / 4
    exceed = 20
    z = t
    ge.append([0, 0, 1])
    while current < (distance + exceed):
        if current < mid / 2:
            a = 15
        elif current < mid:
            a = 20
        else:
            a = -30
        a /= 2
        v0 = v
        s = v0 * t + 0.5 * a * (t * t)
        current += int(s)
        v = v0 + a * t
        y += random.randint(-5, 5)
        z += 100 + random.randint(0, 10)
        ge.append([min(current, (distance + exceed)), y, z])
    while exceed > 0:
        exceed -= random.randint(0, 5)
        y += random.randint(-5, 5)
        z += 100 + random.randint(0, 10)
        ge.append([min(current, (distance + exceed)), y, z])
    return ge

def make_mouse_action_args(distance):
    ge = get_random_ge(distance)
    args = {
        p['k']['k5']: round(distance / CAPTCHA_DISPLAY_WIDTH, 2),
        p['k']['k6']: get_random_ge(distance),
        p['k']['k7']: ge[-1][-1] + random.randint(0, 100),
        p['k']['k8']: CAPTCHA_DISPLAY_WIDTH,
        p['k']['k9']: CAPTCHA_DISPLAY_HEIGHT,
        p['k']['k11']: 1,
        p['k']['k12']: 0,
        p['k']['k13']: -1,
        'act.os': 'android'
    }
    return args

def get_distance(fg, bg):
    # 修正：bg 是背景（大图），fg 是滑块（小图），用 bg 作为搜索目标
    template = cv2.imdecode(np.asarray(bytearray(fg.read()), dtype=np.uint8), 0)
    target = cv2.imdecode(np.asarray(bytearray(bg.read()), dtype=np.uint8), 0)
    result = cv2.matchTemplate(target, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return max_loc[0]  # x coordinate

def update_protocol(protocol_num, js_uri):
    global p
    r = requests.get(js_uri, verify=False)
    names = find_arg_names(r.text)
    p = {'i': protocol_num, 'k': names}
    print(f"  Extracted param names: {names}")

def conf_captcha(organization):
    url = 'https://captcha.fengkongcloud.com/ca/v1/conf'
    args = {
        'organization': organization,
        'model': 'slide',
        'sdkver': '1.1.3',
        'rversion': '1.0.3',
        'appId': 'default',
        'lang': 'zh-cn',
        'channel': 'YingYongBao',
        'callback': 'sm_{}'.format(int(time.time() * 1000))
    }
    r = requests.get(url, params=args, verify=False)
    resp = json.loads(re.search(r'{}\((.*)\)'.format(args['callback']), r.text).group(1))
    return resp

def register_captcha(organization):
    url = 'https://captcha.fengkongcloud.com/ca/v1/register'
    args = {
        'organization': organization,
        'channel': 'YingYongBao',
        'lang': 'zh-cn',
        'model': 'slide',
        'appId': 'default',
        'sdkver': '1.1.3',
        'data': '{}',
        'rversion': '1.0.3',
        'callback': 'sm_{}'.format(int(time.time() * 1000))
    }
    r = requests.get(url, params=args, verify=False)
    resp = json.loads(re.search(r'{}\((.*)\)'.format(args['callback']), r.text).group(1))
    return resp

def verify_captcha(organization, rid, key, distance):
    url = 'https://captcha.fengkongcloud.com/ca/v2/fverify'
    args = {
        'organization': organization,
        p['k']['k1']: 'default',
        p['k']['k2']: 'YingYongBao',
        p['k']['k3']: 'zh-cn',
        'rid': rid,
        'rversion': '1.0.3',
        'sdkver': '1.1.3',
        'protocol': p['i'],
        'ostype': 'web',
        'callback': 'sm_{}'.format(int(time.time() * 1000))
    }
    args.update(make_mouse_action_args(distance))

    key = get_encrypt_content(key, 'sshummei', 0)
    print(f"  Decrypted DES key: '{key}'")

    for k, v in args.items():
        if len(k) == 2:
            args[k] = get_encrypt_content(v, key, 1)

    print(f"  Final args ({len(args)} items):")
    for k, v in args.items():
        val = str(v)
        if len(val) > 60:
            val = val[:60] + '...'
        print(f"    {k}: {val}")

    r = requests.get(url, params=args, verify=False)
    resp = json.loads(re.search(r'{}\((.*)\)'.format(args['callback']), r.text).group(1))
    return resp

def get_verify(organization):
    resp = conf_captcha(organization)
    protocol_num = re.search(r'build/v1.0.3-(.*?)/captcha-sdk.min.js', resp['detail']['js']).group(1)
    print(f"Protocol: {protocol_num}")

    if not p.get('id') or protocol_num != p['i']:
        js_full_url = ''.join(['https://', resp['detail']['domains'][0], resp['detail']['js']])
        print(f"Downloading JS SDK: {js_full_url}")
        update_protocol(protocol_num, js_full_url)

    resp = register_captcha(organization)
    rid = resp['detail']['rid']
    key = resp['detail']['k']

    domain = resp['detail']['domains'][0]
    fg_uri = resp['detail']['fg']
    bg_uri = resp['detail']['bg']

    fg_url = ''.join(['https://', domain, fg_uri])
    bg_url = ''.join(['https://', domain, bg_uri])

    print(f"Downloading images...")
    r = requests.get(fg_url, verify=False)
    fg = BytesIO(r.content)
    r = requests.get(bg_url, verify=False)
    bg = BytesIO(r.content)

    distance = get_distance(fg, bg)
    print(f"Image distance: {distance}px, scaled: {int(distance / 600 * 310)}px")

    r = verify_captcha(organization, rid, key, int(distance / 600 * 310))
    return rid, r

def test():
    organization = 'RlokQwRlVjUrTUlkIqOg'
    print("=" * 60)
    print("Starting ishumei captcha test...")
    print("=" * 60)
    rid, r = get_verify(organization)
    print(f"\nrid: {rid}")
    print(f"Result: code={r.get('code')}, riskLevel={r.get('riskLevel')}, msg={r.get('message')}")
    if r.get('riskLevel') == 'PASS':
        print("SUCCESS!")
    else:
        print("FAILED (REJECT)")

if __name__ == '__main__':
    test()
