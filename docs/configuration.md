# 配置与数据口径

JSON 配置的 version 为 1。mode 为 offline 或 live。每个任务包含 task_id、platform、search_term、target_location、filters、fields、output_formats、status、input。任务编号只能用字母、数字、下划线或短横线。platform 当前只能为 dianping。

target_location 支持 city（必填）、business_area、merchant_name、address、center_name；全部是公开商家搜索目标，不读取操作者当前定位。filters 支持 category 与 range_text，它们必须是实际界面展示的文字。关键词不做模糊匹配。城市、目标和所有已指定筛选在 UI 中可见才能形成新的现场证据；标签不一致时停止并调整配置。

字段集可以使用 `"*"`，或列出需要的字段。task_id、platform、shop_id、captured_at、provenance_id 不可省略。选择价格时必须保留单位，选择距离值时必须保留距离原文、搜索中心和来源，选择分类时必须保留分类依据。

店铺模型见 groupbuy/models.py：完整名称、类别、分类及依据、评分、人均及单位、评价数原文、地址、商圈、可选距离、采集时间、质量标记和溯源。商品模型包括商品编号、名称、类型、售价、原价、单位、销量原文、可见规则和溯源。缺失商品 ID 保持 null，不虚构平台商品编号；用观察溯源区分这些条目。

公开团购商品使用 PublicProduct。PersonalPurchaseOrder 是独立且拒绝实例化的边界模型，没有读取、解析或导出个人订单的功能。

| runtime 参数 | 作用 |
|---|---|
| batch_size | 每批最多处理的任务数，示例为 5 |
| max_scrolls | 每一滚动段的次数预算；达到预算进入 REVIEW |
| wheel_steps | 一次滚轮步数，示例为 10，最大 30 |
| wait_seconds | 每次等待的随机秒数区间，示例为 [1,2] |
| retry_limit | 页面加载中的额外等待次数；不自动重试验证或质量失败 |
| evidence_mode | structured 保存结构化观察摘要；hashes 保留必要结论和帧哈希 |
| output_dir | 不可覆盖的结果目录根 |
| progress_path | 独立进度文件；配套锁防止同时执行 |

证据模式都保留任务、窗口、筛选和时间窗口。原始截图及全部 OCR 文本不落盘；敏感信息不能通过截图旁路进入仓库。本版本不支持图像证据导出。

所有配置路径相对项目根解析，支持环境变量；环境变量未定义时拒绝继续。Windows 的输出路径及其解析后的真实位置必须在 D 盘。临时目录用 GB_TEMP_DIR，启动第三方工具前同时设置 TEMP、TMP、PIP_CACHE_DIR 和 PYTHONDONTWRITEBYTECODE。

schedule 默认 disabled，timezone 当前仅支持 Asia/Shanghai。times 示例为 09:00、12:00、15:00、19:00。现场调度额外需要 live_confirmed=true，表示操作者已经在自己的电脑完成首项和首批验证，不是程序自动给出的认证。

配置中的 status 是新任务的初始状态。已有进度以进度文件为准；完成状态必须由质量门禁和结果清单产生，不能直接配置为 COMPLETE。任务定义变化会改变配置哈希，程序拒绝复用旧进度；修改任务含义时分配新 task_id 或使用独立 progress_path，不篡改旧记录。

## 自动 HAR 配置

模板为 `examples/config.auto-template.json`。`input.kind=reqable` 时，`input.path` 为独立采集目录，必须位于 capture.storage_dir 下。Agent 每次创建会话目录，保存公开字段投影后的条目及 input.har、绑定清单。终页证据、任务哈希与 HAR 哈希同时匹配才可解析。手工 HAR/JSON 输入继续兼容。

| capture 参数 | 默认与作用 |
|---|---|
| enabled | false；reqable 输入必须显式开启 |
| port | 8765；只监听 127.0.0.1 |
| settings_path | runtime/capture/receiver.local.json；随机上传路径，不发布 |
| storage_dir | runtime/capture；每任务目录必须互不相同 |
| settle_seconds | 2；完整分页后等待报告稳定 |
| timeout_seconds | 30；缺页或无报告超时停止，最大 120 |

接收压缩限 gzip 或不压缩，每报告压缩前/后最大 8 MiB，每报告最多 1000 条，每任务最多 5000 条。报告协议错误不会记录原始内容。官方报告上传失败无重试，重新采集需要显式重试任务。

本地网页默认端口 8787，与捕获端口不同。保存表单会创建 `runtime/ui-workspaces/<编号>`；输出、进度及采集目录互相隔离。接收器私有 URL 配置共用，切换工作区无需重新输入 Reqable URL。详见 [cli-ui-guide.md](cli-ui-guide.md)。
