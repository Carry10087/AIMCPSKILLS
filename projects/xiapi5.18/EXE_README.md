# task_worker 打包与运行说明

## 当前工作流程

1. 程序启动后读取同目录的 `config.json`。
2. 根据 `base_url` 和 `api_token` 连接任务平台。
3. 自动发现已经打开的浏览器 CDP 调试端口，或者使用 `config.json` 里的 `adspower_debug_port`。
4. 调用 `/api/task/pull` 拉取任务。
5. 拿到商品链接后，通过浏览器页面打开 Shopee 商品页并提取商品数据。
6. 提取成功后生成 `taskResult`，调用 `/api/task/submit` 提交任务。
7. 成功和失败都会写本地记录：
   - 成功详情：`out/tasks/success/`
   - 失败详情：`out/tasks/failed/`
   - 成功流水：`out/task_records/success/YYYYMMDD.jsonl`
   - 失败流水：`out/task_records/failed/YYYYMMDD.jsonl`
8. 无参数运行时会持续拉取任务，直到手动停止或程序异常退出。

## 打包方式

在项目目录运行：

```bat
build_exe.bat
```

生成文件在：

```text
dist\task_worker.exe
dist\config.json
```

## 换电脑运行

把下面两个文件放到另一台电脑的同一个文件夹：

```text
task_worker.exe
config.json
```

然后直接运行：

```bat
task_worker.exe
```

## 另一台电脑需要准备的东西

- Windows 系统。
- 能访问任务平台接口的网络。
- `config.json` 里的 `base_url` 和 `api_token` 正确。
- 已经打开可被 CDP 连接的浏览器环境。
- 如果 Shopee 访问依赖代理，代理客户端也要正常运行。

注意：exe 只打包 Python 程序和依赖，不会打包浏览器、代理客户端、账号环境或任务平台服务。
