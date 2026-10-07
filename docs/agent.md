# 独立 Agent 使用说明

0.4.0 提供独立的多服务商工具调用循环：请求模型 → 执行本地函数 → 回传结果 → 继续请求。实现 OpenAI Responses、OpenAI 兼容 Chat Completions 和 Anthropic Messages 三种协议。运行时不依赖 Codex 或 MCP。服务商配置见 [多模型 API 指南](model-providers.md)，网页启动与自动 HAR 首次配置见 [CLI / UI 教程](cli-ui-guide.md)。

## 安装与不联网演示

Python 3.10+，Windows 项目放在 D 盘。在根目录执行：

```powershell
$env:GB_TEMP_DIR = Join-Path $PWD 'runtime/tmp'
New-Item -ItemType Directory -Force $env:GB_TEMP_DIR | Out-Null
$env:TEMP = $env:GB_TEMP_DIR
$env:TMP = $env:GB_TEMP_DIR
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
python -B -m groupbuy agent --demo
```

演示为明确标注的 `SCRIPTED_DEMO`，模型决策由程序模拟，本地解析/导出工具是真实执行。没有 API 请求或费用，每次输出到新的 `runtime/agent-demo/<随机编号>`，不复用现场进度。只接受虚构离线配置及 `examples/fixtures` 中标为 synthetic 的样例。

## 自己的 API 和任务配置

复制 `examples/agent.example.json` 为 `agent.local.json`。模型须支持所选协议的工具调用；默认仍使用 OpenAI。`provider`、`model`、`base_url` 为空时读取对应环境设置。密钥从所选服务商环境变量或网页密码框读取，JSON 中禁止 api_key 字段；程序不自动读 `.env`，不读取 Codex 登录凭据。API 按使用者账户计费，没有共享密钥。

PowerShell 隐藏输入密钥，避免把真实值写进命令或文件：

```powershell
$agentSecret = Read-Host 'OpenAI API Key' -AsSecureString
$agentSecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($agentSecret)
try {
    $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($agentSecretPtr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($agentSecretPtr)
}
Remove-Variable agentSecret, agentSecretPtr
$env:OPENAI_MODEL = Read-Host '可访问的模型名称'
python -B -m groupbuy --config config.local.json agent --settings agent.local.json
# 使用结束后清除当前终端的密钥：
Remove-Item Env:OPENAI_API_KEY
```

业务配置见 [公开使用指南](public-getting-started.md)。准备好本地输入的任务使用 `mode=offline`，真实 Agent 仍调用所选模型 API；纯本地处理用原来的 `run --offline`。OpenAI 的地址固定为 `https://api.openai.com/v1/responses`；其他协议按服务商预设或显式配置的基址构造接口。拒绝重定向，无自动网络重试或私有 Codex 接口。

## 现场采集与继续执行

安装 `./setup.ps1 -WithUI`，正常登录微信，校准每项任务的 `ui_profile`。允许正常界面操作后启动：

```powershell
python -B -m groupbuy --config config.local.json agent --settings agent.local.json --allow-ui
```

搜索、筛选和滚动由本地 OCR 驱动执行。模型只选工具，不接收截图，也不会生成新的点击方式；页面变化需要修订本地配置或适配器。UI 依赖存在不代表现场已验证。

自动模式使用 `examples/config.auto-template.json`：Reqable 首次配置报告服务器后，`input.kind=reqable` 的任务在搜索前绑定接收会话，终页后进入 EXPORT_HAR，等待完整分页并保存脱敏 HAR，随后解析导出。人工 har/json 配置仍会返回 `WAITING_INPUT / local_export_required`，正常导出文件后重启继续。所有模型请求默认不继承系统抓包代理，可用 `GROUPBUY_MODEL_PROXY_URL` 指定独立的 HTTP 模型代理；OpenAI 同时兼容旧变量 `OPENAI_PROXY_URL`。它不能是 Reqable 端口。程序不修改系统代理。

滚动预算不是终页。允许分段续滚时，每段检查同任务、窗口、列表和筛选证据；超过 `max_ui_segments` 保留 REVIEW 后停止，新会话仍需通过续滚检查。登录、验证码、限制、失焦、分页失败和工具异常停止批次。人工修正后用 `retry --task` 显式重试，模型没有重置状态的工具。

## 工具与隐私边界

| 工具 | 本机行为 | 模型可见内容 |
|---|---|---|
| check_environment | 检查本机依赖 | 布尔能力状态 |
| get_status | 校验结果并规划当前批次 | 不透明任务引用、阶段、计数、错误码 |
| collect_ui | 正常界面流程与终页证据 | 阶段与终止原因 |
| export_har | 等待官方报告、完整分页与终页证据，保存投影快照 | HAR_READY 或受控错误码 |
| process_task | 本地校验、解析、脱敏、导出 | 店铺/商品数量与状态 |

模型只能操作本批次首个未完成任务。不能改配置、指定任意路径、执行 shell、读取 HAR 或写完成状态。参数使用严格 JSON Schema 并在本机复核；拒绝并行调用和重复 call_id。城市、搜索词、筛选、原始数据、商品文本、截图和本地路径均留在本机。

只有代码门禁和结果清单通过才报告 COMPLETE；模型提前结束、轮数或时间预算耗尽报告 INCOMPLETE。模型声称完成不改变进度。重新启动会校验已完成输出并跳过；崩溃后存在有效清单可恢复，未提交的 RUNNING 任务转为中断停止，需显式重试。

受控会话报告放在 `runtime/outputs/agent-runs`，记录协议、服务商、任务引用、状态、计数、轮数和 API 报告的 token 总数。密钥、API 地址、模型原文、推理内容、请求和原始响应不写报告或日志。多轮上下文只驻留内存；OpenAI Responses 使用 `store=false`，其他服务商有各自数据政策，不能将本地不落盘等同于服务端不保留。

## 预算与定时

模型配置控制 `max_rounds`、`max_output_tokens`（每请求）、`timeout_seconds`、`max_elapsed_seconds`、`max_ui_segments`（每任务）。总时间预算在轮次边界检查，进行中的 UI 工具由自身滚动/等待预算限制，不强制打断输入。没有上下文压缩或费用金额预算。

任务批次大小、等待、滚动速度和运行时间继续由业务配置控制。首次现场验证后才能启用 `schedule.enabled` 和 `schedule.live_confirmed`：

```powershell
python -B -m groupbuy --config config.local.json agent --settings agent.local.json --allow-ui --schedule
```

前台调度器先领取北京时间时间槽再请求模型，同槽不重复启动，崩溃时间槽不自动重领。人工输入、API 错误或其他未完成状态导致服务退出。不唤醒电脑、不代登录、未安装后台任务，默认关闭定时。

## 验证状态

已实现请求层、多轮工具回传、门禁、恢复和前台调度；通过模拟模型和虚构数据测试。真实 API 联调、账户模型权限、本机真实页面及其他电脑仍待验证。缺少模型返回 agent_model_required；缺少密钥返回 api_key_required。

他人使用时下载工程，设置自己的模型密钥、平台账号和任务即可。真实配置和采集文件不提交 GitHub。
