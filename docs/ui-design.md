# UI 设计参考

2026-10-07 检查了以下公开 GitHub 项目与许可证。它们是设计参考，不代表 Apple 或 Anthropic 官方提供或认可本工程。

| 项目 | 已核对许可 | 适合借鉴 |
|---|---|---|
| [Puppertino](https://github.com/codedgar/Puppertino) | MIT | Apple 风格的侧栏、输入控件、排版、层级、键盘焦点和减少动效 |
| [tweakcn](https://github.com/jnsahaj/tweakcn) | Apache-2.0 | 可视化主题编辑，其 [Claude 配色预设](https://github.com/jnsahaj/tweakcn/blob/a3b47b37cba97dd637de517aab52c45ec0f83456/utils/theme-presets.ts) 展示暖白、灰褐和陶土色关系 |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | MIT | 一致的表单、卡片、按钮、状态与可定制组件 |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | MIT | 若后续添加聊天入口，可参考工具调用、流式结果和人工操作的组件组织 |

本版采用独立编写的 HTML/CSS/JavaScript，保持 Python 本机服务即可运行。参考组件层级和视觉原则；没有接入 React、Tailwind、CDN、远程字体或第三方运行代码。今后直接采用上游源码时，需要保留其对应许可及适用版权声明。

## 高 Star 项目与排版

2026-10-07 从 GitHub 公开仓库元数据核对：shadcn/ui 125,238 Star、[Ant Design](https://github.com/ant-design/ant-design) 99,701 Star、tweakcn 10,438 Star。[思源宋体](https://github.com/adobe-fonts/source-han-serif) 9,744 Star，可参考中文衬线字形；本工程不分发字体。Star 数会随时间变化。

借鉴 shadcn/ui 的控件层级、Ant Design 的字号体系和 tweakcn 的暖色主题。主标题采用本地 Noto Serif SC，38px / 650；卡片标题 20px / 650；正文和输入控件使用 Noto Sans SC 14–15px，标签 550、按钮 600。窄屏主标题为 32px。未安装这些字体时使用系统中文字体回退，其他电脑的字形仍需核验。

标志为原创 SVG：四角框与四个方块表达采集和结构化数据；侧栏与 favicon 共用同一几何语言。导航采用一致线宽的几何图标。

## 当前界面

- 桌面浅灰侧栏，直接导航到任务、自动采集和进度。
- 暖白工作区、细边框、克制圆角，陶土色按钮突出开始操作。
- 四个概览值来自实际任务状态：全部、完成、待处理、需关注。
- 任务输入与运行设置分区；首次说明、高级标签和配置路径折叠。
- 状态颜色配合文字，错误独立展示；键盘焦点可见，遵循减少动效设置。
- 窄屏侧栏变为顶部导航，表单堆叠、表格可横向滚动。
- 离线演示使用独立进度，结果目录显示本次演示实际位置。

界面保留 CLI / UI 共用的后端协议与质量门禁。视觉设计不改变“真实平台/API 仍待联调”的验证边界，不新增聊天能力或其他模型平台支持声明。
