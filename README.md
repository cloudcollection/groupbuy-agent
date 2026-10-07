# 通用团购数据采集 Agent

面向所有使用者开放的团购数据采集工程。任何人都可以下载代码，在自己的电脑配置公开商家搜索任务、接入正常导出的本地响应，并获得可复核的精简数据。首个适配器为大众点评；其他平台尚未实现。默认不处理个人购买订单。

这版已验证离线闭环、批量、恢复和调度逻辑。Windows 界面驱动已编写，尚未在真实页面或其他电脑验证。现场操作依赖操作者登录、匹配本机 UI 标签和正常导出文件；不承诺无人值守完成全部现场采集。见 [验证状态](docs/validation.md)。

任务配置 → 正常界面搜索与筛选 → 滚动至明确终页 → 读取本地导出 → 选择最后搜索轮次 → 完整分页 → 店铺及商品关联 → 脱敏与质量检查 → 精简导出 → 每项更新进度。

## 下载与开始使用

在 GitHub 点击 **Code → Download ZIP**，或克隆本仓库。Windows 用户解压到 D 盘，使用自己的微信、平台登录状态和本地输入，账号与采集数据保留在自己的电脑。共享的是代码、安装说明和虚构样例，没有公共采集账号或云端代采服务。

第一次使用请按 [公开使用指南](docs/public-getting-started.md) 完成环境检查、虚构样例和一项真实任务校准。通过后再启用批量与定时运行。

## 快速试用虚构样例

将整个项目放在 D 盘，使用 Python 3.10+。核心只用标准库，不需要安装第三方包。先在项目根目录设置临时目录；如果已有统一的 D 盘临时目录，用环境变量 GB_TEMP_DIR 指向该目录。

```powershell
$env:GB_TEMP_DIR = Join-Path $PWD 'runtime/tmp'
New-Item -ItemType Directory -Force $env:GB_TEMP_DIR | Out-Null
$env:TEMP = $env:GB_TEMP_DIR
$env:TMP = $env:GB_TEMP_DIR
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
python -B -m groupbuy plan
python -B -m groupbuy run
python -B -m groupbuy run
python -B -m unittest discover -s tests -v
```

样例有 6 个虚构任务。第一批处理 5 个，第二批处理剩下 1 个；再次运行跳过已完成结果。每项生成 result.json（完整业务投影、溯源与质量报告）、compact.json、shops.csv、products.csv、manifest.json。默认输出位于 runtime/outputs；进度位于 runtime/progress.json。所有运行产物均被 Git 忽略。

JSON 保留字段原文和 null。CSV 空值为空格单元，不转换“100+”“79起”；可能被表格软件解释为公式的文本添加单引号。价格含独立单位字段，距离含搜索中心及来源，不自动推算未知坐标。

## 配置自己的采集任务

每位使用者使用自己的 config.local.json、输入目录和进度文件。不要共享账号、HAR、证书或 VPN 配置。如果多人协作，由团队统一分配任务编号，避免重复领取；单人使用不需要加入任何分工表。本版没有跨电脑自动派单或集中账号管理。

从 examples/config.live-template.json 复制配置，改成自己授权的公开商家任务；安装可选 UI 依赖用 `./setup.ps1 -WithUI`。配置不会替你登录或修改系统代理。

现场流程和恢复命令见 [使用说明](docs/usage.md)，字段和参数见 [配置说明](docs/configuration.md)，扩展平台见 [适配器说明](docs/adapters.md)。

## 发布准备

本地新仓库采用 publication-files.json 精确白名单、.gitignore 和 .githooks/pre-commit。先运行 `python -B tools/prepare_index.py`，再运行 `python -B tools/release_check.py`。扫描包含未提交工作文件、暂存内容和所有可达 Git 提交；日志只输出问题类型和文件路径。

扫描不能证明业务文件绝对无敏感信息，发布前仍应人工检查白名单和虚构样例来源。任何真实任务、响应、结果和日志都不能进入白名单。维护者发布步骤见 [发布检查说明](docs/publication.md)。采集命令不会自动上传任务或数据。

许可：GPL-3.0-only，来源说明见 NOTICE.md。
