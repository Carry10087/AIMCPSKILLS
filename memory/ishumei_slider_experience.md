# 数美 (ishumei) 滑块验证码逆向经验总结

> 项目路径: `projects/ishumei_slider`
> 最终通过率: **~85%** (3次重试内达标)
> 核心策略: jsdom 补环境 + Python OpenCV 缺口检测 + 物理超调轨迹

---

## 一、项目概述

目标：纯 Python 自动通过数美滑块验证码（conf → register → fverify），无需浏览器。

最终架构：**Python 主控 + Node.js jsdom 子进程**。Python 不直接提取参数或构造加密，而是驱动完整运行数美 SDK 的 jsdom 环境，拦截 fverify URL 即为成功。

---

## 二、文件结构

```
ishumei_slider/
├── main.py                     # Python 入口，调用 jsdom 子进程（含 5 次重试）
├── config.py                   # 配置常量（API 端点、组织 ID、版本号）
├── requirements.txt            # Python 依赖
├── captcha/
│   ├── api.py                  # 原纯 Python 路径：conf/register 接口封装
│   ├── crypto_utils.py         # DES 加解密、参数名提取（静态正则，已废弃）
│   └── slider.py               # 原纯 Python 路径：OpenCV 缺口检测
├── env_patch/
│   ├── env_patch_v8.js         # ★ 主力 jsdom 补环境脚本
│   ├── gap_detect.py           # Python OpenCV 缺口检测（被 jsdom 子进程调用）
│   ├── sdk_v104_206.js         # 数美 SDK v1.0.4-206 (449KB webpack bundle)
│   ├── smcp.min.js / fp.min.js / captcha_7867b6f.js  # SDK 依赖脚本
│   ├── trial_page.html         # 数美 trial 页面 HTML
│   └── package.json            # Node 依赖 (jsdom)
└── logs/                       # 运行日志
```

---

## 三、核心难题与解决

### 🔴 难题 1：SDK 在 jsdom 中无法初始化

**现象**：`SMCaptcha` 的 `initSMCaptcha` 回调从不触发，`inst.verify()` 无反应。

**根因**（按排查顺序）：
| 阶段 | 发现 | 解决 |
|------|------|------|
| A | `el.textContent = sdkCode` 导致 prototype 方法全部丢失 | 改用 `window.eval(sdkCode)` 直接执行 |
| B | CSS link 标签加载失败，SDK 等待 onload | 拦截 `createElement('link')`，手动触发 `el.onload` |
| C | `navigator.maxTouchPoints=undefined` 导致 SDK 判定为移动端 | 补全 `maxTouchPoints: 0`、`userAgentData`、`languages` |
| D | `act.os=web_mobile` 而非 `web_pc` | navigator mock 补全 |
| E | jsdom 无 CSS 布局，所有元素 `getBoundingClientRect` 返回 0 | 直接强制设置 `_data` 维度值 |
| F | `_data.registerApiInvalid: true` | 设为 `false` |

### 🔴 难题 2：静态提取 SDK 参数名失效

**现象**：`find_arg_names()` 正则无法匹配新版 SDK 混淆代码。

**解决**：放弃静态正则提取，改用 jsdom 完整运行 SDK。SDK 自己构造正确的 fverify URL，我们在 `script.src` setter 中拦截即可。

### 🔴 难题 3：fverify 持续 REJECT（核心难题，耗时最久）

**排查路径**（版本迭代）：

| 版本 | 轨迹方案 | 结果 |
|------|----------|------|
| v1 | 简单线性插值 | 0% PASS |
| v2 | 四段式（加速/巡航/减速/微调）+ 高斯噪声 | 0% PASS |
| v3 | 同上 + 停顿 + 回退 | 0% PASS |
| v4 | 百分比超调 (35~50%) + 物理加速 + 回退修正 | **4/6 PASS** 🎉 |
| v5 | 参考博客固定 20px 超调 | 0/2 PASS |
| v6 | v4 超调 + 封顶 55px + 最小 25 步 + Canny 边缘 | **3/5 PASS** |

**最终结论**：

- **缺口定位必须精确**——静态公式 `slideWidth - blockWidth` 假设缺口在最右侧，实际缺口可能在任意位置（44~190px），必须用 OpenCV 模板匹配
- **轨迹必须有显著超调**——服务器偏好超调后修正的人类行为，温和轨迹全被 REJECT
- **短 gap (< 50px)** 通过率低，是剩余主要瓶颈

### 🟡 难题 4：低对比度图片 OpenCV 检测不准

**解决**：给 `gap_detect.py` 增加多级回退：
1. 灰度图 TM_CCOEFF_NORMED / TM_CCORR_NORMED
2. Canny 边缘检测（3 种阈值：30/40/50）
3. CLAHE 增强 + Canny
4. 取最高置信度结果

---

## 四、轨迹生成器 v6 参数（最终生效版本）

```javascript
// 超调量：总距离的 25~43%，最少 6px，最多 55px
var overshoot = max(6, min(d*0.35, 30) + random*min(d*0.15, 15))
if (overshoot < 15 && d > 30) overshoot = 15 + random*10
overshoot = min(overshoot, 55)

// 加速度缩放：短距离使用 sqrt 子线性缩放
var scale = d < 60 ? max(1.5, 80 / max(d, 25)) : 1

// 三阶段加速度
阶段1 (0~17%): accel = d * 0.20~0.28
阶段2 (17~50%): accel = d * 0.28~0.38
阶段3 (50~82%): accel = d * 0.15~0.21
阶段4 (82~end): 负加速度制动
→ 回退段: 逐步递减 exceed 到精确目标

// 最少 25 步，Y轴 ±5px 抖动
// 总耗时: max(800, targetX * 22 + random(0~400)) ms
```

---

## 五、关键经验

### 1. jsdom 补环境优先级
```
SDK eval > CSS onload > navigator 补全 > DOM 维度注入 > 事件系统
```

### 2. 不要静态提取加密参数
JS 混淆版本迭代频繁，静态正则总会失效。完整运行 SDK 是唯一可持续的方案。

### 3. 轨迹是决定性因素
缺口位置、超调幅度、步数密度、时间分布都影响 PASS/REJECT。纯数学轨迹（无超调、无回退）100% REJECT。

### 4. Python + Node 混合架构
- Python：主控流程、日志、OpenCV 缺口检测、重试逻辑
- Node.js (jsdom)：SDK 运行、环境模拟、加密
- 通信：Python `subprocess` 启动 Node，stdout/stderr 传回结果

### 5. 重试机制是必要的
单次 PASS 率约 62~85%，3~5 次重试可达 >95% 整体通过率。每次重试获取新的 captcha，缺口位置和图片都不同。

---

## 六、剩余风险

| 风险 | 影响 | 建议 |
|------|------|------|
| 小 gap (< 50px) 通过率低 | ~15% 发生率，全 REJECT | 短距离特殊轨迹策略 / 二次确认 OpenCV 结果 |
| 极低置信度 (< 0.5) 高 REJECT | ~10% 发生率 | 可增加重试次数 / 回退到其他检测方法 |
| SDK 版本更新 | 补环境可能失效 | 监控 conf 返回的 SDK 版本号 |
| jsdom 内存泄漏 | 长时间运行后 OOM | 每次子进程独立运行，Node 退出自动释放 |
