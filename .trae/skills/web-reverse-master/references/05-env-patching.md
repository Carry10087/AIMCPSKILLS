# 浏览器补环境完整方法

## 原则

**不要试图补全一整个浏览器。只补目标代码实际用到的API。**

优先级：
1. 补 `undefined` 导致主流程中断的
2. 补 `Illegal invocation` 错误的
3. 补原型链和描述符不一致的
4. 补 native `toString` 暴露的
5. 补指纹异常的

---

## 快速诊断

### 方法A：简单Proxy收集
```javascript
// MCP: js-reverse-mcp → evaluate_script
const handler = {
    get(target, prop) {
        if (!(prop in target)) {
            console.log('[MISSING]', prop);
            target[prop] = undefined;
        }
        return target[prop];
    }
};
window = new Proxy(window, handler);
```

### 方法B：watch() 全Proxy监控（推荐）

```javascript
// 强大的环境监控工具——递归Proxy所有对象
// 能够：
// 1. 精确追踪代码实际访问了哪些属性
// 2. 记录属性的get/set/in/delete操作
// 3. 发现运行时才赋值的属性（不要补那些）

function watch(obj, name, visited = new WeakSet()) {
    if (obj === null || typeof obj !== 'object' || visited.has(obj)) {
        return obj;
    }
    visited.add(obj);

    return new Proxy(obj, {
        get: function (target, property, receiver) {
            try {
                // 跳过特殊属性
                if (typeof property === 'symbol' || 
                    property === 'constructor' || 
                    property === '__proto__') {
                    return Reflect.get(target, property, receiver);
                }
                const value = Reflect.get(target, property, receiver);
                // 递归代理子对象
                if (typeof value === 'object' && value !== null) {
                    const nestedName = `${name}.${String(property)}`;
                    return watch(value, nestedName, visited);
                }
                // 记录undefined访问（缺环境）
                if (value === undefined) {
                    console.log(`对象 => ${name}, 读取属性: ${String(property)}, 值为: undefined`);
                }
            } catch (e) {}
            return Reflect.get(target, property, receiver);
        },
        set: (target, property, newValue, receiver) => {
            try {
                console.log(`对象 => ${name}, 设置属性: ${String(property)}, 值: ${typeof newValue === 'function' ? 'function' : newValue}`);
            } catch (e) {}
            return Reflect.set(target, property, newValue, receiver);
        },
        has: function(target, property) {
            console.log(`对象 => ${name}, in运算符检测: ${String(property)}`);
            return Reflect.has(target, property);
        },
        deleteProperty: function(target, property) {
            console.log(`对象 => ${name}, 删除属性: ${String(property)}`);
            return Reflect.deleteProperty(target, property);
        },
        ownKeys: function(target) {
            console.log(`对象 => ${name}, 获取自身键(Object.keys)`);
            return Reflect.ownKeys(target);
        },
        defineProperty: function(target, property, descriptor) {
            console.log(`对象 => ${name}, 定义属性: ${String(property)}`);
            return Reflect.defineProperty(target, property, descriptor);
        },
        setPrototypeOf: function(target, prototype) {
            console.log(`检测: setPrototypeOf 被调用 (对象: ${name})`);
            return Reflect.setPrototypeOf(target, prototype);
        },
        getPrototypeOf: function(target) {
            console.log(`检测: getPrototypeOf 被调用 (对象: ${name})`);
            return Reflect.getPrototypeOf(target);
        }
    });
}

// 使用方式
document = watch(document, 'document');
navigator = watch(navigator, 'navigator');
window = watch(window, 'window');
```

**watch() 使用建议**：
- 在浏览器Console中先运行一次 → 获得目标代码**实际**访问了哪些属性
- 只补 `undefined` 的那些，不补运行时赋值的
- 补完立即验证，不要等到"全补完"

---

## 标准补环境模板

```javascript
// env/browser.js - 最小补环境模板
const vm = require('vm');

// ==========================================
// 1. 基础全局对象
// ==========================================
const context = {
    // 全局
    window: {},
    self: {},
    globalThis: {},
    top: {},
    parent: {},
    
    // 基础函数
    setTimeout: setTimeout,
    setInterval: setInterval,
    clearTimeout: clearTimeout,
    clearInterval: clearInterval,
    
    // Console
    console: console,
    
    // 编码
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
};

// 循环引用
context.window.window = context.window;
context.window.self = context.window;
context.window.top = context.window;
context.self = context.window;
context.globalThis = context.window;

// ==========================================
// 2. Navigator
// ==========================================
context.navigator = {
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    platform: "Win32",
    language: "zh-CN",
    languages: ["zh-CN", "zh"],
    cookieEnabled: true,
    hardwareConcurrency: 8,
    deviceMemory: 8,
    maxTouchPoints: 0,
    vendor: "Google Inc.",
    vendorSub: "",
    productSub: "20030107",
    appCodeName: "Mozilla",
    appName: "Netscape",
    appVersion: "5.0 ...",
    onLine: true,
    webdriver: false,
    plugins: { length: 0, item: () => null, namedItem: () => null, refresh: () => {} },
    mimeTypes: { length: 0, item: () => null, namedItem: () => null },
};
context.window.navigator = context.navigator;

// ==========================================
// 3. Document
// ==========================================
context.document = {
    cookie: "",
    referrer: "",
    domain: "",
    URL: "https://target-site.com/",
    title: "",
    characterSet: "UTF-8",
    charset: "UTF-8",
    readyState: "complete",
    hidden: false,
    visibilityState: "visible",
    createElement: (tag) => {
        const el = {
            tagName: tag.toUpperCase(),
            style: {},
            setAttribute: () => {},
            getAttribute: () => null,
            appendChild: () => {},
        };
        if (tag === 'canvas') {
            el.getContext = () => ({
                fillRect: () => {}, fillText: () => {},
                getImageData: () => ({ data: new Uint8Array(100) }),
                measureText: () => ({ width: 50 }),
            });
        }
        return el;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    getElementsByClassName: () => [],
    getElementsByTagName: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
};
context.window.document = context.document;

// ==========================================
// 4. Location
// ==========================================
context.location = {
    href: "https://target-site.com/",
    protocol: "https:",
    host: "target-site.com",
    hostname: "target-site.com",
    port: "",
    pathname: "/",
    search: "",
    hash: "",
    origin: "https://target-site.com",
    ancestorOrigins: { length: 0 },
    reload: () => {},
    replace: () => {},
    assign: () => {},
};
context.window.location = context.location;
context.document.location = context.location;

// ==========================================
// 5. Storage
// ==========================================
class Storage {
    constructor() { this._data = {}; }
    getItem(k) { return this._data[k] || null; }
    setItem(k, v) { this._data[k] = String(v); }
    removeItem(k) { delete this._data[k]; }
    clear() { this._data = {}; }
    get length() { return Object.keys(this._data).length; }
    key(i) { return Object.keys(this._data)[i] || null; }
}
context.localStorage = new Storage();
context.sessionStorage = new Storage();
context.window.localStorage = context.localStorage;
context.window.sessionStorage = context.sessionStorage;

// ==========================================
// 6. Crypto（关键！很多风控用到）
// ==========================================
const crypto = require('crypto');
context.crypto = {
    getRandomValues: (arr) => {
        const bytes = crypto.randomBytes(arr.length);
        for (let i = 0; i < arr.length; i++) arr[i] = bytes[i];
        return arr;
    },
    randomUUID: () => crypto.randomUUID(),
    subtle: {}, // 如果用到subtle需要专门处理
};
context.window.crypto = context.crypto;

// ==========================================
// 7. Performance
// ==========================================
context.performance = {
    now: () => Date.now() - startTime + performance.now() % 100,
    timing: {
        navigationStart: Date.now() - 1000,
        domLoading: Date.now() - 500,
        domComplete: Date.now() - 100,
    },
    getEntriesByType: () => [],
    mark: () => {},
    measure: () => {},
};
context.window.performance = context.performance;

// ==========================================
// 8. Screen
// ==========================================
context.screen = {
    width: 1920, height: 1080,
    availWidth: 1920, availHeight: 1040,
    colorDepth: 24, pixelDepth: 24,
};
context.window.screen = context.screen;

// ==========================================
// 9. History
// ==========================================
context.history = {
    length: 1,
    state: null,
    back: () => {}, forward: () => {}, go: () => {},
    pushState: () => {}, replaceState: () => {},
};
context.window.history = context.history;

// ==========================================
// 10. 原型链保护（防止被检测出补环境）
// ==========================================

// === 核心工具：obj_toString ===
function obj_toString(obj, name) {
    Object.defineProperty(obj, Symbol.toStringTag, { 
        value: name,
        configurable: true,
        enumerable: false,
        writable: false
    });
}
// 用法示例
obj_toString(context.window, 'Window');
obj_toString(context.navigator, 'Navigator');
obj_toString(context.document, 'HTMLDocument');
obj_toString(context.location, 'Location');

// === 高级 Function.prototype.toString 对抗（闭包捕获版）===
!function() {
    var nativeToString = Function.prototype.toString;
    var nativeFuncs = [];
    
    function markAsNative(fn, name) {
        if (!nativeFuncs.includes(fn)) {
            nativeFuncs.push(fn);
        }
        Object.defineProperty(fn, 'name', { value: name || '' });
        return fn;
    }
    
    Object.defineProperty(Function.prototype, "toString", {
        enumerable: false,
        configurable: true,
        writable: true,
        value: function() {
            if (typeof this === 'function' && nativeFuncs.includes(this)) {
                return `function ${this.name || ''}() { [native code] }`;
            }
            return nativeToString.call(this);
        }
    });
    
    context.__markAsNative = markAsNative;
}();

// === 环境标志删除 ===
delete context.process;
delete context.__dirname;
delete context.__filename;
context.globalThis = context.window;

// === Symbol.toStringTag 保护 ===
Object.defineProperty(Object.prototype, Symbol.toStringTag, {
    get() {
        return Object.getPrototypeOf(this).constructor.name;
    }
});

// ==========================================
// 11. 创建沙箱并运行
// ==========================================
const startTime = Date.now();
vm.createContext(context);

// 加载目标代码
const targetCode = require('fs').readFileSync('target.js', 'utf8');
vm.runInContext(targetCode, context);
```

---

## 特殊对象处理

### document.all

`document.all` 是 V8 的特殊对象，纯 JS 无法完全模拟。如果被检测到：

```javascript
// 最低限度模拟
context.document.all = {
    0: context.document.documentElement,
    length: 1,
    item: (i) => i === 0 ? context.document.documentElement : null,
    namedItem: () => null,
};
// 使其 typeof 返回 undefined（HTMLAllCollection 特征）
// 纯JS做不到，需要 C++ addon
```

### WebGL/Canvas 指纹

```javascript
// 最小模拟
context.HTMLCanvasElement = function() {};
context.HTMLCanvasElement.prototype.getContext = function(type) {
    if (type === 'webgl' || type === 'experimental-webgl') {
        return {
            getParameter: (p) => {
                // 返回真实浏览器的参数值
                const params = {
                    0x1F02: "WebGL 1.0",       // VERSION
                    0x1F00: "WebKit",            // VENDOR
                    0x9245: "ANGLE (...)",       // UNMASKED_VENDOR
                    0x9246: "ANGLE (...)",       // UNMASKED_RENDERER
                };
                return params[p] || null;
            },
            getExtension: () => null,
            getShaderPrecisionFormat: () => ({ rangeMin: 127, rangeMax: 127, precision: 23 }),
        };
    }
    if (type === '2d') {
        return { fillRect: () => {}, fillText: () => {}, getImageData: () => ({ data: [] }) };
    }
    return null;
};
```

---

## 补环境决策

| 情况 | 方案 |
|------|------|
| 只用到 navigator/document 基础属性 | 最小补环境（模板就够） |
| 用到 Canvas/WebGL 指纹 | 补环境 + 真实指纹值 |
| 用到 Audio/WebRTC/Worker | 考虑切换到CDP桥 |
| 代码已收缩成纯函数 | 不要补环境，直接扣函数 |
| 补环境成本 > 2小时 | 直接上CDP桥 |
| 需要 document.all | 无法完美模拟，用CDP桥 |

---

## 切换策略

当补环境成本过高时，切换到浏览器内执行：

```python
# 方案1: CDP桥 → 在AdsPower浏览器内执行
# 方案2: Node.js子进程 + JsRpc → 通过WebSocket通信
# 方案3: 直接在Node.js中启动Puppeteer/Playwright
```

---

## 高级指纹模拟

### navigator.plugins 真实模拟

简单的 `{length: 0}` 会被检测为异常，需要模拟真实浏览器插件列表：

```javascript
// 真实浏览器的 navigator.plugins（Chrome 典型值）
context.navigator.plugins = (function() {
    const pluginList = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
    ];
    
    const plugins = {
        length: pluginList.length,
        item: function(i) { return i < pluginList.length ? pluginList[i] : null; },
        namedItem: function(name) { return pluginList.find(p => p.name === name) || null; },
        refresh: function() {},
        0: pluginList[0],
        1: pluginList[1],
        2: pluginList[2],
    };
    
    // 补上原型链（PluginArray 类型检测）
    Object.setPrototypeOf(plugins, PluginArray.prototype);
    return plugins;
})();

// navigator.mimeTypes（与 plugins 关联）
context.navigator.mimeTypes = (function() {
    const mimeTypeList = [
        { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];
    
    const mimeTypes = {
        length: mimeTypeList.length,
        item: function(i) { return i < mimeTypeList.length ? mimeTypeList[i] : null; },
        namedItem: function(name) { return mimeTypeList.find(m => m.type === name) || null; },
        0: mimeTypeList[0],
        1: mimeTypeList[1],
    };
    
    Object.setPrototypeOf(mimeTypes, MimeTypeArray.prototype);
    return mimeTypes;
})();
```

### Canvas 指纹完整模拟（toDataURL/toBlob）

```javascript
// Canvas 指纹检测通常：
// 1. 渲染一段文字/图形到 Canvas
// 2. toDataURL() 提取 Base64 → 对不同浏览器/OS 输出不同
// 3. 比对指纹判断是否伪造

// 方案：提供真实浏览器的指纹值（从真实浏览器捕获）
context.HTMLCanvasElement = function() {};
context.HTMLCanvasElement.prototype = {
    getContext: function(type) {
        if (type === '2d') {
            const ctx = {
                // === 字体相关 ===
                font: '',
                textBaseline: 'alphabetic',
                textAlign: 'start',
                
                // === 绘制方法（stub） ===
                fillRect: function() {},
                strokeRect: function() {},
                fillText: function() {},
                strokeText: function() {},
                beginPath: function() {},
                moveTo: function() {},
                lineTo: function() {},
                arc: function() {},
                bezierCurveTo: function() {},
                quadraticCurveTo: function() {},
                closePath: function() {},
                fill: function() {},
                stroke: function() {},
                clip: function() {},
                save: function() {},
                restore: function() {},
                scale: function() {},
                rotate: function() {},
                translate: function() {},
                transform: function() {},
                setTransform: function() {},
                
                // === 样式属性 ===
                fillStyle: '#000000',
                strokeStyle: '#000000',
                lineWidth: 1,
                lineCap: 'butt',
                lineJoin: 'miter',
                miterLimit: 10,
                globalAlpha: 1,
                globalCompositeOperation: 'source-over',
                shadowBlur: 0,
                shadowColor: 'rgba(0,0,0,0)',
                shadowOffsetX: 0,
                shadowOffsetY: 0,
                
                // === 绘制方法（关键） ===
                drawImage: function() {},
                createImageData: function() { return { data: new Uint8Array(100) }; },
                getImageData: function(x, y, w, h) {
                    // 返回固定指纹数据（从真实浏览器捕获）
                    const size = w * h * 4;
                    const data = new Uint8Array(size);
                    // 如果需要真实指纹，从浏览器捕获一次后填入
                    return { data, width: w, height: h };
                },
                putImageData: function() {},
                
                // === 文本测量 ===
                measureText: function(text) {
                    // 返回固定宽度（从真实浏览器捕获）
                    return {
                        width: text.length * 8,
                        actualBoundingBoxAscent: 10,
                        actualBoundingBoxDescent: 3,
                        actualBoundingBoxLeft: -1,
                        actualBoundingBoxRight: text.length * 8 + 1,
                    };
                },
                
                // === Canvas 状态 ===
                canvas: {
                    width: 280,
                    height: 60,
                    toDataURL: function(type) {
                        // 返回固定指纹（从真实浏览器捕获一次 → 存入此处）
                        return 'data:image/png;base64,iVBORw0KGgoAAAA...';
                    },
                    toBlob: function(callback) {
                        callback(new Blob([]));
                    },
                },
            };
            return ctx;
        }
        
        if (type === 'webgl' || type === 'experimental-webgl') {
            return createWebGLContext();  // 见下方 WebGL 模拟
        }
        
        return null;
    },
    
    toDataURL: function() {
        return 'data:image/png;base64,iVBORw0KGgoAAAA...';
    },
    toBlob: function(callback) {
        callback(new Blob([]));
    },
    
    width: 280,
    height: 60,
};

// WebGL 上下文详细模拟
function createWebGLContext() {
    return {
        // === 关键指纹参数 ===
        getParameter: function(p) {
            const params = {
                0x1F02: 'WebGL 1.0 (OpenGL ES 2.0 Chromium)',  // VERSION
                0x1F00: 'WebKit',                                  // VENDOR
                0x1F01: 'WebKit WebGL',                           // RENDERER
                0x9245: 'Google Inc. (NVIDIA)',                   // UNMASKED_VENDOR
                0x9246: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 ...)', // UNMASKED_RENDERER
                0x8B8A: 4096,        // MAX_TEXTURE_SIZE
                0x8B8B: 32,          // MAX_CUBE_MAP_TEXTURE_SIZE
                0x8B8C: 16,          // MAX_RENDERBUFFER_SIZE
                0x8B8D: 32,          // MAX_VIEWPORT_DIMS (返回 Int32Array)
                0x8B8E: 16,          // MAX_VERTEX_TEXTURE_IMAGE_UNITS
                0x8B8F: 16,          // MAX_TEXTURE_IMAGE_UNITS
                0x8B90: 32,          // MAX_COMBINED_TEXTURE_IMAGE_UNITS
                0x8B4A: 31,          // MAX_VERTEX_ATTRIBS
                0x8B4B: 30,          // MAX_VERTEX_UNIFORM_VECTORS
                0x8B4C: 4096,       // MAX_VARYING_VECTORS
                0x8B4D: 1024,       // MAX_FRAGMENT_UNIFORM_VECTORS
                0x8869: 16,          // MAX_DRAW_BUFFERS
                0x9240: 1,           // UNMASKED flags
            };
            return params[p] || null;
        },
        getExtension: function(name) {
            // 返回常见的 WebGL 扩展
            const extensions = {
                'WEBGL_debug_renderer_info': {},
                'EXT_texture_filter_anisotropic': { MAX_TEXTURE_MAX_ANISOTROPY_EXT: 16 },
                'WEBGL_lose_context': { loseContext: function() {}, restoreContext: function() {} },
            };
            return extensions[name] || null;
        },
        getSupportedExtensions: function() {
            return [
                'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
                'EXT_disjoint_timer_query', 'EXT_float_blend', 'EXT_frag_depth',
                'EXT_shader_texture_lod', 'EXT_texture_compression_bptc',
                'EXT_texture_compression_rgtc', 'EXT_texture_filter_anisotropic',
                'EXT_sRGB', 'OES_element_index_uint', 'OES_fbo_render_mipmap',
                'OES_standard_derivatives', 'OES_texture_float', 'OES_texture_float_linear',
                'OES_texture_half_float', 'OES_texture_half_float_linear',
                'OES_vertex_array_object', 'WEBGL_color_buffer_float',
                'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
                'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders', 'WEBGL_depth_texture',
                'WEBGL_draw_buffers', 'WEBGL_lose_context', 'WEBGL_multi_draw',
            ];
        },
        getShaderPrecisionFormat: function() {
            return { rangeMin: 127, rangeMax: 127, precision: 23 };
        },
        
        // === Stub 方法 ===
        clear: function() {}, clearColor: function() {},
        createBuffer: function() { return {}; },
        createShader: function() { return {}; },
        createProgram: function() { return {}; },
        bindBuffer: function() {}, bufferData: function() {},
        shaderSource: function() {}, compileShader: function() {},
        attachShader: function() {}, linkProgram: function() {},
        useProgram: function() {}, viewport: function() {},
        enable: function() {}, disable: function() {},
        drawArrays: function() {}, drawElements: function() {},
        uniform1i: function() {}, uniform2f: function() {},
        vertexAttribPointer: function() {}, enableVertexAttribArray: function() {},
        getAttribLocation: function() { return 0; },
        getUniformLocation: function() { return {}; },
        
        drawingBufferWidth: 300,
        drawingBufferHeight: 150,
    };
}
```

### AudioContext 模拟

```javascript
// AudioContext 指纹检测常用于：
// 1. 生成音频信号 → 分析频谱特征
// 2. 不同浏览器/OS 的 AudioContext 输出略有不同

context.AudioContext = context.AudioContext || context.webkitAudioContext || function() {
    return {
        sampleRate: 44100,
        destination: {},
        currentTime: 0,
        state: 'running',
        
        createOscillator: function() {
            return {
                type: 'sine',
                frequency: { value: 0, setValueAtTime: function() {} },
                connect: function() {},
                start: function() {},
                stop: function() {},
                disconnect: function() {},
            };
        },
        
        createGain: function() {
            return {
                gain: { value: 0, setValueAtTime: function() {} },
                connect: function() {},
                disconnect: function() {},
            };
        },
        
        createDynamicsCompressor: function() {
            return {
                threshold: { value: -24 },
                knee: { value: 30 },
                ratio: { value: 12 },
                attack: { value: 0.003 },
                release: { value: 0.25 },
                connect: function() {}, disconnect: function() {},
            };
        },
        
        createAnalyser: function() {
            return {
                fftSize: 2048,
                frequencyBinCount: 1024,
                connect: function() {},
                disconnect: function() {},
                getByteFrequencyData: function(arr) {
                    // 返回固定指纹数据
                    for (let i = 0; i < arr.length; i++) {
                        arr[i] = (i * 7 + 13) % 256;
                    }
                },
            };
        },
        
        createBuffer: function(channels, length, sampleRate) {
            return {
                numberOfChannels: channels,
                length: length,
                sampleRate: sampleRate,
                getChannelData: function(ch) {
                    const arr = new Float32Array(length);
                    for (let i = 0; i < length; i++) {
                        arr[i] = Math.sin(i / 100) * 0.1;
                    }
                    return arr;
                },
            };
        },
        
        createBufferSource: function() {
            return {
                buffer: null,
                connect: function() {},
                start: function() {},
                stop: function() {},
                disconnect: function() {},
            };
        },
        
        close: function() {},
        decodeAudioData: function(data, success, error) {
            success({ length: 1000, sampleRate: 44100 });
        },
    };
};
context.window.AudioContext = context.AudioContext;
```

---

## 模块化补环境架构

将大型单体模板拆分为可插拔模块，按需加载：

```javascript
// env_loader.js — 模块化补环境加载器
const vm = require('vm');
const fs = require('fs');

class EnvPatcher {
    constructor() {
        this.context = {
            window: {},
            self: {},
            globalThis: {},
            console: console,
            setTimeout: setTimeout,
            setInterval: setInterval,
        };
        // 循环引用
        this.context.window.window = this.context.window;
        this.context.self = this.context.window;
        this.context.globalThis = this.context.window;
        
        this.modules = {};
    }
    
    // 加载模块（返回修补后的 context）
    load(moduleName, config = {}) {
        const module = require(`./modules/${moduleName}`);
        this.context = module.patch(this.context, config);
        this.modules[moduleName] = true;
        console.log(`[Env] Loaded: ${moduleName}`);
        return this;
    }
    
    // 只在缺属性时加载
    loadIfMissing(propertyPath, moduleName, config = {}) {
        const parts = propertyPath.split('.');
        let current = this.context;
        for (const p of parts) {
            if (current[p] === undefined) {
                return this.load(moduleName, config);
            }
            current = current[p];
        }
        console.log(`[Env] Skip: ${moduleName} (${propertyPath} already exists)`);
        return this;
    }
    
    // 运行目标代码
    run(code) {
        vm.createContext(this.context);
        return vm.runInContext(code, this.context);
    }
    
    // 导出 context 给外部使用
    get() {
        return this.context;
    }
}

// === 使用示例 ===
const env = new EnvPatcher();

// 按需加载模块
env.load('navigator', { ua: 'Mozilla/5.0 ...', platform: 'Win32' })
   .load('document', { url: 'https://target.com/' })
   .load('location', { href: 'https://target.com/' })
   .load('storage')
   .load('crypto')
   .loadIfMissing('window.screen', 'screen')
   .loadIfMissing('window.AudioContext', 'audio')
   .loadIfMissing('window.HTMLCanvasElement', 'canvas');

// 执行目标代码
const targetCode = fs.readFileSync('target.js', 'utf-8');
env.run(targetCode);
```

### 模块目录结构

```
modules/
├── navigator.js    ← navigator 对象
├── document.js     ← document 对象（含 createElement）
├── location.js     ← location 对象
├── storage.js      ← localStorage + sessionStorage
├── crypto.js       ← crypto.getRandomValues
├── screen.js       ← screen 属性
├── history.js      ← history 对象
├── canvas.js       ← HTMLCanvasElement + WebGL
├── audio.js        ← AudioContext
├── webworker.js    ← Worker 最小 stub
├── webrtc.js       ← RTCPeerConnection 最小 stub
├── plugins.js      ← navigator.plugins + mimeTypes
├── prototypes.js   ← 原型链保护（toString/descriptors）
└── utils.js        ← watch() / obj_toString 等工具
```

---

## 自动诊断脚本

快速检测补环境与真实浏览器的差异：

```javascript
// env_diff.js — 补环境差异诊断工具
// 在真实浏览器 Console 中运行，然后对比补环境输出

(function() {
    const CHECKS = [
        // === navigator 检查 ===
        { name: 'navigator.userAgent',        get: () => navigator.userAgent },
        { name: 'navigator.platform',          get: () => navigator.platform },
        { name: 'navigator.language',          get: () => navigator.language },
        { name: 'navigator.hardwareConcurrency', get: () => navigator.hardwareConcurrency },
        { name: 'navigator.deviceMemory',      get: () => navigator.deviceMemory },
        { name: 'navigator.webdriver',         get: () => navigator.webdriver },
        { name: 'navigator.plugins.length',    get: () => navigator.plugins.length },
        { name: 'navigator.mimeTypes.length',  get: () => navigator.mimeTypes.length },
        
        // === document 检查 ===
        { name: 'document.cookie',             get: () => document.cookie?.substring(0, 50) },
        { name: 'document.hidden',             get: () => document.hidden },
        { name: 'document.visibilityState',    get: () => document.visibilityState },
        { name: 'document.readyState',         get: () => document.readyState },
        
        // === screen 检查 ===
        { name: 'screen.width',                get: () => screen.width },
        { name: 'screen.height',               get: () => screen.height },
        { name: 'screen.colorDepth',           get: () => screen.colorDepth },
        { name: 'screen.pixelDepth',           get: () => screen.pixelDepth },
        
        // === location 检查 ===
        { name: 'location.href',               get: () => location.href },
        { name: 'location.protocol',           get: () => location.protocol },
        
        // === 类型检查 ===
        { name: 'typeof window',               get: () => typeof window },
        { name: 'typeof document',             get: () => typeof document },
        { name: 'typeof navigator',            get: () => typeof navigator },
        { name: 'typeof document.all',         get: () => typeof document.all },  // 应为 'undefined'
        
        // === toString 检查 ===
        { name: 'window.toString()',           get: () => window.toString() },
        { name: 'navigator.toString()',        get: () => navigator.toString?.() },
        
        // === Function.toString 检查 ===
        { name: 'alert.toString()',            get: () => window.alert?.toString()?.substring(0, 50) },
        { name: 'setTimeout.toString()',       get: () => setTimeout.toString()?.substring(0, 50) },
        
        // === 环境泄漏检测 ===
        { name: 'process (Node.js leak)',      get: () => typeof process },
        { name: '__dirname (Node.js leak)',    get: () => typeof __dirname },
        { name: '__filename (Node.js leak)',   get: () => typeof __filename },
        { name: 'global (Node.js leak)',       get: () => typeof global },
    ];
    
    const results = {};
    for (const check of CHECKS) {
        try {
            const value = check.get();
            results[check.name] = {
                value: String(value),
                type: typeof value,
            };
        } catch (e) {
            results[check.name] = { error: e.message };
        }
    }
    
    console.log('[EnvDiff] Browser fingerprint:');
    console.log(JSON.stringify(results, null, 2));
    
    // 输出为文件（可选）
    return results;
})();
```

将此脚本在真实浏览器中运行一次，保存输出为 `browser_env.json`。在补环境后运行同样检查，用 Python 对比差异：

```python
# compare_env.py — 对比浏览器环境与补环境的差异
import json

def compare_env(browser_file, patched_file):
    with open(browser_file) as f:
        browser = json.load(f)
    with open(patched_file) as f:
        patched = json.load(f)
    
    diffs = []
    for key in browser:
        if key not in patched:
            diffs.append(f"[MISSING] {key}")
        elif browser[key].get('type') != patched[key].get('type'):
            diffs.append(f"[TYPE] {key}: browser={browser[key].get('type')} patched={patched[key].get('type')}")
        elif browser[key].get('value') != patched[key].get('value'):
            diffs.append(f"[VALUE] {key}:\n  browser: {browser[key].get('value')}\n  patched: {patched[key].get('value')}")
    
    for key in patched:
        if key not in browser:
            diffs.append(f"[EXTRA] {key} (not in browser)")
    
    if diffs:
        print(f"[FAIL] {len(diffs)} differences found:")
        for d in diffs:
            print(f"  {d}")
    else:
        print("[PASS] Environments match!")
    
    return len(diffs) == 0

if __name__ == "__main__":
    import sys
    compare_env(sys.argv[1], sys.argv[2])
```

---

## 更新的补环境决策矩阵

| 情况 | 方案 | 成本 |
|------|------|------|
| 只用到 navigator/document 基础属性 | 最小补环境（模板就够） | 10分钟 |
| 用到 Canvas/WebGL 指纹 | 补环境 + 真实指纹值（见上方 Canvas 模拟） | 30分钟 |
| 用到 AudioContext 指纹 | 补环境 + AudioContext stub（见上方） | 20分钟 |
| 用到 plugins/mimeTypes | 补环境 + 真实插件列表 | 15分钟 |
| 用到 WebRTC/Service Worker | 考虑切换到CDP桥 | — |
| 代码已收缩成纯函数 | 不要补环境，直接扣函数 | 5分钟 |
| 需要 document.all typeof | 无法完美模拟，用CDP桥 | — |
| 需要多个模块组合 | 使用模块化 EnvPatcher 加载器 | 按模块叠加 |
| 补环境成本 > 2小时 | 直接上CDP桥 | — |
