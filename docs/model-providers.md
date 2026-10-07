# 多模型 API：网页和 CLI 共用

0.4.0 实现三种公开 API 协议：OpenAI Responses、OpenAI 兼容 Chat Completions、Anthropic Messages。预设 DeepSeek、通义千问、Kimi 和 Claude；另提供自定义兼容地址。预设不是所有模型的兼容保证：必须选择账户有权限且支持工具调用的模型。没有共享 Key，不使用 Codex 请求或登录凭据。

## 网页使用

1. 更新源码后重启 `./start-ui.ps1`，刷新浏览器。
2. 在「启动你的 Agent」选择 API 服务商。预设会填入 API 基础地址；百炼可替换为自己区域或工作空间的地址。
3. 填写模型名称和该服务商的 API Key。选择其他兼容服务时，必须填写基础地址，例如 `https://api.example.invalid/v1`。不要加 `/chat/completions`、`/messages` 或 `/responses`。
4. 保存任务并完成本机页面与 Reqable 配置，勾选正常界面操作，开始本批。

Key 只驻留本次运行内存，不写配置、报告、浏览器存储或日志。切换服务商会清空输入的 Key 和模型，避免误用。Key 将发送至填写的 API 地址，应使用服务商官方地址或自己的可信服务。模型参数在网页本次会话内使用；重启后的默认值从启动终端环境读取，任务进度照常恢复。

| provider 值 | 请求协议 | 默认 API 基础地址 | 服务商密钥环境变量 |
|---|---|---|---|
| `openai` | Responses | `https://api.openai.com/v1`，固定 | `OPENAI_API_KEY` |
| `deepseek` | Chat Completions | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `qwen` | Chat Completions | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `kimi` | Chat Completions | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| `anthropic` | Messages | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `openai_compatible` | Chat Completions | 必须显式填写 | `GROUPBUY_API_KEY` |
| `responses_compatible` | Responses，Codex 同类 SSE 请求 | 必须显式填写 | `GROUPBUY_API_KEY` |

## Codex 格式中转

要求 Codex 请求格式的服务选择 `responses_compatible`，不要选择 Chat Completions。API 基址填到 `/v1`；程序请求 `<基址>/responses`。UI 可选推理强度，CLI 用 `--reasoning-effort medium`，模型名填写服务商实际提供的值。

参照 [Codex 公开请求结构](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/common.rs) 和 [SSE 解析](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs) 独立实现：显式 developer/user 消息块，`stream=true`、`store=false`、`parallel_tool_calls=false`、`reasoning.effort`、`include=["reasoning.encrypted_content"]`。按其请求字段省略 `max_output_tokens`，所以该模式不提供请求级输出 token 上限；轮数、上下文体积、超时和总时间门禁保留。等到 `response.completed` 才执行函数，保留 reasoning、phase 和工具回传条目用于下一轮。截断 SSE 或仅有 `[DONE]` 均视为未完成。

程序以自己的客户端名称发送请求，不复制 Codex 登录凭据或伪装官方客户端。这是请求协议兼容，不能保证任意中转的账户规则、模型路由或认证策略。仅接受 Codex 官方客户端的服务仍需按服务商支持方式使用。

「测试 API 连接」发送一次无业务数据的 `connection_probe` 工具请求，成功显示「API 工具调用通过」。该测试可能产生 API 费用，不执行采集、不更改任务进度。网页中输入的 Key 在测试后保留于当前密码框内存，方便随后启动；切换服务商、刷新网页或启动采集后清空。使用完成可手动清空。测试通过仍不等于微信与 Reqable 联动验证。

```powershell
# 先用隐藏输入设置 GROUPBUY_API_KEY；虚构地址需要替换
./.venv/Scripts/python.exe -B -m groupbuy model-check --provider responses_compatible --base-url https://api.example.invalid/v1 --model gpt-6.1-sol --reasoning-effort medium
```

推理强度在 `agent.local.json` 使用 `reasoning_effort`，环境默认值使用 `GROUPBUY_REASONING_EFFORT`。它只适用于两种 Responses 入口。协议枚举包括 none/minimal/low/medium/high/xhigh，但每个模型可能只接受其中部分。官方 OpenAI 接入仍用 Responses API，旧配置保持有效。

默认没有模型名，按服务商控制台填写。Chat Completions 兼容服务必须接受标准 messages、tools、tool_choice、max_tokens 和非流式响应。兼容地址不代表实现了该服务商的原生协议。Claude 适配器使用标准文本与工具消息，未开启扩展思考或服务端工具；通义请求显式关闭思考。DeepSeek/Kimi 返回的 `reasoning_content` 会在内存中随原生工具消息回传。服务商的特殊字段、网关扩展和不同模型行为仍需实际验证。

## CLI 使用

设置密钥时使用隐藏输入，避免将真实密钥写进命令历史：

```powershell
$providerSecret = Read-Host '所选服务商 API Key' -AsSecureString
$providerSecretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($providerSecret)
try {
    $env:GROUPBUY_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($providerSecretPtr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($providerSecretPtr)
}
Remove-Variable providerSecret, providerSecretPtr
$env:GROUPBUY_MODEL = Read-Host '可访问且支持工具调用的模型名称'
./.venv/Scripts/python.exe -B -m groupbuy --config config.local.json agent --provider deepseek --allow-ui
Remove-Item Env:GROUPBUY_API_KEY
```

将 `--provider` 改成 `qwen`、`kimi` 或 `anthropic` 即可换预设。`GROUPBUY_API_KEY` 是操作者显式提供给所选 API 的通用密钥；未设置时才读表中对应变量，绝不拿 `OPENAI_API_KEY` 作为其他服务商的后备。

自定义兼容服务：

```powershell
./.venv/Scripts/python.exe -B -m groupbuy --config config.local.json agent --provider openai_compatible --base-url https://api.example.invalid/v1 --allow-ui
```

此地址为虚构示例，需要替换。远程 API 必须 HTTPS；本机兼容服务可显式使用 `http://127.0.0.1:端口/v1`、localhost 或 IPv6 回环。地址拒绝用户名、密码、查询参数、片段和接口后缀。不自动查找本机模型，也不保证任意本机模型具备工具调用能力。

还可用环境变量 `GROUPBUY_PROVIDER`、`GROUPBUY_MODEL`、`GROUPBUY_BASE_URL`，或 `agent.local.json` 的 provider/model/base_url 设置默认值；CLI 参数优先于 JSON，JSON 的非空值优先于环境。模型为空时，服务商专用变量依次为 OPENAI_MODEL、DEEPSEEK_MODEL、QWEN_MODEL、KIMI_MODEL、ANTHROPIC_MODEL。默认 provider 仍是 openai；旧 OpenAI 配置继续有效。定时入口同样接受 `--provider`、`--model`、`--base-url`。

所有模型请求默认跳过系统抓包代理。需要独立模型代理时使用 `GROUPBUY_MODEL_PROXY_URL`，OpenAI 兼容旧 `OPENAI_PROXY_URL`；其他服务商不继承旧 OpenAI 代理变量。API 代理与 Reqable 抓包端口分开，程序不更改系统或 VPN 配置。

## 门禁与验证

不同协议统一为本机工具调用后再校验：禁止并行工具调用、重复调用编号、任意命令、非法参数和越过批次顺序。截断响应、拒绝、认证失败和限流均停止；模型不能直接写完成状态。模型只收到不透明任务引用、计数、状态和错误码，业务数据留本机。

已实现协议适配，并用虚构响应验证各预设的多轮工具回传、解析导出、进度恢复、停止、密钥隔离和不落盘；自定义兼容 API 另通过真实本机 HTTP 验证。尚未使用真实服务商 Key 发起联调，账户权限、费用、工具调用能力和现场采集需要操作者验证。无密钥的 `agent --demo` 是脚本演示，不是任何大模型联调。

协议参考：[OpenAI 函数调用](https://developers.openai.com/api/docs/guides/function-calling)、[百炼兼容 API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)、[Kimi API](https://platform.kimi.com/docs/api/chat)、[Claude 工具回传](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)。DeepSeek 预设接口仍需真实账户验证，其文档入口为 [官方 API 文档](https://api-docs.deepseek.com/)。
