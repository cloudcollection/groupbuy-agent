# CLI 与网页：同一个独立 Agent

0.3.0 的两个入口共用 `run_agent`、平台工具、自动 HAR 接收器和进度文件。运行时不需要 Codex、MCP 或 Codex 登录态。真实模型调用使用你自己的 OpenAI API；Python 执行观察、输入、分页校验及导出。模型不能直接点任意坐标或读取抓包内容。

## 先打开网页体验

把项目放在 D 盘，安装 Python 3.10+，在项目根目录打开 PowerShell：

```powershell
./setup.ps1 -WithUI
./start-ui.ps1
```

浏览器打开 `http://127.0.0.1:8787`，保持终端运行。先点击「离线演示」：无需密钥，使用虚构数据与脚本模拟模型，真实执行解析、质量检查和导出。演示结果在独立 `runtime/agent-demo/<编号>/outputs`，具体位置见运行报告。演示不会把你保存的真实任务标记完成。

网页只监听本机，接口校验 Host、Origin 和 CSRF。不要把端口转发到外网。API Key 可以在密码输入框填写，只用于本次运行内存；也可通过启动终端的 `OPENAI_API_KEY` 传入。程序不将密钥写入配置、日志或报告。

## 第一次配置自动 HAR

1. 在网页每行填写 `城市 | 搜索词`，填写页面实际可见的类别、范围，先用一项任务，点击「保存任务」。每次保存建立新的工作区；以后继续同一批时直接点击启动。
2. 点击「获取配置地址」，复制本机上传 URL。它包含随机路径，保存在忽略的 `runtime/capture/receiver.local.json`，不要分享该文件。
3. 打开 Reqable，进入「工具 → 报告服务器」，分别新增两条规则。匹配：`https://*.dianping.com/*`、`https://*.meituan.com/*`；两条都使用上述上传 URL，压缩选 **Gzip 或不压缩**，启用规则。
4. 使用你已有的正常抓包方式，只捕获微信小程序的目标平台流量；如果使用系统代理，请在 Reqable 排除模型、ChatGPT、Codex 等进程/域名。程序不会安装证书、开启系统代理、修改 VPN 或绕过验证。
5. 登录微信、打开大众点评小程序。网页高级配置填写本机实际的窗口标题、进程名、搜索框文字、列表锚点和筛选文字。城市与所选条件须能在页面观察到。
6. 填写账户可用、支持 Responses 函数调用的模型名称和 API Key，勾选「允许正常操作本机小程序」，点击「开始 / 继续本批」。启动后程序会将匹配的小程序窗口置前一次，之后保持它在前台。

Reqable 官方报告服务器从 v2.20.0 起提供此功能，本机检测到 3.2.15。参考 [官方说明](https://github.com/reqable/reqable-docs/blob/master/en-US/capture/30_report-server.md)。报告在 HTTP 会话结束时上传，失败不重试，因此要在开始搜索前启动 Agent 接收器；不要先搜索完再启动。

```text
启动接收器 → 绑定任务时间窗 → 正常搜索/筛选/滚动 → 明确终页证据
→ 等待报告与完整分页 → 保存脱敏 HAR → 解析/校验/导出 → 更新进度
```

没有收到完整分页会停止，绝不会把没滚到终页或没收到数据当成完成。接收器只接受本机指定秘密路径，投影公开业务字段后才写磁盘，丢弃无关平台、模型流量、Cookie、请求头、认证字段及个人定位。Reqable 自身的原始缓存仍由你管理，不能发布。

## 模型网络与抓包分开

模型请求默认显式不继承系统/抓包代理。如果 VPN 使用 TUN，可使用默认连接。如果必须经过独立 HTTP 代理，在启动终端设置 `OPENAI_PROXY_URL` 为你 VPN 的 HTTP 代理地址；不支持 SOCKS 或带用户名密码的代理 URL。不要设置成 Reqable 的抓包端口。自动报告接收器始终走本机回环。

## CLI 用法

安装后使用 `./.venv/Scripts/python.exe -B`。网页保存后会显示配置路径，CLI 使用该路径即可继续同一进度。运行时请让另一个入口保持空闲，不能同时采集。

```powershell
# 不联网演示，不需要 API Key
./.venv/Scripts/python.exe -B -m groupbuy agent --demo

# 网页保存的配置路径，或复制模板创建的 config.local.json
$taskConfig = Read-Host '任务配置路径'
./.venv/Scripts/python.exe -B -m groupbuy --config $taskConfig plan
./.venv/Scripts/python.exe -B -m groupbuy --config $taskConfig capture-init

# 配置密钥和模型后运行同一个 Agent
./.venv/Scripts/python.exe -B -m groupbuy --config $taskConfig agent --allow-ui
```

隐藏输入密钥的方法见 [agent.md](agent.md)。CLI 启动前将 `GB_TEMP_DIR`、`TEMP`、`TMP` 指向 D 盘，可沿用 `start-ui.ps1` 的配置；它们不能是 C 盘。模型名通过 `OPENAI_MODEL` 或 `agent.local.json` 的 model 字段配置。

不使用网页时，复制 `examples/config.auto-template.json` 为 `config.local.json`，改成自己的任务并设 `synthetic=false`。`input.kind=reqable` 的 `input.path` 是每任务独立的目录，自动 HAR 在其下按采集会话保存；不是待人工导出的文件名。`capture-init` 只生成上传地址，不开启监听；`agent --allow-ui` 才开启接收器。旧 `ui` 和 `run` 命令适用于人工输入流程，不能代替自动 Agent 的完整链路。

## 继续、停止、失败与结果

- 一次最多处理 `runtime.batch_size` 项，默认 5。每项先保存结果，再处理下一项；再次启动跳过完成项。
- 网页「停止」或 CLI `Ctrl+C` 在安全边界中断。正在进行的模型请求需要返回或超时；返回后不会再启动新工具动作。输出事务在完成写入后才更新进度。
- 滚动预算只产生 REVIEW。续段必须仍是原任务、窗口、列表和筛选；验证、失焦或访问限制会停止批次。
- 修正错误后点击「重试」，或 `retry --task TASK-ID`。自动采集任务的显式重试会重新搜索并创建新会话，保留旧文件以便复核。
- 结果路径见网页与运行报告。每项结果包括 `shops.csv`、`products.csv`、`compact.json`、`result.json` 和完整性清单。两个 CSV 最适合日常交付，详细 JSON 用于复核。
- 不直接修改已有任务含义和旧进度；网页保存新工作区保留旧结果。网页启动会记住最近一次保存的工作区，即使传入别的配置仍优先恢复它。

## 定时与交给其他人

首次真实单项和首批验证后，设置 `schedule.enabled=true`、`schedule.live_confirmed=true`，再运行：

```powershell
./.venv/Scripts/python.exe -B -m groupbuy --config $taskConfig agent --allow-ui --schedule
```

默认模板为北京时间 09:00、12:00、15:00、19:00，每批 5 项。调度是保持终端运行的前台服务，不唤醒电脑、不自动登录、不与网页另开并发任务。失焦/验证/API 错误等会停止服务。网页目前用于手动开始批次，定时参数在 JSON 中配置。

同学拿到源码后，各自安装依赖、设置模型密钥、配置 Reqable 和本机页面标签，导入分配给自己的任务即可。共享代码与虚构示例，不共享账号、HAR、证书、真实配置、结果或运行日志。

## 验证范围

已在本机用虚构数据验证 CLI、网页 HTTP 接口、自动报告 HTTP/gzip、任务绑定、脱敏、分页、恢复、导出及停止机制。真实 OpenAI API、小程序 OCR 与 Reqable 报告联动尚未完成实机验证；其他电脑也需要校准。仅支持当前大众点评搜索列表结构，不自动进入商品详情页，缺失规则留空。不得把离线演示作为真实采集验证。
