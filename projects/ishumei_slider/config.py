# ============================================================
# 数美滑块验证码 - 配置常量
# 
# 分析依据：数美官方 trial 页面 (www.ishumei.com/trial/captcha.html)
# API 端点来自：captcha.fengkongcloud.com
# ============================================================

# ---------- API 端点 ----------
CONF_URL = "https://captcha.fengkongcloud.com/ca/v1/conf"
REGISTER_URL = "https://captcha.fengkongcloud.com/ca/v1/register"
FVERIFY_URL = "https://captcha.fengkongcloud.com/ca/v2/fverify"

# ---------- 固定请求参数（从 trial 页面提取）----------
ORGANIZATION = "RlokQwRlVjUrTUlkIqOg"
APP_ID = "default"
SDK_VER = "1.1.3"
RVERSION = "1.0.3"
PROTOCOL = "70"  # fverify 接口的 protocol 字段
MODEL = "slide"
LANG = "zh-cn"
CHANNEL = "YingYongBao"
OSTYPE = "web"
ACT_OS = "web_pc"

# ---------- 图片相关 ----------
# 页面展示尺寸（CSS 像素）
CAPTCHA_DISPLAY_WIDTH = 310
CAPTCHA_DISPLAY_HEIGHT = 155
# 原图尺寸（从 register 接口 bg_height/bg_width 获取，这里做默认值）
CAPTCHA_IMAGE_WIDTH = 600
CAPTCHA_IMAGE_HEIGHT = 300

# ---------- 滑块识别 ----------
# 模板匹配的相似度阈值（0-1，越高越严格）
MATCH_THRESHOLD = 0.35
# 边缘检测的 Canny 阈值
CANNY_LOW = 50
CANNY_HIGH = 150

# ---------- 轨迹模拟 ----------
# 滑动起始位置偏移范围（防止被检测为脚本）
START_OFFSET_MIN = 2
START_OFFSET_MAX = 8
# 滑动总时间范围（毫秒）
SLIDE_TIME_MIN = 800
SLIDE_TIME_MAX = 1500
# 轨迹点之间的最小时间间隔（毫秒）
TRACK_STEP_MIN = 5
TRACK_STEP_MAX = 20

# ---------- 请求头 ----------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.ishumei.com/trial/captcha.html",
}

# ---------- 日志路径 ----------
LOG_DIR = "logs"
IMAGE_DIR = "images"
