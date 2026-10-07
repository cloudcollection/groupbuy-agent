# 公开使用指南

任何人都可以使用本工程源码，按 GPL-3.0-only 许可修改及分发。本工程默认采集公开展示的商家、套餐和优惠券；不提供个人订单读取功能或共享平台账号。

## 1. 下载并检查环境

从 GitHub 下载 ZIP 或克隆仓库，Windows 上放到 D 盘。安装 Python 3.10+；核心离线模式只需要标准库，Git 只在克隆及开发发布时需要。首次使用先打开项目根目录终端。

```powershell
$env:GB_TEMP_DIR = Join-Path $PWD 'runtime/tmp'
New-Item -ItemType Directory -Force $env:GB_TEMP_DIR | Out-Null
$env:TEMP = $env:GB_TEMP_DIR
$env:TMP = $env:GB_TEMP_DIR
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
python -B -m groupbuy doctor
python -B -m groupbuy run
```

doctor 区分核心可运行与现场依赖可用，不会把依赖存在当成实际采集已验证。默认样例只有虚构数据，可以先确认输出表和溯源结构。

## 2. 配置真实公开商家任务

复制 examples/config.live-template.json 为 config.local.json。填写自己的城市、商圈、商家或地址、关键词和界面可见筛选文字。每项使用独立 task_id。输入文件默认放在仓库外的 groupbuy-local 目录，保护真实数据。

需要界面驱动时执行 `./setup.ps1 -WithUI` 安装可选依赖，随后用 `./.venv/Scripts/python.exe -B` 代替命令中的 python。安装脚本不替你登录、不修改代理，也不要求购买模型 API 服务。当前 CLI 的规划、校验和导出由本地代码完成，可由 Codex 等 Agent 按使用说明调用。

## 3. 首次校准大众点评页面

操作者正常登录微信并打开大众点评小程序。把 ui_profile 的窗口标题、进程名称、搜索标签、筛选步骤和结果列表锚点改成本机实际可见文字。程序使用当前观察定位，不接受其他电脑复制来的坐标。

```powershell
python -B -m groupbuy --config config.local.json ui --task YOUR-TASK-ID
```

界面流程到明确终页后保留结构化证据。Reqable 的导出仍由操作者正常完成，项目不代替抓包软件配置或导出。仅捕获目标平台流量，与 VPN 和 Codex/ChatGPT 网络分开。若无法确定分流，先准备已有授权导出文件走离线模式。

把正常导出的本次 HAR 放到 input.path，然后运行：

```powershell
python -B -m groupbuy --config config.local.json run --offline
```

检查目标、筛选、终页、分页、字段来源、价格单位、食品类别和输出条数。平台 schema 或标签变化时需要修订对应适配器；当前版本的真实界面驱动尚未完成实机验证。

## 4. 批次、续跑与定时

单项验证后增加自己的待办任务。batch_size 默认 5，逐项保存，完成项不会重复执行。滚动预算不是终页；续段需要相同任务、窗口及列表，见 docs/usage.md。

定时模板为北京时间 09:00、12:00、15:00、19:00，默认关闭。完成本机首批验证后再启用配置，保持前台调度终端运行。它不会替你唤醒电脑或自动登录平台。

## 5. 分享代码与报告问题

分享仓库链接、Fork 或代码修改即可。每个人保管自己的真实配置、HAR、证书和数据。提交问题时提供版本、受控错误码、预期行为以及重新编写的虚构样例；不要附账号、完整 HAR 或采集结果。新增平台需提供适配器和验证，未实现的平台不属于支持范围。
