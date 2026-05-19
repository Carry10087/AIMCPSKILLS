/**
 * env_patch_v8.js — 同步 HTTP 代理
 * 
 * 关键改进：script.src setter 中用 execSync(curl) 做同步 HTTP，
 *          这样 callback 名天然匹配，不再需要预取+替换
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const execSync = require('child_process').execSync;

const BASE = __dirname;

// 命令行参数：--target-distance=N（Python OpenCV 计算出的缺口位置）
var EXTERNAL_TARGET = null;
process.argv.forEach(function(arg) {
    var m = arg.match(/^--target-distance=(\d+\.?\d*)$/);
    if (m) { EXTERNAL_TARGET = parseFloat(m[1]); console.error('[ARG] external target distance:', EXTERNAL_TARGET); }
});

// ================================================================
// 同步 HTTP GET
// ================================================================
function syncHttpGetStr(url) {
    try {
        var cmd = 'powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri \'' + url.replace(/'/g, "''") + '\' -UseBasicParsing -TimeoutSec 15 -Headers @{\'User-Agent\'=\'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\'}; Write-Output $r.Content } catch { Write-Output \'ERROR\' }"';
        var result = execSync(cmd, { encoding: 'utf-8', timeout: 20000, windowsHide: true });
        if (result.trim() === 'ERROR') throw new Error('PowerShell fetch failed');
        return result;
    } catch (e) {
        console.error('[syncHttpGet] FAIL:', url.substring(0, 80), e.message);
        return '';
    }
}

async function main() {
    console.error('=== Launching env_patch_v8 ===');

    process.on('uncaughtException', function(e) {
        console.error('[UNCAUGHT]', e.message);
        console.error('[UNCAUGHT-STACK]', e.stack);
    });
    process.on('unhandledRejection', function(reason) {
        console.error('[UNHANDLED-REJECTION]', reason && (reason.stack || reason.message || String(reason)));
    });

    var dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body><div id="floatCtn"></div></body></html>',
        { url: 'https://www.ishumei.com/trial/captcha.html', referrer: 'https://www.ishumei.com/', runScripts: 'dangerously' }
    );
    var w = dom.window, d = dom.window.document, nav = dom.window.navigator;

    // --- Navigator ---
    try {
        var navProps = {
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
            platform: 'Win32',
            language: 'zh-CN',
            languages: ['zh-CN', 'zh'],
            webdriver: false,
            hardwareConcurrency: 8,
            deviceMemory: 8,
            maxTouchPoints: 0,
            vendor: 'Google Inc.',
            vendorSub: '',
            productSub: '20030107',
            appCodeName: 'Mozilla',
            appName: 'Netscape',
            appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
            cookieEnabled: true,
            doNotTrack: null,
            onLine: true,
            pdfViewerEnabled: true,
            userAgentData: {
                brands: [{brand:'Chromium',version:'140'},{brand:'Google Chrome',version:'140'},{brand:'Not?A_Brand',version:'99'}],
                mobile: false,
                platform: 'Windows'
            }
        };
        Object.keys(navProps).forEach(function(k) {
            try { Object.defineProperty(nav, k, { value: navProps[k], configurable: true, enumerable: true }); } catch(e) {}
        });
    } catch(e) {}


    w.onerror = function(msg, src, line, col, err) {
        console.error('[WINDOW-ERROR]', msg, '| src:', src, '| line:', line);
        if (err && err.stack) console.error('[WINDOW-ERROR-STACK]', err.stack);
    };

    // 拦截所有 console 输出用于诊断
    var _origLog = console.error;
    w.console = { log: function(){}, warn: function(){}, error: function(){} };
    ['log','warn','error','info','debug'].forEach(function(lvl) {
        var orig = console[lvl] || console.error;
        w.console[lvl] = function() {
            var args = Array.prototype.slice.call(arguments);
            var msg = args.map(function(a){return typeof a==='string'?a:JSON.stringify(a);}).join(' ');
            if (msg.indexOf('[SDK')===-1 && msg.indexOf('[HOOK')===-1 && msg.indexOf('[SCRIPT')===-1 && msg.indexOf('[XHR')===-1 && msg.indexOf('[DIAG')===-1 && msg.indexOf('[TRY')===-1 && msg.indexOf('[STATUS')===-1) {
                console.error('[CONSOLE-' + lvl.toUpperCase() + ']', msg);
            }
            orig.apply(console, args);
        };
    });

    // --- Navigator ---
    var nv = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36', platform: 'Win32', webdriver: false, hardwareConcurrency: 8, deviceMemory: 8, language: 'zh-CN', cookieEnabled: true, onLine: true };
    for (var k in nv) { try { Object.defineProperty(nav, k, { value: nv[k], configurable: true }); } catch(e) {} }

    // --- Screen ---
    try { Object.defineProperty(w, 'screen', { value: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24 }, configurable: true }); } catch(e) {}
    try { w.innerWidth = 1920; w.innerHeight = 937; } catch(e) {}

    // --- Storage ---
    try {
        function mkStore() {
            var data = {};
            return { getItem: function(k){return data[k]||null;}, setItem: function(k,v){data[k]=String(v);}, removeItem: function(k){delete data[k];}, clear: function(){data={};}, get length(){return Object.keys(data).length;}, key: function(i){return Object.keys(data)[i]||null;} };
        }
        Object.defineProperty(w, 'localStorage', { value: mkStore(), configurable: true });
        Object.defineProperty(w, 'sessionStorage', { value: mkStore(), configurable: true });
    } catch(e) {}

    // --- Performance ---
    var t0 = Date.now();
    w.performance = { now: function(){return Date.now();}, timing: { navigationStart: t0-1000, loadEventEnd: t0 }, getEntriesByType: function(){return[];}, getEntriesByName: function(){return[];}, mark: function(){}, measure: function(){} };
    w.requestAnimationFrame = function(cb){return setTimeout(function(){cb(Date.now());},16);};
    w.cancelAnimationFrame = function(id){clearTimeout(id);};
    w.matchMedia = function(){return {matches:false,media:'',addListener:function(){},removeListener:function(){}};};
    w.getComputedStyle = function(){return {getPropertyValue:function(){return'';}};};

    // --- Events ---
    ['Event','CustomEvent','UIEvent','MouseEvent','TouchEvent','PointerEvent','KeyboardEvent','WheelEvent','FocusEvent'].forEach(function(name){
        if (!w[name]) w[name] = function(t,o){this.type=t;if(o)for(var k in o)this[k]=o[k];};
        if (w[name] && w[name].prototype) { w[name].prototype.preventDefault = function(){}; w[name].prototype.stopPropagation = function(){}; }
    });

    // --- atob/btoa ---
    if (!w.atob) w.atob = function(s){return Buffer.from(s,'base64').toString('binary');};
    if (!w.btoa) w.btoa = function(s){return Buffer.from(s,'binary').toString('base64');};

    // --- Image ---
    w.Image = function(w,h){this.width=w||0;this.height=h||0;this.src='';this.onload=null;this.onerror=null;this.complete=false;};

    // --- crypto ---
    try { w.crypto = { getRandomValues: function(arr){for(var i=0;i<arr.length;i++)arr[i]=Math.floor(Math.random()*256);return arr;}, subtle: {digest:function(){return Promise.resolve(new ArrayBuffer(32));}} }; } catch(e) {}

    // ================================================================
    // createElement 拦截 — 同步 HTTP 注入
    // ================================================================
    var origCE = d.createElement.bind(d);
    var intercepted = {};
    var regData = {};
    var state = { sdk: false, conf: false, reg: false, fv: false };

    function emitFverifyResult(url, result, source, error) {
        if (intercepted.finalEmitted) return;
        intercepted.finalEmitted = true;
        var payload = {
            status: result ? 'result' : 'captured',
            source: source || 'unknown',
            url: url,
            reg: regData
        };
        if (result) payload.result = result;
        if (error) payload.error = String(error && (error.message || error));
        process.stdout.write(JSON.stringify(payload) + '\n');
    }

    function notifyScriptLoaded(el, label) {
        setTimeout(function() {
            try { el.readyState = 'complete'; } catch(e) {}
            try {
                if (typeof el.onreadystatechange === 'function') {
                    console.error('[SCRIPT] manual readystatechange:', label);
                    el.onreadystatechange.call(el);
                }
            } catch(e1) {
                console.error('[SCRIPT] readystatechange error:', label, e1 && (e1.stack || e1.message) || e1);
            }
            try {
                if (typeof el.onload === 'function') {
                    console.error('[SCRIPT] manual onload:', label);
                    el.onload.call(el);
                }
            } catch(e2) {
                console.error('[SCRIPT] onload error:', label, e2 && (e2.stack || e2.message) || e2);
            }
        }, 100);
    }

    function dumpContextMethodDiagnostics(cap) {
        var interesting = [
            'saveFullPageData',
            'cellectFullPageData',
            'collectFullPageData',
            'getMouseAction',
            'getEncryptContent',
            'getResult',
            'sendRequest'
        ];
        var report = {
            timestamp: new Date().toISOString(),
            keyCount: 0,
            functionCount: 0,
            methods: {},
            sampleKeys: []
        };
        for (var key in cap) {
            report.keyCount++;
            if (report.sampleKeys.length < 80) report.sampleKeys.push(key);
            if (typeof cap[key] === 'function') report.functionCount++;
        }
        interesting.forEach(function(name) {
            report.methods[name] = typeof cap[name];
        });
        try {
            fs.writeFileSync(path.join(BASE, 'captcha_context_methods.json'), JSON.stringify(report, null, 2));
        } catch(e) {
            console.error('[DIAG] write captcha_context_methods.json error:', e.message);
        }
        console.error('[DIAG] context methods:', JSON.stringify(report.methods));
    }

    function saveTrajectoryDiagnostics(totalDistance, steps, trajectory) {
        var sum = 0;
        var min = Infinity;
        var max = -Infinity;
        var zeroCount = 0;
        var negativeCount = 0;
        for (var i = 0; i < trajectory.length; i++) {
            var d = Number(trajectory[i]) || 0;
            sum += d;
            if (d < min) min = d;
            if (d > max) max = d;
            if (d === 0) zeroCount++;
            if (d < 0) negativeCount++;
        }
        var report = {
            timestamp: new Date().toISOString(),
            targetDistance: totalDistance,
            requestedSteps: steps,
            actualSteps: trajectory.length,
            totalDelta: Math.round(sum * 10) / 10,
            minDelta: min === Infinity ? 0 : min,
            maxDelta: max === -Infinity ? 0 : max,
            zeroDeltaCount: zeroCount,
            negativeDeltaCount: negativeCount,
            deltas: trajectory
        };
        try {
            fs.writeFileSync(path.join(BASE, 'trajectory_diagnostics.json'), JSON.stringify(report, null, 2));
        } catch(e) {
            console.error('[DIAG] write trajectory_diagnostics.json error:', e.message);
        }
        console.error('[DIAG] trajectory:', JSON.stringify({
            steps: report.actualSteps,
            totalDelta: report.totalDelta,
            minDelta: report.minDelta,
            maxDelta: report.maxDelta,
            zeroDeltaCount: report.zeroDeltaCount,
            negativeDeltaCount: report.negativeDeltaCount
        }));
    }

    d.createElement = function(tagName, options) {
        var el = origCE(tagName, options);

        // 覆盖 getBoundingClientRect — jsdom 无 CSS 布局，所有元素尺寸为 0
        // SDK 需要这些值来计算滑块目标位置
        if (el && typeof el.getBoundingClientRect === 'function') {
            el.getBoundingClientRect = function() {
                return { top: 0, left: 0, bottom: 150, right: 300, width: 300, height: 150 };
            };
        }
        if (el && typeof el.getClientRects === 'function') {
            el.getClientRects = function() {
                return [{ top: 0, left: 0, bottom: 150, right: 300, width: 300, height: 150 }];
            };
        }
        // offset 属性也需要有效值
        try { Object.defineProperty(el, 'offsetWidth', {get:function(){return 300;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'offsetHeight', {get:function(){return 150;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'offsetLeft', {get:function(){return 0;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'offsetTop', {get:function(){return 0;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'clientWidth', {get:function(){return 300;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'clientHeight', {get:function(){return 150;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'scrollWidth', {get:function(){return 300;},configurable:true}); } catch(e) {}
        try { Object.defineProperty(el, 'scrollHeight', {get:function(){return 150;},configurable:true}); } catch(e) {}

        // 处理 link 标签 (CSS 加载)
        if (tagName.toLowerCase() === 'link') {
            var _href = '';
            Object.defineProperty(el, 'href', {
                get: function(){ return _href; },
                set: function(url) {
                    _href = url;
                    var s = String(url);
                    if (s.indexOf('.css') !== -1) {
                        console.error('[LINK] css href set:', s.substring(s.length-50));
                        // CSS 不实际加载，直接触发 onload
                        setTimeout(function() {
                            try {
                                if (typeof el.onload === 'function') {
                                    console.error('[LINK] manual onload: css');
                                    el.onload.call(el);
                                }
                            } catch(e) { console.error('[LINK] onload err:', e.message); }
                        }, 50);
                    }
                }
            });
        }

        if (tagName.toLowerCase() === 'script') {
            var _src = '';
            Object.defineProperty(el, 'src', {
                get: function(){return _src;},
                set: function(url) {
                    _src = url;
                    var s = String(url);
                    console.error('[SCRIPT]', s.substring(0, 160));

                    // fverify 检测 (JSONP 方式) — 同步获取响应看 PASS/REJECT
                    if (s.indexOf('fverify') !== -1 && intercepted.fverifyUrl === undefined) {
                        console.error('\n========================================');
                        console.error('[FVERIFY JSONP CAPTURED!]');
                        console.error('[FVERIFY] Full URL:', s);
                        intercepted.fverifyUrl = s;
                        state.fv = true;
                        fs.writeFileSync(path.join(BASE, 'fverify_url.txt'), s);
                        var fvResp = null;
                        var fvError = null;
                        // 同步获取 fverify 响应
                        try {
                            var fvBody = syncHttpGetStr(s);
                            var fvMatch = fvBody.match(/\{[^]*\}/);
                            if (fvMatch) {
                                fvResp = JSON.parse(fvMatch[0]);
                                console.error('[FVERIFY RESPONSE] code:', fvResp.code, 'message:', fvResp.message, 'riskLevel:', fvResp.riskLevel);
                                fs.writeFileSync(path.join(BASE, 'fverify_response.txt'), JSON.stringify(fvResp, null, 2));
                            }
                        } catch(e) {
                            fvError = e;
                            console.error('[FVERIFY] fetch response err:', e.message);
                        }
                        emitFverifyResult(s, fvResp, 'script_jsonp', fvError);
                        console.error('========================================');
                    }

                    // 同步 HTTP 代理 conf
                    if (s.indexOf('/ca/v1/conf') !== -1 && s.indexOf('callback=') !== -1) {
                        console.error('[SCRIPT] syncing conf...');
                        var body = syncHttpGetStr(s);
                        console.error('[SCRIPT] conf body:', body.substring(0, 60));
                        // 解析 conf 响应找到额外 JS 文件
                        var jsonMatch = body.match(/\{[^]*\}/);
                        if (jsonMatch) {
                            try {
                                var confData = JSON.parse(jsonMatch[0]);
                                var detail = confData.detail || confData;
                                console.error('[CONF DATA] js:', JSON.stringify(detail.js));
                                console.error('[CONF DATA] css:', JSON.stringify(detail.css));
                                console.error('[CONF DATA] domains:', JSON.stringify(detail.domains));
                            } catch(e2) {
                                console.error('[CONF PARSE]', e2.message);
                            }
                        }
                        if (body) {
                            el.textContent = body; state.conf = true;
                            // 保存完整响应到文件供分析
                            fs.writeFileSync(path.join(BASE, 'conf_response.txt'), body);
                            notifyScriptLoaded(el, 'conf');
                        }
                        return;
                    }
                    // 同步 HTTP 代理 register
                    if (s.indexOf('/ca/v1/register') !== -1 && s.indexOf('callback=') !== -1) {
                        console.error('[SCRIPT] syncing register...');
                        var body2 = syncHttpGetStr(s);
                        console.error('[SCRIPT] reg body:', body2.substring(0, 60));
                        if (body2) {
                            el.textContent = body2;
                            notifyScriptLoaded(el, 'register');
                            state.reg = true;
                            var m = body2.match(/\{.*\}/);
                            if (m) {
                                var dd = JSON.parse(m[0]); var dt2 = dd.detail || dd;
                                regData = { rid: dt2.rid, k: dt2.k, bg: dt2.bg, fg: dt2.fg };
                                console.error('[REG DATA] rid:', regData.rid);
                                // 保存完整 register 响应
                                fs.writeFileSync(path.join(BASE, 'register_response.txt'), body2);
                            }
                        }
                        return;
                    }
                    // 注入 captcha-sdk (v1.0.4-206) — 用 w.eval 替代 textContent
                    // textContent 方式在 jsdom 中导致 prototype 丢失
                    if (s.indexOf('captcha-sdk') !== -1) {
                        var sdkCode = fs.readFileSync(path.join(BASE, 'sdk_v104_206.js'), 'utf-8');
                        el.textContent = ''; // 空脚本，不执行
                        state.sdk = true;
                        console.error('[SCRIPT] sdk v1.0.4-206 injecting via w.eval...');
                        // 在 window 上下文中 eval SDK 代码
                        try {
                            var ret = dom.window.eval(sdkCode);
                            console.error('[SCRIPT] sdk eval returned:', typeof ret);
                            console.error('[SCRIPT] SMCaptcha:', typeof dom.window.SMCaptcha);
                            if (typeof dom.window.SMCaptcha === 'function') {
                                var ppk = [];
                                for (var ppkk in dom.window.SMCaptcha.prototype) ppk.push(ppkk);
                                console.error('[SCRIPT] SMCaptcha.prototype methods:', JSON.stringify(ppk));
                            }
                        } catch(e) {
                            console.error('[SCRIPT] sdk eval ERROR:', e.message);
                        }
                        notifyScriptLoaded(el, 'captcha-sdk');
                        return;
                    }
                    // 跳过指纹
                    if (s.indexOf('fp.min.js') !== -1) { el.textContent = ''; notifyScriptLoaded(el, 'fp.min.js'); return; }
                }
            });
        }

        if (tagName.toLowerCase() === 'canvas') {
            el.width = 300; el.height = 150;
            el.getContext = function(type) {
                if (type === '2d') {
                    var ctx = {};
                    ['save','restore','beginPath','closePath','moveTo','lineTo','arc','rect','fillRect','strokeRect','clearRect','fill','stroke','clip','drawImage','fillText','strokeText','putImageData'].forEach(function(f){ctx[f]=function(){};});
                    ctx.measureText = function(t){return {width:t.length*7};};
                    ctx.getImageData = function(x,y,w,h){return {data:new Uint8ClampedArray(w*h*4),width:w,height:h};};
                    ctx.toDataURL = function(){return 'data:image/png;base64,iVBORw0KGgo=';};
                    ctx.isPointInPath = function(){return false;};
                    return ctx;
                }
                if (type === 'webgl' || type === 'experimental-webgl') {
                    var gl = {drawingBufferWidth:300,drawingBufferHeight:150};
                    var p={0x1F00:'WebKit',0x1F01:'WebKit WebGL',0x9245:'WebGL',0x9246:'ANGLE (Intel, Intel(R) UHD Graphics 620)',0x0B46:8,0x0D33:16,0x0D56:8,0x821B:'WebGL 1.0',0x821C:'WebGL GLSL ES 1.0',0x9039:4096,0x8DF8:16};
                    gl.getParameter=function(k){return p[k]!==undefined?p[k]:null;};
                    gl.getExtension=function(){return null;};
                    gl.getSupportedExtensions=function(){return[];};
                    gl.isContextLost=function(){return false;};
                    ['createBuffer','createProgram','createShader','shaderSource','compileShader','attachShader','linkProgram','useProgram','bindBuffer','bufferData','clear','viewport','enableVertexAttribArray','vertexAttribPointer','drawArrays'].forEach(function(f){gl[f]=function(){};});
                    return gl;
                }
                return null;
            };
            el.toDataURL = function(){return 'data:image/png;base64,iVBORw0KGgo=';};
            el.toBlob = function(cb){setTimeout(function(){cb&&cb({size:100});},0);};
        }

        return el;
    };

    // ================================================================
    // XHR 拦截
    // ================================================================
    var XHR = w.XMLHttpRequest;
    XHR.prototype.open = new Proxy(XHR.prototype.open, {
        apply: function(target, self, args) {
            self._url = args[1]; self._method = args[0];
            return Reflect.apply(target, self, args);
        }
    });
    XHR.prototype.send = new Proxy(XHR.prototype.send, {
        apply: function(target, self, args) {
            var url = self._url || '';
            console.error('[XHR]', self._method, url.substring(0, 100));
            if (url.indexOf('fverify') !== -1 || url.indexOf('/ca/v2/verify') !== -1) {
                console.error('\n============== [FVERIFY!] ==============');
                console.error('URL:', url);
                intercepted.fverifyUrl = url;
                emitFverifyResult(url, null, 'xhr');
            }
            return Reflect.apply(target, self, args);
        }
    });

    // ================================================================
    // SMCaptcha 构造器拦截 — 诊断 _captcha 为什么是空的
    // ================================================================
    (function() {
        var _SMCaptcha = undefined;
        Object.defineProperty(w, 'SMCaptcha', {
            get: function() { return _SMCaptcha; },
            set: function(val) {
                console.error('[HOOK] window.SMCaptcha defined, type:', typeof val);
                if (typeof val === 'function') {
                    var Orig = val;
                    var Wrapped = function() {
                        console.error('[HOOK] new SMCaptcha() called with', arguments.length, 'args');
                        // 导出 config 到文件
                        fs.writeFileSync(path.join(BASE, 'config_snapshot.json'), JSON.stringify(arguments[0], null, 2));
                        console.error('[HOOK] config saved to config_snapshot.json');
                        console.error('[HOOK] config apiConf:', typeof arguments[0].apiConf);
                        if (arguments[0].apiConf) {
                            console.error('[HOOK] apiConf keys:', Object.keys(arguments[0].apiConf));
                            console.error('[HOOK] apiConf.css:', arguments[0].apiConf.css);
                            console.error('[HOOK] apiConf.js:', arguments[0].apiConf.js);
                            console.error('[HOOK] apiConf.detail:', arguments[0].apiConf.detail ? Object.keys(arguments[0].apiConf.detail) : 'none');
                        }
                        try {
                            var inst = Orig.apply(this, arguments);
                            console.error('[HOOK] SMCaptcha constructor returned, keys:', JSON.stringify(Object.keys(this)));
                            console.error('[HOOK] this._captcha:', typeof this._captcha);
                            if (typeof this._captcha === 'function') {
                                var capProto = this._captcha.prototype;
                                var ownKeys = Object.keys(capProto||{}).slice(0,30);
                                var allKeys = [];
                                for (var kk in capProto) allKeys.push(kk);
                                console.error('[HOOK] _captcha is FUNCTION');
                                console.error('[HOOK] _captcha.prototype own:', JSON.stringify(ownKeys));
                                console.error('[HOOK] _captcha.prototype all:', JSON.stringify(allKeys.slice(0,30)));
                                console.error('[HOOK] _captcha.prototype length:', allKeys.length);
                            }
                            return inst;
                        } catch(e) {
                            console.error('[HOOK] SMCaptcha constructor THREW:', e.message);
                            console.error('[HOOK] stack:', e.stack);
                            throw e;
                        }
                    };
                    Wrapped.prototype = Orig.prototype;
                    _SMCaptcha = Wrapped;
                } else {
                    _SMCaptcha = val;
                }
                console.error('[HOOK] SMCaptcha set complete');
            },
            configurable: true,
            enumerable: true
        });
    })();

    // ================================================================
    // 加载 smcp — 也用 w.eval 保证在 window 上下文中执行
    // ================================================================
    console.error('\n=== LOAD smcp via w.eval ===');
    var smcpCode = fs.readFileSync(path.join(BASE, 'smcp.min.js'), 'utf-8');
    try {
        w.eval(smcpCode);
        console.error('initSMCaptcha:', typeof w.initSMCaptcha);
    } catch(e) {
        console.error('[SMCP EVAL ERROR]', e.message);
        console.error('[SMCP STACK]', e.stack && e.stack.substring(0, 300));
    }

    // ================================================================
    // 调用
    // ================================================================
    if (typeof w.initSMCaptcha === 'function') {
        console.error('\n=== CALL initSMCaptcha ===');
        try {
            w.initSMCaptcha({
                organization: 'RlokQwRlVjUrTUlkIqOg',
                product: 'float',
                mode: 'slide',
                appendTo: 'floatCtn',
            }, function(inst) {
                console.error('\n[INSTANCE READY] own keys:', JSON.stringify(Object.keys(inst)));
                w._inst = inst;

                // 深度 dump
                var allKeys = [];
                for (var k in inst) { allKeys.push(k); }
                console.error('[DIAG] inst for..in keys:', JSON.stringify(allKeys));

                if (inst._captcha) {
                    var capType = typeof inst._captcha;
                    console.error('[DIAG] _captcha type:', capType);
                    if (capType === 'function') {
                        console.error('[DIAG] _captcha.prototype keys:', JSON.stringify(Object.keys(inst._captcha.prototype||{})));
                        console.error('[DIAG] _captcha.prototype all:', JSON.stringify((function(){var k=[];for(var kk in this)k.push(kk);return k;}).call(inst._captcha.prototype)));
                    }
                }

                // 代理关键方法
                ['verify','show','getValidate'].forEach(function(mn) {
                    if (typeof inst[mn] === 'function') {
                        var orig = inst[mn].bind(inst);
                        inst[mn] = function() {
                            console.error('[PROXY-' + mn + '] called');
                            console.error('[PROXY-' + mn + '] _captcha type:', typeof inst._captcha);
                            if (typeof inst._captcha === 'object') {
                                // dump _captcha methods
                                var capMethods = [];
                                for (var cm in inst._captcha) {
                                    if (typeof inst._captcha[cm] === 'function') capMethods.push(cm);
                                }
                                console.error('[PROXY-' + mn + '] _captcha methods:', JSON.stringify(capMethods));
                            }
                            return orig.apply(this, arguments);
                        };
                    }
                });

                if (inst._config) {
                    console.error('[DIAG] _config:', JSON.stringify(Object.keys(inst._config)));
                }

                // 检查 DOM
                var ctn = d.getElementById('floatCtn');
                console.error('[DIAG] floatCtn innerHTML len:', ctn ? ctn.innerHTML.length : 'NULL');
                console.error('[DIAG] floatCtn children:', ctn ? ctn.children.length : 'NULL');
                if (ctn && ctn.innerHTML.length > 10) {
                    console.error('[DIAG] floatCtn html:', ctn.innerHTML.substring(0, 500));
                }

                // 自动触发
                setTimeout(function() {
                    console.error('\n=== TRIGGER sequence ===');
                    var ctn2 = d.getElementById('floatCtn');
                    console.error('[DIAG] floatCtn after wait:', ctn2 ? ctn2.innerHTML.length : 'NULL');

                    // 先试 show (show 可能会初始化 _captcha)
                    if (typeof inst.show === 'function') {
                        console.error('[TRY] inst.show()...');
                        try { inst.show(); console.error('[TRY] inst.show() OK, _captcha type after:', typeof inst._captcha); }
                        catch(e) { console.error('[TRY] inst.show() ERR:', e.message); }
                    }
                    if (inst._captcha && typeof inst._captcha.show === 'function') {
                        console.error('[TRY] _captcha.show()...');
                        try { inst._captcha.show(); console.error('[TRY] _captcha.show() OK'); }
                        catch(e) { console.error('[TRY] _captcha.show() ERR:', e.message); }
                    }

                    // 等 DOM 渲染
                    setTimeout(function() {
                        var ctn3 = d.getElementById('floatCtn');
                        console.error('[DIAG] floatCtn after show:', ctn3 ? ctn3.innerHTML.length : 'NULL');

                        // 试 verify
                        if (typeof inst.verify === 'function') {
                            console.error('[TRY] inst.verify()...');
                            try { inst.verify(); console.error('[TRY] inst.verify() OK'); }
                            catch(e) { console.error('[TRY] inst.verify() ERR:', e.message.substr(0, 100)); }
                        }

                        // 等滑块 UI 出现后模拟拖动
                        setTimeout(function() {
                            simulateSlideDrag();
                        }, 1500);
                    }, 2000);
                }, 3000);

                // ============================================================
                    // 绕过 DOM 事件，直接调用 _captcha 内部 handler 链
                    // ============================================================
                    function simulateSlideDrag() {
                        var cap = inst._captcha;
                        if (!cap || typeof cap !== 'object') {
                            console.error('[DRAG] _captcha not ready');
                            return;
                        }

                        // 初步设置 _data（blockWidth 由 SDK 根据图片计算，不覆盖）
                        if (cap._data) {
                            cap._data.firstRootDomWidth = 300;
                            cap._data.trueWidth = 260;
                            cap._data.trueHeight = 150;
                            cap._data.slideWidth = 260;
                            cap._data.beforeResizeWidth = 260;
                            cap._data.trueUnit = 'px';
                            cap._data.registerApiInvalid = false;
                            console.error('[DRAG] _data layout dimensions forcibly set');
                        }
                    dumpContextMethodDiagnostics(cap);

                    // 检查关键方法
                    var hasStart = typeof cap.startHandler === 'function';
                    var hasMove = typeof cap.moveHandler === 'function';
                    var hasEnd = typeof cap.endHandler === 'function';
                    var hasGetEncrypt = typeof cap.getEncryptContent === 'function';
                    var hasGetResult = typeof cap.getResult === 'function';
                    var hasSendReq = typeof cap.sendRequest === 'function';
                    console.error('[DRAG] startHandler:', hasStart, 'moveHandler:', hasMove, 'endHandler:', hasEnd);
                    console.error('[DRAG] getEncryptContent:', hasGetEncrypt, 'getResult:', hasGetResult, 'sendRequest:', hasSendReq);

                    if (!hasStart || !hasMove || !hasEnd) {
                        console.error('[DRAG] Missing handler methods, trying getEncryptContent directly...');
                        if (hasGetEncrypt) {
                            try {
                                var enc = cap.getEncryptContent();
                                console.error('[DRAG] getEncryptContent result:', typeof enc, JSON.stringify(enc||{}).substring(0, 500));
                            } catch(e) { console.error('[DRAG] getEncryptContent err:', e.message); }
                        }
                        if (hasGetResult) {
                            try {
                                var res = cap.getResult();
                                console.error('[DRAG] getResult:', JSON.stringify(res||{}).substring(0, 500));
                            } catch(e) { console.error('[DRAG] getResult err:', e.message); }
                        }
                        if (hasSendReq) {
                            try {
                                cap.sendRequest();
                                console.error('[DRAG] sendRequest() called');
                            } catch(e) { console.error('[DRAG] sendRequest err:', e.message); }
                        }
                        return;
                    }

                    function makeEvent(x, y, type) {
                        var now = Date.now();
                        var _target = slideBtnEl || {className:'',id:''};
                        return {
                            type: type,
                            clientX: x, clientY: y,
                            pageX: x, pageY: y,
                            screenX: x + 100, screenY: y + 100,
                            offsetX: x, offsetY: y,
                            movementX: 0, movementY: 0,
                            target: _target,
                            currentTarget: _target,
                            srcElement: _target,
                            button: 0,
                            buttons: type === 'mouseup' ? 0 : 1,
                            which: 1,
                            timeStamp: now,
                            bubbles: true,
                            cancelable: true,
                            isTrusted: true,
                            preventDefault: function(){},
                            stopPropagation: function(){},
                            stopImmediatePropagation: function(){},
                            touches: type.indexOf('touch')===0 ? [{identifier:1, clientX:x, clientY:y, pageX:x, pageY:y, screenX:x+100, screenY:y+100, radiusX:10, radiusY:10, rotationAngle:0, force:0.5}] : undefined,
                            changedTouches: type==='touchend' ? [{identifier:1, clientX:x, clientY:y, pageX:x, pageY:y, screenX:x+100, screenY:y+100}] : undefined,
                            targetTouches: type.indexOf('touch')===0 ? [{identifier:1, clientX:x, clientY:y, pageX:x, pageY:y}] : undefined,
                            detail: 0,
                            view: w
                        };
                    }

                    // ============================================================
                    // 完整事件流监控 — 拦截 _captcha 所有 handler
                    // ============================================================
                    var cap = inst._captcha;
                    if (cap && typeof cap === 'object') {
                        var handlers = ['startHandler','moveHandler','endHandler','fpMouseClickHandler',
                                       'overHandler','outHandler','floatOverHandler','mouseLeftClick','mouseRightClick'];
                        var interceptedCallCount = {};
                        handlers.forEach(function(hn) {
                            if (typeof cap[hn] === 'function') {
                                var origFn = cap[hn];
                                cap[hn] = function() {
                                    interceptedCallCount[hn] = (interceptedCallCount[hn]||0) + 1;
                                    var ae = arguments[0] || {};
                                    if (hn === 'moveHandler') {
                                        // 每次 move 只记录 delta（避免刷屏）
                                        console.error('[EVENT-' + hn + ' #' + interceptedCallCount[hn] + '] x=' +
                                            ae.clientX + ' y=' + ae.clientY + ' type=' + ae.type);
                                    } else {
                                        console.error('[EVENT-' + hn + '] type=' + ae.type +
                                            ' clientX=' + ae.clientX + ' clientY=' + ae.clientY +
                                            ' buttons=' + ae.buttons + ' which=' + ae.which);
                                    }
                                    return origFn.apply(this, arguments);
                                };
                            }
                        });
                        console.error('[DRAG] hooked handlers:', JSON.stringify(Object.keys(interceptedCallCount)));
                    }

                    // Step 0: SDK 上下文初始化 — saveFullPageData / cellectFullPageData
                    console.error('[DRAG] calling saveFullPageData...');
                    try {
                        if (typeof cap.saveFullPageData === 'function') {
                            cap.saveFullPageData();
                            console.error('[DRAG] saveFullPageData OK');
                        }
                    } catch(e) { console.error('[DRAG] saveFullPageData err:', e.message); }
                    try {
                        if (typeof cap.cellectFullPageData === 'function') {
                            cap.cellectFullPageData();
                            console.error('[DRAG] cellectFullPageData OK');
                        }
                    } catch(e) { console.error('[DRAG] cellectFullPageData err:', e.message); }

                    // 重新强制设置维度（saveFullPageData 可能覆盖）
                    if (cap._data) {
                        cap._data.slideWidth = 260;
                        cap._data.trueWidth = 260;
                        cap._data.firstRootDomWidth = 300;
                        console.error('[DRAG] _data dimensions re-forced after init');
                    }

                    // ================================================================
                    // 缺口位置检测：调用 Python OpenCV 子进程
                    // ================================================================
                    var targetX;
                    if (EXTERNAL_TARGET !== null) {
                        targetX = EXTERNAL_TARGET;
                        console.error('[DRAG] targetX from external: ' + targetX);
                    } else {
                        // 尝试用 Python OpenCV 检测真实缺口
                        var bgUrl = '';
                        var fgUrl = '';
                        try {
                            var rd = cap._data.registerData;
                            if (rd && rd.bg && rd.fg && rd.domains && rd.domains.length > 0) {
                                var cdn = 'https://' + rd.domains[0];
                                bgUrl = cdn + rd.bg;
                                fgUrl = cdn + rd.fg;
                            }
                        } catch(e) {}
                        if (bgUrl && fgUrl) {
                            try {
                                var pyScript = path.join(BASE, 'gap_detect.py');
                                var tmpErr = path.join(BASE, 'gap_stderr.tmp');
                                var pyCmd = 'python "' + pyScript + '" "' + bgUrl + '" "' + fgUrl + '" 260 2>"' + tmpErr + '"';
                                console.error('[DRAG] calling gap_detect.py...');
                                var gapLine = execSync(pyCmd, { encoding: 'utf-8', timeout: 30000, windowsHide: true }).trim();
                                try {
                                    var stderrTxt = fs.readFileSync(tmpErr, 'utf-8').trim();
                                    if (stderrTxt) {
                                        var seLines = stderrTxt.split(/[\r\n]+/);
                                        for (var sli = 0; sli < seLines.length; sli++) {
                                            if (seLines[sli]) console.error('[DRAG] gap_detect.py: ' + seLines[sli]);
                                        }
                                    }
                                } catch(ee) {}
                                var parsed = parseInt(gapLine);
                                if (!isNaN(parsed) && parsed > 0 && parsed < 260) {
                                    targetX = parsed;
                                    console.error('[DRAG] Python OpenCV gap: ' + targetX + 'px');
                                } else {
                                    throw new Error('Invalid gap: ' + gapLine);
                                }
                            } catch(e) {
                                console.error('[DRAG] gap_detect.py FAILED:', e.message);
                                // 回退到估算
                                var slideWidth = cap._data.slideWidth || 260;
                                var blockWidth = cap._data.blockWidth;
                                if (blockWidth === undefined || blockWidth === null || blockWidth === 48 || blockWidth <= 0 || blockWidth > 100) {
                                    var regBgW = (cap._data.registerData && cap._data.registerData.bg_width) || 600;
                                    blockWidth = slideWidth * 80 / regBgW;
                                }
                                targetX = slideWidth - blockWidth;
                                console.error('[DRAG] fallback targetX: ' + targetX.toFixed(2));
                            }
                        } else {
                            // 无图片 URL，估算
                            var slideWidth = cap._data.slideWidth || 260;
                            var blockWidth = cap._data.blockWidth;
                            if (blockWidth === undefined || blockWidth === null || blockWidth === 48 || blockWidth <= 0 || blockWidth > 100) {
                                var regBgW = (cap._data.registerData && cap._data.registerData.bg_width) || 600;
                                blockWidth = slideWidth * 80 / regBgW;
                            }
                            targetX = slideWidth - blockWidth;
                            console.error('[DRAG] no image URL, fallback targetX: ' + targetX.toFixed(2));
                        }
                    }
                    var steps = 35 + Math.floor(Math.random() * 10);
                    var trajectory = generateTrajectory(targetX, steps);
                    saveTrajectoryDiagnostics(targetX, steps, trajectory);
                    var totalTime = Math.max(800, Math.floor(targetX * 22) + Math.floor(Math.random() * 400));

                    // Step 1: startHandler — send both mouse and touch start
                    // 关键：找到滑块按钮 DOM 元素作为 target
                    var slideBtnEl = null;
                    try {
                        var ctn = d.getElementById('floatCtn');
                        if (ctn) {
                            // 尝试各种常见选择器找到滑块按钮
                            var candidates = ctn.querySelectorAll('[class*="slid"],[class*="btn"],[class*="button"],[class*="drag"]');
                            for (var ci = 0; ci < candidates.length; ci++) {
                                var c = candidates[ci];
                                var cn = c.className || '';
                                if (cn.length > 0 && cn.length < 80) {
                                    console.error('[DRAG] dom candidate:', JSON.stringify({tag:c.tagName,id:c.id,cls:cn,rect:{w:c.offsetWidth,h:c.offsetHeight}}));
                                }
                            }
                            // 取第一个候选作为 target
                            if (candidates.length > 0) slideBtnEl = candidates[0];
                        }
                    } catch(e) { console.error('[DRAG] btn search err:', e.message); }
                    console.error('[DRAG] slideBtnEl found:', !!slideBtnEl, slideBtnEl ? (slideBtnEl.tagName + '.' + slideBtnEl.className).substring(0,60) : 'NONE');
                    console.error('[DRAG] startHandler...');
                    try { cap.startHandler(makeEvent(10, 75, 'mousedown')); }
                    catch(e) { console.error('[DRAG] mousedown err:', e.message); }
                    try { cap.startHandler(makeEvent(10, 75, 'touchstart')); }
                    catch(e) {}
                    console.error('[DRAG] startHandler OK');

                    // Step 2: move steps — 可变间隔（非均匀步长）
                    var cumX = 0;
                    var startX = 10;
                    var startY = 75;
                    // 预计算可变时间间隔
                    var baseStepTime = totalTime / trajectory.length;
                    var stepDelays = [];
                    var accTime = 0;
                    for (var td = 0; td < trajectory.length; td++) {
                        // 每步间隔加 ±25% 随机抖动，模拟人类不均匀节奏
                        var jitter = (Math.random() - 0.5) * baseStepTime * 0.5;
                        var stepTime = Math.max(8, baseStepTime + jitter);
                        accTime += stepTime;
                        stepDelays.push(Math.floor(accTime));
                    }
                    for (var si = 0; si < trajectory.length; si++) {
                        cumX += trajectory[si];
                        var curX = startX + cumX;
                        var curY = startY + (Math.random() - 0.5) * 10;
                        var delay = stepDelays[si];
                        (function(cx, cy, idx) {
                            setTimeout(function() {
                                try { cap.moveHandler(makeEvent(cx, cy, 'mousemove')); }
                                catch(e2) {}
                                try { cap.moveHandler(makeEvent(cx, cy, 'touchmove')); }
                                catch(e2) {}
                                if (idx === trajectory.length - 1) {
                                    console.error('[DRAG] final X:', cx);
                                    setTimeout(function() {
                                         // dump getMouseAction 完整结构
                                         if (typeof cap.getMouseAction === 'function') {
                                             try {
                                                 var ma = cap.getMouseAction();
                                                 if (typeof ma === 'object' && ma !== null) {
                                                     var maKeys = Object.keys(ma);
                                                     console.error('[DRAG] getMouseAction keys:', JSON.stringify(maKeys));
                                                     for (var mai = 0; mai < maKeys.length; mai++) {
                                                         var mk = maKeys[mai], mv = ma[mk];
                                                         var mvs = typeof mv === 'object' ? JSON.stringify(mv).substring(0, 200) : String(mv);
                                                         console.error('[DRAG]   ' + mk + ': ' + typeof mv + ' = ' + mvs);
                                                     }
                                                     try {
                                                         var safeCopy = {};
                                                         for (var si = 0; si < maKeys.length; si++) {
                                                             var sk = maKeys[si];
                                                             try { safeCopy[sk] = ma[sk]; } catch(e) {}
                                                         }
                                                         fs.writeFileSync(path.join(BASE, 'mouse_action_dump.json'), JSON.stringify(safeCopy, null, 2));
                                                         console.error('[DRAG] mouse_action_dump.json saved');
                                                     } catch(ee) { console.error('[DRAG] save ma failed:', ee.message); }
                                                 }
                                             } catch(e0) { console.error('[DRAG] getMouseAction err:', e0.message); }
                                         }

                                         // 关键：dump _data 看 selectData 是否被填充
                                          try {
                                              console.error('[DRAG] _data.selectData:', JSON.stringify(cap._data ? cap._data.selectData : 'NO _data'));
                                              console.error('[DRAG] _data.selectPosData:', JSON.stringify(cap._data ? cap._data.selectPosData : 'none'));
                                              console.error('[DRAG] _isMoving:', cap._isMoving);
                                              console.error('[DRAG] _currentStatus:', cap._currentStatus);
                                              if (cap._data) {
                                                  var dataKeys = Object.keys(cap._data);
                                                  console.error('[DRAG] _data keys:', JSON.stringify(dataKeys));
                                                  // dump each non-empty _data property
                                                  dataKeys.forEach(function(dk) {
                                                      var dv = cap._data[dk];
                                                      if (typeof dv === 'object' && dv !== null && Object.keys(dv).length > 0) {
                                                          console.error('[DRAG]   _data.' + dk + ': ' + JSON.stringify(dv).substring(0, 300));
                                                      } else if (typeof dv === 'string' && dv.length > 0) {
                                                          console.error('[DRAG]   _data.' + dk + ': ' + dv.substring(0, 300));
                                                      } else if (typeof dv !== 'object') {
                                                          console.error('[DRAG]   _data.' + dk + ': ' + String(dv));
                                                      }
                                                  });
                                              }
                                          } catch(e) {};

                                         // 尝试调用 saveEventList 看返回值
                                         if (typeof cap.saveEventList === 'function') {
                                             try {
                                                 var sel = cap.saveEventList();
                                                 console.error('[DRAG] saveEventList() returns:', typeof sel);
                                                 if (typeof sel === 'object') {
                                                     console.error('[DRAG] saveEventList keys:', JSON.stringify(Object.keys(sel||{})));
                                                     console.error('[DRAG] saveEventList:', JSON.stringify(sel).substring(0, 500));
                                                 } else {
                                                     console.error('[DRAG] saveEventList:', String(sel).substring(0, 300));
                                                 }
                                             } catch(e) { console.error('[DRAG] saveEventList err:', e.message); }
                                         }
                                         try { cap.endHandler(makeEvent(cx, cy, 'mouseup')); }
                                          catch(e3) {}
                                          try { cap.endHandler(makeEvent(cx, cy, 'touchend')); }
                                          catch(e3) {}
                                          // send click
                                          try { cap.fpMouseClickHandler(makeEvent(cx, cy, 'click')); }
                                          catch(e3) {}
                                          console.error('[DRAG] endHandler OK');

                                        // 等 SDK 处理后检查结果
                                        setTimeout(function() {
                                            // dump post-endHandler _data 最终状态
                                            try {
                                                console.error('[DRAG] POST-END _data.selectData:', JSON.stringify(cap._data ? cap._data.selectData : 'NO _data'));
                                                console.error('[DRAG] POST-END _isMoving:', cap._isMoving);
                                                console.error('[DRAG] POST-END _currentStatus:', cap._currentStatus);
                                                console.error('[DRAG] POST-END _data.mouseMoveX:', cap._data ? cap._data.mouseMoveX : 'none');
                                                if (cap._data && cap._data.blockWidth !== undefined) {
                                                    console.error('[DRAG] POST-END _data.blockWidth:', cap._data.blockWidth);
                                                    console.error('[DRAG] POST-END expected slide pos:', (cap._data.slideWidth||260) - cap._data.blockWidth);
                                                }
                                            } catch(e) {}
                                            if (typeof cap.getEncryptContent === 'function') {
                                                try {
                                                    var enc = cap.getEncryptContent();
                                                    console.error('[DRAG] getEncryptContent result:', typeof enc, JSON.stringify(enc||{}).substring(0, 1000));
                                                } catch(e4) { console.error('[DRAG] getEncryptContent err:', e4.message); }
                                            }
                                            if (typeof cap.getResult === 'function') {
                                                try {
                                                    var res = cap.getResult();
                                                    console.error('[DRAG] getResult:', JSON.stringify(res||{}).substring(0, 500));
                                                } catch(e5) { console.error('[DRAG] getResult err:', e5.message); }
                                            }
                                        }, 500);
                                    }, 50);
                                }
                            }, delay);
                        })(curX, curY, si);
                    }
                }

                // ============================================================
                // 高斯噪声
                // ============================================================
                function gaussianRand(mean, stddev) {
                    var u1, u2, z;
                    do { u1 = Math.random(); } while (u1 === 0);
                    u2 = Math.random();
                    z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
                    return mean + z * stddev;
                }

                // ============================================================
                // 轨迹生成策略 v6 — 百分比超调 + 封顶 + 多步长
                //
                // 经验数据:
                //   - v4 (35-50%超调, minDelta -86~-91): 4/6 PASS ✓
                //   - v5 (固定20px超调, minDelta -4~-37): 0/2 REJECT ✗
                //   → 服务器倾向显著超调 → 回退修正的人类行为
                //
                // v6: 取 v4 的百分比超调思路 + 封顶 + 更多步长
                // ============================================================
                function generateTrajectory(totalDistance, steps) {
                    var d = totalDistance;
                    // v4 的超调公式 (4/6 PASS) + 封顶
                    var overshoot = Math.max(6, Math.min(d * 0.35, 30) + Math.random() * Math.min(d * 0.15, 15));
                    // 额外保证：短距离至少 15px 超调
                    if (overshoot < 15 && d > 30) overshoot = 15 + Math.random() * 10;
                    overshoot = Math.min(overshoot, 55);
                    var peakDist = d + overshoot;
                    var mid = d * 0.50;
                    var scale = d < 60 ? Math.max(1.5, 80 / Math.max(d, 25)) : 1;

                    var positions = [];
                    var current = 0;
                    var velocity = 0;
                    var dt = 1;

                    while (current < peakDist) {
                        var accel;
                        if (current < mid * 0.35) {
                            accel = (d * 0.20 + Math.random() * d * 0.08) * scale;
                        } else if (current < mid) {
                            accel = (d * 0.28 + Math.random() * d * 0.10) * scale;
                        } else if (current < d * 0.82) {
                            accel = (d * 0.15 + Math.random() * d * 0.06) * scale;
                        } else {
                            accel = -(d * 0.22 + Math.random() * d * 0.08) * scale;
                        }
                        var ds = velocity * dt + 0.5 * accel * dt * dt;
                        current = Math.min(current + ds, peakDist);
                        velocity = velocity + accel * dt;
                        if (velocity < 0.04 && current >= peakDist * 0.97) {
                            current = peakDist;
                            positions.push(current);
                            break;
                        }
                        positions.push(Math.round(current * 100) / 100);
                    }

                    var remainingExceed = current - d;
                    while (remainingExceed > 0.3) {
                        var step = Math.random() * Math.min(remainingExceed, 5);
                        if (step < 0.25) step = 0.25;
                        current -= step;
                        remainingExceed -= Math.min(step, remainingExceed);
                        positions.push(Math.round(current * 100) / 100);
                        if (positions.length > 60) break;
                    }

                    if (Math.abs(current - d) > 0.3) {
                        positions.push(d);
                    }

                    var deltas = [];
                    var prev = 0;
                    for (var i = 0; i < positions.length; i++) {
                        deltas.push(Math.round((positions[i] - prev) * 100) / 100);
                        prev = positions[i];
                    }

                    var sum = 0;
                    for (var si = 0; si < deltas.length; si++) sum += deltas[si];
                    if (Math.abs(sum) > 0.001) {
                        deltas[deltas.length - 1] = Math.round((deltas[deltas.length - 1] + d - sum) * 100) / 100;
                    }

                    if (deltas.length < 25) {
                        var targetCount = 25 + Math.floor(Math.random() * 6);
                        var expanded = [];
                        var ratio = deltas.length / targetCount;
                        for (var ei = 0; ei < targetCount; ei++) {
                            var srcIdx = Math.min(Math.floor(ei * ratio), deltas.length - 1);
                            expanded.push(deltas[srcIdx] / Math.max(1, Math.floor(1 / Math.max(ratio, 0.08))));
                        }
                        var expSum = 0;
                        for (var ek = 0; ek < expanded.length; ek++) expSum += expanded[ek];
                        var expScale = expSum > 0 ? d / expSum : 1;
                        for (var ej = 0; ej < expanded.length; ej++) {
                            expanded[ej] = Math.round(expanded[ej] * expScale * 100) / 100;
                        }
                        deltas = expanded;
                    }

                    return deltas;
                }
            });
            console.error('initSMCaptcha ok');
        } catch(e) {
            console.error('[INIT ERROR]', e.message);
        }
    }

    // ================================================================
    // 等待
    // ================================================================
    var ticks = 90;
    var iv = setInterval(function() {
        ticks--;
        // 延迟退出，让 POST-END 诊断有机会输出
        if (intercepted.fverifyUrl) { clearInterval(iv); console.error('\nDONE fverify! waiting 2s for diagnostics...'); setTimeout(function(){ process.exit(0); }, 2000); }
        if (ticks <= 0) { clearInterval(iv); console.error('\nTIMEOUT sdk:'+state.sdk+' conf:'+state.conf+' reg:'+state.reg+' fv:'+!!intercepted.fverifyUrl); process.exit(0); }
        if (ticks % 15 === 0) console.error('[STATUS] conf:'+state.conf+' sdk:'+state.sdk+' reg:'+state.reg+' fv:'+!!intercepted.fverifyUrl);
    }, 1000);
}

main().catch(function(e) { console.error('[FATAL]', e); process.exit(1); });
