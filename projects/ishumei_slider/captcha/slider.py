# ============================================================
# 数美滑块验证码 - 滑块位置识别模块
#
# 方法1（主）：Canny 边缘检测 + 模板匹配（对颜色变化鲁棒）
# 方法2（备）：直接灰度模板匹配 TM_CCOEFF_NORMED
# ============================================================

import cv2
import numpy as np

from config import (
    CANNY_LOW,
    CANNY_HIGH,
)


def detect_slider_position_canny(
    bg_path: str,
    fg_path: str,
    bg_width: int = 600,
) -> int:
    """
    方法1：基于 Canny 边缘检测的滑块定位。
    
    先对背景图和滑块图做边缘提取，再模板匹配。
    优点：不受颜色差异影响，只关注形状/纹理边界。
    
    返回：滑块缺口在原图上的 x 偏移量（像素）
    """
    bg = cv2.imread(bg_path)
    fg = cv2.imread(fg_path)

    if bg is None or fg is None:
        raise FileNotFoundError(f"无法读取图片: bg={bg_path}, fg={fg_path}")

    # 灰度化
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    fg_gray = cv2.cvtColor(fg, cv2.COLOR_BGR2GRAY)

    # Canny 边缘提取
    bg_edges = cv2.Canny(bg_gray, CANNY_LOW, CANNY_HIGH)
    fg_edges = cv2.Canny(fg_gray, CANNY_LOW, CANNY_HIGH)

    # 模板匹配
    result = cv2.matchTemplate(bg_edges, fg_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)

    # max_loc[0] 是匹配到的缺口左上角 x 坐标
    slider_x = max_loc[0]
    slider_x = max(0, min(slider_x, bg_width - fg.shape[1]))

    return slider_x


def detect_slider_position_gray(
    bg_path: str,
    fg_path: str,
    bg_width: int = 600,
) -> int:
    """
    方法2：直接灰度模板匹配。
    
    使用 TM_CCOEFF_NORMED 在背景图中搜索滑块位置。
    比 Canny 方法更快，但对颜色差异更敏感。
    
    返回：滑块缺口在原图上的 x 偏移量（像素）
    """
    bg = cv2.imread(bg_path, 0)  # 灰度读入
    fg = cv2.imread(fg_path, 0)

    if bg is None or fg is None:
        raise FileNotFoundError(f"无法读取图片: bg={bg_path}, fg={fg_path}")

    result = cv2.matchTemplate(bg, fg, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)

    slider_x = max_loc[0]
    slider_x = max(0, min(slider_x, bg_width - fg.shape[1]))

    return slider_x


def detect_slider_position(
    bg_path: str,
    fg_path: str,
    bg_width: int = 600,
    bg_height: int = 300,
    display_width: int = 310,
    display_height: int = 155,
    method: str = "canny",
) -> int:
    """
    统一入口：检测滑块缺口位置。

    参数:
        bg_path: 背景图路径
        fg_path: 滑块图路径
        bg_width, bg_height: 原图尺寸
        display_width, display_height: 页面展示尺寸
        method: "canny" 或 "gray"

    返回:
        滑块在原图上的 x 偏移量（像素）
    """
    if method == "gray":
        return detect_slider_position_gray(bg_path, fg_path, bg_width)
    else:
        return detect_slider_position_canny(bg_path, fg_path, bg_width)


# ================================================================
# 轨迹生成（物理模型）- 已移至 crypto_utils.py
# 这里保留一个更简单的几何轨迹备选
# ================================================================

def generate_track_simple(distance: int, points: int = 30) -> list:
    """
    简单轨迹（备选）：匀加速 + 匀减速。
    
    返回格式: [[x, y, dt], ...] 与 crypto_utils.generate_track_physics 一致。
    """
    import random
    track = []
    total_time = random.randint(800, 1500)
    dt_per_point = total_time // points

    for i in range(points + 1):
        fraction = i / points
        # easeInOut 曲线
        if fraction < 0.5:
            x = distance * (2 * fraction * fraction)
        else:
            x = distance * (1 - ((-2 * fraction + 2) ** 2) / 2)
        y = random.uniform(-2, 2)
        track.append([round(x, 1), round(y, 1), dt_per_point])

    # 在终点附近增加微调点
    for _ in range(random.randint(2, 5)):
        y = random.uniform(-1, 1)
        track.append([distance, round(y, 1), random.randint(20, 50)])

    return track
