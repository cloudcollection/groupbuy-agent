# Groupbuy Agent · 通用团购数据采集助手

**配置任务，让 Agent 完成搜索、采集、校验与精简导出。**

[![CI](https://github.com/cloudcollection/groupbuy-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudcollection/groupbuy-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-GPL--3.0-orange)

[第一次使用](docs/first-run.md) · [完整链路模拟](docs/simulation.md) · [结果文件](docs/results.md) · [模型 API 配置](docs/model-providers.md) · [工作流与 skill](docs/workflow-design.md)

在自己的电脑上，整理商家公开展示的店铺、团购套餐和优惠券，输出可核对的 CSV / JSON。提供本地网页与 CLI；运行不依赖 Codex 或 MCP，各自使用自己的模型 Key 和平台登录状态。

**当前版本 0.6.0，实验阶段。** 仅实现大众点评适配器。新增无需 Key 的完整链路模拟，实际经过本地 HTTP/SSE、Gzip 上报、解析、导出和恢复，12 项模拟检查通过。真实小程序从搜索到最终导出的完整流程仍须现场验证。先运行模拟，再从一项真实任务开始。

## 能做什么

| 需求 | 当前实现 | 结果 |
|---|---|---|
| 整理多个城市、地点或关键词 | 配置任务、筛选和批次 | 独立任务与可恢复进度 |
| 让模型驱动采集工作流 | 多轮工具调用、状态校验、预算与空转停止 | 逐项执行，失败时报告原因 |
| 正常操作微信小程序 | 当前窗口 OCR 定位、搜索、筛选、滚动与终页核对 | 本地任务证据，需每台电脑校准 |
| 自动接收本地响应 | Reqable 官方报告服务器、时间窗与字段投影 | 通过核对后保存 HAR 并解析 |
| 精简公开数据 | 店铺与商品关联、价格单位、销量原文与来源 | shops.csv、products.csv、JSON |
| 分批、暂停和续跑 | 每项先保存，锁与文件校验、显式重试 | 避免串任务或静默覆盖 |
| 交给同学使用 | 安装脚本、虚构样例、详细指南与采集 skill | 各自本机运行，账号和数据独立 |

不处理个人购买订单、支付记录、核销码或手机号。未知信息留空。其他团购平台尚未实现。

## 第一次运行：先看输出

Windows 建议 Python 3.12。把仓库放到 D 盘，在 PowerShell 中运行：

~~~powershell
git clone https://github.com/cloudcollection/groupbuy-agent.git
cd groupbuy-agent
./setup.ps1
./.venv/Scripts/python.exe -B -m groupbuy simulate
~~~

模拟不需要 API Key、微信或 Reqable，不操作桌面。虚构规划器和界面通过真实本地 HTTP/SSE 和 Gzip 报告连接，执行接收、分页、去重、关联、脱敏与导出。

预期看到 `status: PASS`、`backend: SIMULATED_RESPONSES_SSE` 和 **12 / 12 检查通过**。正常场景先完成 5 项，再恢复第 6 项，共导出 12 家虚构店铺、12 个商品；错误地址、缺页、验证、网络异常、失焦与模型流中断均不会误报完成。报告和 CSV 在 `runtime/simulations/<运行编号>/`。

网页也提供“模拟完整链路”按钮。原有 `agent --demo` 保留，用于快速查看 5 项离线样例输出。**模拟与演示不证明真实采集成功或模型规划能力。** [场景与验收条件](docs/simulation.md)。

## 真实采集：使用网页

~~~powershell
./setup.ps1 -WithUI
./start-ui.ps1
~~~

打开 [本机工作台](http://127.0.0.1:8787/)，保持终端运行。

1. 正常登录微信，打开大众点评小程序。
2. 每行填写 `城市 | 搜索词 | 搜索中心`，第三项可省略。每批先设为 **1**。核对实际城市、地点中心、类别与页面提供的范围，配置可见文字步骤后保存。
3. 点击“检查本机环境”；复制完整上传地址，在 Reqable 编辑目标平台规则并开启报告服务器总开关。上传地址包含 `/report/` 随机路径，不能使用工作台的 `8787` 页面地址。只有一个免费名额时先配置大众点评域名验证；未配置域名的响应不在覆盖范围。
4. 点击“测试上报 · 30秒”，在小程序产生新请求。上报为 0 时先修复连接。
5. 填写支持工具调用的模型、API 地址和 Key，测试 API。工作台分别保留环境、上报与模型的检查记录。
6. 允许操作小程序，点击“开始 / 继续本批”，保持小程序前台。每项通过终页、分页、安全与关联检查后才导出并继续。
7. 打开结果目录核对 CSV，首项通过后再扩大到每批 5 项。

**搜索关键词不会自动改变定位中心。** 页面不存在的半径不能填成已支持。Reqable 只面向目标小程序，与模型/VPN 网络分开。本程序不修改系统代理，不安装证书，不重放私有接口。

详细菜单、上报规则、校准和错误处理见 [六步教程](docs/first-run.md)。CLI 与网页共用同一配置和进度，同一时间运行一个入口。

## 技术上是什么 Agent？

这是 **LLM 工具编排 + GUI 自动化 + OCR + 本地数据处理**：模型按当前任务阶段选择受限工具，本地代码观察窗口并执行动作，独立校验决定任务是否完成。

当前采集模型接收的是任务引用、状态与受控摘要；截图由本地 OCR 处理，没有直接发送给模型。因此不宣称已经实现截图驱动的自主多模态规划。

~~~text
配置任务 → 环境 / 上报 / 模型检查 → 模型选择工具
                                       ↓
正常界面操作 → 本地响应 → 分页与质量核对 → 导出 → 保存进度
                                       ↓
                                  失败停止并说明原因
~~~

运行提示位于 [collection.md](groupbuy/prompts/collection.md)，可分发流程位于 [采集 SKILL.md](skills/groupbuy-collection/SKILL.md)。skill 方便助手协助配置和诊断，不是运行 Agent 的前提。[设计依据与参考仓库](docs/workflow-design.md)。

## 模型、恢复与交接

- 支持 OpenAI Responses、Chat Completions 与 Anthropic Messages 协议；提供 DeepSeek、Qwen、Kimi、Claude 及自定义兼容地址配置。各账号和模型仍须独立测试。
- Codex 同类网关选择“Codex / Responses 兼容 API”。Key 只用于当前运行内存，不使用 Codex 的账号凭据。[模型指南](docs/model-providers.md)。
- 页面网络异常或持续加载超时会停止，不自动更换账号、网络或绕过限制。
- 登录、验证、失焦、缺页或解析异常会停止；处理原因后显式重试。连续无进展的检查会提前停止，避免模型空转。
- 同学只需下载代码、安装环境并完成自己电脑的页面与连接校准。各自保存配置和结果，不共享账号、密钥或真实 HAR。
- 批量合并与前台定时器见 [使用与恢复](docs/usage.md)。只有首项和首批真实验证后才启用定时；本项目未安装后台系统任务。

## 验证与发布

[验证记录](docs/validation.md) 区分软件测试、连接探测和真实现场验证。仓库只发布最终源码、配置模板、虚构测试样例、使用说明与检查工具。原始 HAR、真实任务、截图、运行日志、结果和中间笔记留在本地。

开发验证：`python -B -m unittest discover -s tests -v`。发布使用精确白名单、提交前检查，以及工作区、暂存区和全部可达历史敏感扫描。[发布说明](docs/publication.md)。

## License

GPL-3.0-only，见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。项目独立实现；不隶属于大众点评、美团或模型服务商。
