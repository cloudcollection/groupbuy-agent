# 安装、现场操作与恢复

CLI / UI 独立 Agent 与自动 HAR 的推荐流程见 [cli-ui-guide.md](cli-ui-guide.md)。下文描述人工输入工具模式；`ui`/`run` 命令不会启动自动报告接收器。

核心需要 Python 3.10+ 和 Git。直接在项目根目录运行即可，不必将核心安装到系统 Python。setup.ps1 可建立 D 盘虚拟环境；WithUI 安装可选的 pywinauto、RapidOCR、Pillow 和 psutil，网络或依赖安装失败时明确停止。脚本不修改全局 Git 用户身份，不注册系统任务。

## 离线读取

1. 使用正常导出的 HAR，或按下述结构提供 JSON/缓存转换文件；输入只读。
2. 准备任务的 UI 证据，包含实际搜索、目标、筛选、窗口和终页观察及本次起止时间。
3. 执行 `python -B -m groupbuy --config config.local.json run --offline`。
4. 检查状态、result.json 的 quality、CSV 及 manifest.json。任何门禁失败都会停止批次，不标记完成。

程序仅从时间窗口内读取大众点评/美团域名的 search 路径 HAR 条目；不存储 URL、请求头或认证字段。这个路径匹配是第一版保守规则，实际平台 schema 变化必须在适配器中更新并验证。只处理 data.list[].shopInfo 与其公开优惠，不补发详情接口。

JSON 格式为 `{ "schema": "groupbuy.local-responses.v1", "entries": [...] }`。每条 entries 至少有 captured_at（带时区）、request（keyword 与 start）、response（平台 JSON）；可带 round_id 区分搜索轮次。裸 JSON 无法证明请求关键词和捕获时段，会被拒绝。未实现自动寻找或解密 Reqable 缓存；需正常导出或由操作者转换为这个公开格式。

UI 证据结构可参考虚构的 examples/fixtures/*.evidence.json，但必须用实际观察重建；不能复制合成哈希充当真实证据。哈希是防误串任务及溯源手段，不能独立证明操作者声明真实，本版不具备签名取证。

## 现场界面操作

1. 操作者正常登录微信、打开大众点评。只让目标平台流量进入 Reqable；Codex/ChatGPT/OpenAI 继续使用自己的正常网络。不要复用代理、VPN、账号或证书配置。项目不会安装系统代理，也不会启动或抓取 Codex 流量。
2. 复制 live-template，调整本机 window_title_regex、process_names、list_anchor 和步骤中的可见文字。例子里的标签是示意，未针对你电脑调试。
3. 当前窗口必须保持前台。每次动作前重新观察，唯一匹配 OCR 文字后点击；结果列表从当前 list_anchor 下沿确定。坐标来自当前截图，程序不保存另一台机器的像素位置。
4. 执行 `python -B -m groupbuy --config config.local.json ui --task TASK-ID`。程序搜索、执行筛选、确认目标，滚动并随机等待。成功后证据保存在 runtime，返回 UI_READY。
5. 在 Reqable 正常导出本次时间窗口的 HAR，放到配置指定的 input.path；随后执行 `run --offline`。现场与文件解析分开，是当前可复核的推荐路径。

直接 `run` 配合 live 模式也会执行界面流程，但仍需输入文件已经由正常导出流程提供；程序不会自动操纵 Reqable 导出。尚未验证这些现场步骤，因此不要直接开启大规模自动任务。

达到滚动预算返回 scroll_budget 和 REVIEW，并保留原筛选证据。保持同一窗口、尺寸及列表内容，执行 `ui --task TASK-ID --resume` 继续一段。列表或窗口变化、登录/验证提示或失焦都不能通过 resume 绕过。终页需在结果区域出现明确文字，等待后再次观察，仍可见且加载已结束才通过；静止或预算用完不等于终页。

## 失败恢复与批次合并

修正输入/配置环境并重新确认必要观察后，使用 `retry --task TASK-ID` 将 STOPPED/REVIEW 转为待办。风险或窗口中断会丢弃可自动复用的 UI 证据；应重新运行 ui。已完成结果不可重写。程序会检查已完成清单中的文件哈希；篡改或丢失时停止。

每个任务落盘后再更新进度。崩溃发生在结果清单写好、进度更新前，下次运行会验证并恢复完成状态；未写完清单的目录保留，下次用新目录重跑。进程异常退出可能留下锁：确认没有活跃进程后，人工检查并仅删除该 progress_path 配套的 .lock 文件。不要自动清理不明锁。

合并命令：`merge --inputs runtime/outputs/TASK-A/results/RUN-A runtime/outputs/TASK-B/results/RUN-B --output runtime/batch.json`。合并检查文件哈希、任务唯一性和质量门禁，保留同一店铺在不同任务中的观察；输出已有则拒绝覆盖。

## 定时运行

在本机完成单项及一批真实验证后才启用 schedule。执行 `python -B -m groupbuy --config config.local.json schedule`，保持终端和电脑运行。它是前台轮询器，每 20 秒检查一次，每个时间槽最多领取一批；没有注册 Windows 后台服务。

时间以北京时间为准；错过时间槽不补跑。崩溃后已领取的时间槽不再自动执行，需人工检查进度后运行普通 run。失败停止当前批次；后续时间槽遇到未解决 REVIEW/STOPPED 只报告需关注。停止终端即停止调度，不会自动重启。

同学应各用自己的配置、输入和进度；GitHub 只同步代码。首项比对终页、分页、价格单位和分类，首批检查 5 项隔离与合并，再考虑四个时间槽。
