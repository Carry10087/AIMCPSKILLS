"""Minimal gap detection script for jsdom integration.
Usage: python gap_detect.py <bg_url> <fg_url> [display_width=260]
Output: "gap_display_px" or "gap_display_px confidence" or "ERROR:..."
"""
import sys
import cv2
import numpy as np
import requests

def detect_gap(bg_url, fg_url, display_width=260):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.ishumei.com/trial/captcha.html'
    }

    r = requests.get(bg_url, headers=headers, timeout=15)
    bg_arr = np.frombuffer(r.content, np.uint8)
    bg = cv2.imdecode(bg_arr, cv2.IMREAD_GRAYSCALE)

    r = requests.get(fg_url, headers=headers, timeout=15)
    fg_arr = np.frombuffer(r.content, np.uint8)
    fg = cv2.imdecode(fg_arr, cv2.IMREAD_GRAYSCALE)

    if bg is None or fg is None:
        raise Exception("Failed to decode images")

    bg_h, bg_w = bg.shape

    # === Method 1: Direct grayscale template matching ===
    methods = [
        (cv2.TM_CCOEFF_NORMED, "ccoeff"),
        (cv2.TM_CCORR_NORMED, "ccorr"),
    ]

    best_gap_img_px = 0
    best_conf = 0
    best_method = ""

    for method, name in methods:
        result = cv2.matchTemplate(bg, fg, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_conf:
            best_conf = max_val
            best_gap_img_px = max_loc[0]
            best_method = name

    # === Method 2a: Canny edge matching with multiple thresholds ===
    canny_methods = [
        (cv2.TM_CCOEFF_NORMED, "canny_ccoeff"),
        (cv2.TM_CCORR_NORMED, "canny_ccorr"),
    ]
    for low_t, high_t in [(30, 100), (40, 120), (50, 150)]:
        bg_edges = cv2.Canny(cv2.GaussianBlur(bg, (3, 3), 0), low_t, high_t)
        fg_edges = cv2.Canny(cv2.GaussianBlur(fg, (3, 3), 0), low_t, high_t)

        for method, name in canny_methods:
            result = cv2.matchTemplate(bg_edges, fg_edges, method)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_conf:
                best_conf = max_val
                best_gap_img_px = max_loc[0]
                best_method = name + "_t" + str(low_t)

    # === Method 2b: CLAHE enhanced + Canny ===
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    bg_clahe = clahe.apply(bg)
    fg_clahe = clahe.apply(fg)
    bg_edges_c = cv2.Canny(bg_clahe, 40, 120)
    fg_edges_c = cv2.Canny(fg_clahe, 40, 120)
    for method, name in canny_methods:
        result = cv2.matchTemplate(bg_edges_c, fg_edges_c, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_conf:
            best_conf = max_val
            best_gap_img_px = max_loc[0]
            best_method = "clahe_" + name

    gap_display_px = int(best_gap_img_px / bg_w * display_width)

    return gap_display_px, best_conf, best_method, bg_w

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python gap_detect.py <bg_url> <fg_url> [display_width]")
        sys.exit(1)

    bg_url = sys.argv[1]
    fg_url = sys.argv[2]
    display_w = int(sys.argv[3]) if len(sys.argv) > 3 else 260

    try:
        gap, conf, method, bg_w = detect_gap(bg_url, fg_url, display_w)
        print(str(gap))
        # Confidence info goes to stderr for jsdom diagnostics
        print("CONF:" + str(round(conf, 4)) + " method:" + method + " bg_w:" + str(bg_w), file=sys.stderr)
        if conf < 0.3:
            print("LOW_CONF", file=sys.stderr)
    except Exception as e:
        print("ERROR:" + str(e), file=sys.stderr)
        sys.exit(1)
