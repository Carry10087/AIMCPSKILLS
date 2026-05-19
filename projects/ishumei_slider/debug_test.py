# -*- coding: utf-8 -*-
# 详细调试 — 打印所有 fverify 请求参数
import sys, os, base64, json, random, re, time, logging
from io import BytesIO
import cv2, numpy as np, requests
from pyDes import des, ECB

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from captcha.api import fetch_conf, fetch_register, download_image
from captcha.slider import detect_slider_position
from captcha.crypto_utils import (
    des_decrypt, des_encrypt, generate_track_physics, build_fverify_params, debug_print_params
)

logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("debug")

# 从旧版 SDK 提取的参数映射
PARAM_NAMES = {
    "k1": "sq", "k2": "ko", "k3": "vj",
    "k5": "mn", "k6": "nj", "k7": "zl",
    "k8": "yv", "k9": "ch",
    "k11": "ga", "k12": "kh", "k13": "oe",
}

logger.info("Step 1: Conf")
conf = fetch_conf()
js_url = conf['detail']['js']
protocol = re.search(r'build/v1.0.3-(.*?)/captcha-sdk', js_url).group(1)
logger.info(f"  protocol={protocol}, js={js_url}")

logger.info("Step 2: Register")
session = fetch_register()
logger.info(f"  rid={session.rid}, k={session.k}, l={session.l}")

logger.info("Step 3: Download images")
session.bg_path = download_image(session.bg_url, "bg_debug.jpg")
session.fg_path = download_image(session.fg_url, "fg_debug.png")
logger.info(f"  bg={session.bg_path}, fg={session.fg_path}")

logger.info("Step 4: Detect slider position")
slider_x = detect_slider_position(session.bg_path, session.fg_path, method="gray")
logger.info(f"  offset={slider_x}px (orig), display={int(slider_x/600*310)}px")

logger.info("Step 5: Build & debug fverify params")
params = build_fverify_params(session.rid, session.k, slider_x, PARAM_NAMES, protocol)
real_key = des_decrypt(session.k, "sshummei")
debug_print_params(params, PARAM_NAMES, real_key)

logger.info("Step 6: Submit fverify...")
# 手动发送，不加额外参数覆盖
params["callback"] = f"sm_{int(time.time()*1000)}"
params.setdefault("organization", "RlokQwRlVjUrTUlkIqOg")
resp = requests.get("https://captcha.fengkongcloud.com/ca/v2/fverify", params=params, timeout=15, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})
# 清理 JSONP
text = resp.text
s = text.find("(")
e = text.rfind(")")
data = json.loads(text[s+1:e]) if s >= 0 and e > s else {}
logger.info(f"  Response: code={data.get('code')}, msg={data.get('message')}, risk={data.get('riskLevel')}")

# 也打印第一个参数的原始值（用于对比）
logger.info("\n--- Raw param values (before encryption) ---")
display_distance = int(slider_x / 600 * 310)
ge = generate_track_physics(display_distance)
logger.info(f"  k5 (mn) = {round(display_distance/310, 2)}")
logger.info(f"  k6 (nj) = trajectory with {len(ge)} points")
logger.info(f"  k7 (zl) = {ge[-1][-1] + random.randint(0, 100)}")
logger.info(f"  k8 (yv) = 310")
logger.info(f"  k9 (ch) = 155")
logger.info(f"  k11 (ga) = 1")
logger.info(f"  k12 (kh) = 0")
logger.info(f"  k13 (oe) = -1")
logger.info(f"  Track first 3 points: {ge[:3]}")
logger.info(f"  Track last 3 points: {ge[-3:]}")
