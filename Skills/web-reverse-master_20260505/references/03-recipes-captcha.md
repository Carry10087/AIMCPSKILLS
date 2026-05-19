# 验证码对抗配方 + CDP桥

## 验证码5线拆分法

任何验证码/风控challenge，先拆成5条线：

```
1. 初始化线：获取challenge/token/图片URL
2. 图像识别线：缺口检测/OCR/目标检测
3. 参数builder线：collectData/轨迹/设备信息生成+加密
4. 环境指纹线：Canvas/WebGL/Audio/Crypto指纹
5. 最终verify线：提交验证结果 → 获取token
```

---

## 配方A：滑块验证码（标准型）

### 适用：背景图+滑块图分离的类型

**Step 1：获取验证码**
```python
import requests

# 不同站点的获取方式不同
# 丰巢: POST /captcha/querySlideImage/{uuid}
# 返回: {shadeImageUrl, slideImageUrl, checkId, key, pointX, pointY}

resp = requests.post(f"https://acs.fcbox.com/captcha/querySlideImage/{uuid}", json={})
data = resp.json()["data"]
bg_url = data["shadeImageUrl"]
slider_url = data["slideImageUrl"]
check_id = data["checkId"]
```

**Step 2：OpenCV检测缺口**
```python
import cv2
import numpy as np
import requests

# 下载图片
bg_bytes = requests.get(bg_url).content
slider_bytes = requests.get(slider_url).content
bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
slider = cv2.imdecode(np.frombuffer(slider_bytes, np.uint8), cv2.IMREAD_COLOR)

# 模板匹配
result = cv2.matchTemplate(bg, slider, cv2.TM_CCOEFF_NORMED)
_, max_val, _, max_loc = cv2.minMaxLoc(result)
distance = max_loc[0]  # 滑动距离（像素）
print(f"Distance: {distance}px, confidence: {max_val:.2f}")
```

**Step 3：生成人类-like轨迹**
```python
import random
import time

def generate_track(distance):
    """生成人类滑动轨迹"""
    track = []
    current = 0
    t = int(time.time() * 1000)
    
    # 加速阶段 (0 → 80%距离)
    while current < distance * 0.8:
        move = random.randint(2, 5)  # 较快步长
        current += move
        t += random.randint(10, 30)
        track.append({"x": min(current, distance), 
                      "y": random.randint(-3, 3),
                      "time": t})
    
    # 减速阶段 (80% → 目标)
    while current < distance:
        move = random.randint(1, 2)  # 较慢步长
        current += move
        t += random.randint(20, 50)
        track.append({"x": min(current, distance),
                      "y": random.randint(-2, 2),
                      "time": t})
    
    # 末端微调（模拟人类微调动作）
    for _ in range(random.randint(1, 3)):
        current += random.choice([-1, 1])
        t += random.randint(50, 150)
        track.append({"x": current, "y": random.randint(-1, 1), "time": t})
    
    return track
```

**Step 4：加密并提交**
```python
import hashlib

def submit_captcha(check_id, uuid, track, client_ip):
    """加密轨迹并提交验证"""
    # 丰巢: md5(clientIp + checkId + uuid + track_string)
    track_str = ",".join([f"({p['x']},{p['y']},{p['time']})" for p in track])
    sign = hashlib.md5(f"{client_ip}{check_id}{uuid}{track_str}".encode()).hexdigest()
    
    # AES加密整个请求体
    # ...（不同站点加密方式不同）
    
    resp = requests.post(
        f"https://acs.fcbox.com/captcha/checkCode/{uuid}",
        json={"data": encrypted_data, "int8": False}
    )
    return resp.json().get("data", {}).get("token")
```

---

## 配方B：涂鸦/第三方验证码（RSA+AES混合加密型）

### 适用：tuyacn.com、顶象、极验等第三方验证码

**核心特征**：
- 大文件JS（yrule.js 742KB级别）
- RSA+AES混合加密
- collectData（行为采集数据，3000-4800字符Base64）

**Step 1：深度Hook捕获加密数据**
```javascript
// MCP: js-reverse-mcp → inject_before_load → 注入Hook

// Hook collectData发送函数
// 搜索: sendCollect / collectData / XMLHttpRequest.send
const origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function(body) {
    if (this._request_url?.includes('collectData')) {
        console.log('[collectData]', {
            url: this._request_url,
            body: body
        });
        // 保存完整请求数据
        window.__lastCollectData = body;
    }
    return origSend.call(this, body);
};
```

**Step 2：分析加密流程**
```python
import base64
import json

# 从捕获的数据分析
captured = json.loads(window.__lastCollectData)
"""
{
  "type": 1,
  "challenge": "s_xxx",
  "verifyId": "v_xxx", 
  "collectData": "Base64(AES加密的行为数据, 3000-4800字符)",
  "key": "Base64(RSA加密的AES密钥, 344字符)",
  "callback": "verify_xxx"
}
"""

# key字段：RSA-2048加密的AES密钥（解码后256字节）
decoded_key = base64.b64decode(captured["key"])
print(f"RSA encrypted key length: {len(decoded_key)} bytes")  # 应为256
```

**Step 3：反混淆定位加密函数**
```powershell
# 1. 保存 yrule.js 和主混淆文件
# 2. AST反混淆
node .qoder/skills/ast-deobfuscation/scripts/run-pipeline.js yrule.js output/
# 3. 搜索AES/RSA加密函数
# 搜索: AES.encrypt / RSA.encrypt / publicKey / encrypt
```

---

## 配方C：算术验证码

### 适用：若依框架等简单算术题

```python
# 接口: GET /captchaImage → 返回算术题图片
# 响应: {code: 200, data: {img: "base64...", uuid: "xxx"}}

import requests
import re

# Step 1: 获取验证码
resp = requests.get("https://xxx.com/captchaImage")
data = resp.json()["data"]

# Step 2: 下载图片并用OCR识别
# 或直接分析（若依的算术题通常在图片URL中有线索）

# Step 3: 计算答案
# 图片内容: "3 + 5 = ?"
question = "3 + 5"  # 从OCR结果
answer = eval(question)  # 8

# Step 4: 提交验证
resp = requests.post("https://xxx.com/login", json={
    "username": "...",
    "password": "...",
    "code": str(answer),
    "uuid": data["uuid"]
})
```

---

## 配方D：CDP桥（签名太复杂时的终极方案）

### 适用场景
- 签名算法极复杂且频繁升级
- 本地纯算/补环境方案都已失效
- 有AdsPower且profile已登录目标站点

### 架构原理

```
Python脚本 ←─ CDP WebSocket ─→ AdsPower浏览器
    │                               │
    │ Runtime.evaluate              │ 内部axios自动签名
    │ 注入桥接函数                   │ Cookie/TLS/Headers全自动
    │                               │
    ▼                               ▼
JSON结果 ←──────────────── 浏览器自动生成
```

### 实现代码

```python
# cdp_bridge.py - CDP桥核心
# 依赖: pip install websocket-client

import json
import http.client
import websocket

class CDPBridge:
    """通过CDP WebSocket在AdsPower浏览器中执行JavaScript
    
    使用方式：
    1. MCP: adspower-browser → open-browser → get-opened-browser 获取CDP端口
    2. bridge = CDPBridge(61559); bridge.connect()
    3. result = bridge.evaluate("document.title")
    
    ⚠️ 依赖 pip install websocket-client（非标准库），不可用裸socket手写WebSocket帧
    """
    
    def __init__(self, cdp_port):
        self.cdp_port = cdp_port
        self._msg_id = 0
        self.ws = None
    
    def _get_ws_url(self):
        """从CDP HTTP端点获取WebSocket URL"""
        conn = http.client.HTTPConnection("127.0.0.1", self.cdp_port)
        conn.request("GET", "/json")
        resp = json.loads(conn.getresponse().read())
        for tab in resp:
            if tab.get("type") == "page":
                return tab["webSocketDebuggerUrl"]
        raise Exception("未找到可调试的页面标签")
    
    def connect(self):
        """建立WebSocket连接"""
        ws_url = self._get_ws_url()
        self.ws = websocket.create_connection(ws_url)
    
    def evaluate(self, expression):
        """在浏览器中执行JavaScript并返回结果"""
        self._msg_id += 1
        msg = json.dumps({
            "id": self._msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            }
        })
        self.ws.send(msg)
        response = json.loads(self.ws.recv())
        
        if "result" in response:
            return response["result"]["result"]["value"]
        raise Exception(f"CDP error: {response}")

# 使用示例
bridge = CDPBridge(61559)

# 注入桥接函数（在浏览器webpack中注册API调用）
bridge.evaluate("""
    // 扫描webpack模块，找到axios实例
    const modules = window.__webpack_require__.c;
    for (let id in modules) {
        // 找到axios或业务API模块
        // 注册全局桥接函数
    }
    window.__bridge = {
        homefeed: async (params) => {
            const axios = window.__webpack_require__(12345);
            const res = await axios.post('/api/homefeed', params);
            return res.data;
        }
    };
""")

# 调用业务API
result = bridge.evaluate("""
    window.__bridge.homefeed({
        cursor_score: "", num: 8, refresh_type: 1,
        category: "homefeed_recommend"
    })
""")
```

### 关键约束
- AdsPower profile必须已登录目标站点
- 当前浏览器tab必须在目标站点域名下
- 业务调用链必须符合浏览器原生顺序
- 每轮调用前可能需要预热（homefeed → detail → report）

> 完整实现参考：`sites/rednote/src/cdp_bridge.py`

---

## 配方E：字体反爬

### 适用：WOFF2/WOFF字体混淆数字

> 📌 SKILL.md [配方7](../SKILL.md#配方7字体反爬) 提供简洁版（仅 numberOfContours+width+height），本配方为完整版（含 xMin/xMax/yMin/yMax 几何边界指纹）。

```python
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
import requests

# Step 1: 下载字体
font_url = "https://xxx.com/fonts/xxx.woff2"
font_bytes = requests.get(font_url).content
with open("temp.woff2", "wb") as f:
    f.write(font_bytes)

# Step 2: 解析字体
font = TTFont("temp.woff2")
cmap = font.getBestCmap()  # unicode → glyph name 映射

# Step 3: 提取glyph指纹
fingerprints = {}
for uni, glyph_name in cmap.items():
    glyph = font['glyf'][glyph_name]
    # 几何指纹: 轮廓数、最小x、最大x、宽度、高度
    if hasattr(glyph, 'numberOfContours'):
        fp = (
            glyph.numberOfContours,
            glyph.xMin, glyph.xMax,
            glyph.yMin, glyph.yMax,
            glyph.width, glyph.height
        )
        fingerprints[glyph_name] = fp

# Step 4: 第一页推导映射
# init API返回真实数字 → 比对glyph指纹 → 建立映射表
# 后续页面直接用指纹匹配解码
```

---

## 验证码方案选择速查

| 验证码类型 | 首选方案 | 备选方案 |
|-----------|----------|----------|
| 滑块拼图（简单）| OpenCV模板匹配 + 轨迹生成 | 云码API打码 |
| 滑块拼图（加密）| OpenCV + 反混淆加密函数 | CDP桥自动化 |
| 涂鸦/第三方验证码 | 深度Hook + AST反混淆 + 纯算 | 浏览器自动化 |
| 算术题 | Python eval | OCR识别 |
| 点选验证码 | 目标检测模型 | 云码API |
| 旋转验证码 | 角度回归模型 | 云码API |
| 腾讯验证码 | collect参数还原 | CDP桥 |
| 极验4 | w参数还原 | 浏览器自动化 |
