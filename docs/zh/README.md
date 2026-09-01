# dcc-mcp-obs

DCC-MCP 生态中的原生、类型化 OBS Studio 控制产品。

本产品由 OBS 原生插件和 DCC-MCP sidecar 组成。C++ 插件运行在精确的 OBS
进程内，负责宿主生命周期、UI 线程派发，并通过官方 OBS WebSocket 5.x API
注册有界 vendor requests。进程外 sidecar 负责 MCP、Gateway、Install SOP v1
CLI 和内置 Agent Skill；Release standalone 包自带私有 Python 运行时，不要求用户
安装系统 Python。

OBS WebSocket 只承担鉴权传输，不提供不受限制的 raw request 或任意脚本工具。

## 首个功能切片

- 精确插件版本、OBS 版本、PID、实例 ID、readiness 和事件序号
- 有界场景枚举和当前场景读回
- 当前场景或精确指定场景的有界 source 枚举
- 按精确 PID、HWND 和标题创建并回读 Windows 窗口捕获 source
- 类型化场景切换、场景项 CRUD、转场和 Studio Mode 预览/节目操作
- 插件内置、隐私安全的 Agent 键盘/鼠标活动提示 source
- OBS 顶层原生 `DCC MCP` 菜单，提供状态、叠加层、Gateway Admin 和插件信息
- 录制状态
- 开始、停止、暂停、继续录制
- 类型化直播、回放缓存、虚拟摄像机和具名输出控制
- 经评审的 source/input/property/filter 契约，以及精确音频和媒体控制
- 每次写操作后独立的类型化状态读回
- 稳定脱敏错误、有界 UI 派发和跨实例漂移拒绝

已交付与剩余域由机器可读的[能力矩阵](../../contracts/obs-capabilities-v1.json)跟踪；
类型化契约落地前不会伪装成已支持工具。

## 已交付控制面

- [直播、回放缓存、虚拟摄像机和输出](https://github.com/dcc-mcp/dcc-mcp-obs/issues/2)
- [输入、属性、滤镜、音频和媒体](../typed-source-controls.md)
- [类型化场景图控制](../scene-graph.md)
- [Windows 精确窗口捕获](../window-capture.md)
- [内置 Agent 输入提示](../agent-input-overlay.md)

## 完整控制路线图

- [配置、场景集合、有界热键、截图和操作者状态](https://github.com/dcc-mcp/dcc-mcp-obs/issues/3)
- [可丢弃真实 OBS 验收](https://github.com/dcc-mcp/dcc-mcp-obs/issues/4)

## 要求

- OBS Studio 28+，并启用 OBS WebSocket 5.x
- 与 Windows、macOS 或 Linux 匹配的 standalone Release 包

只有选择 PyPI/源码安装时才需要 Python 3.10+ 和
`dcc-mcp-core>=0.20.14,<1.0.0`；Core 会由 pip 自动解析，无需单独手装。

## 安装

下载并解压对应平台的 `*-standalone` Release 包。它同时包含 sidecar、私有运行时和
精确匹配的原生插件包。关闭 OBS 后执行：

```console
dcc-mcp-obs.exe install-bundled
dcc-mcp-obs.exe --host-pid <obs-pid>
```

macOS/Linux 使用 `./dcc-mcp-obs`。开发者或明确希望使用 Python 包的用户仍可走：

```console
python -m pip install dcc-mcp-obs
dcc-mcp-obs-install install \
  --plugin-archive dcc-mcp-obs-plugin.zip \
  --sha256 <release-sha256>
dcc-mcp-obs-install verify
```

两种安装入口都只输出一个 Install SOP v1 JSON 对象。`--dry-run` 只执行包与所有权预检，
不会修改 OBS 插件目录。详见[安装说明](../install.zh-CN.md)。

## 密码与端点

OBS WebSocket 密码由操作者通过环境变量提供：

```console
set DCC_MCP_OBS_WEBSOCKET_PASSWORD=your-password
```

首个版本只接受 `ws://127.0.0.1:<port>`，默认端口为 4455。密码不会出现在工具
结果、receipt、公开错误或日志中。`DCC_MCP_OBS_WEBSOCKET_URL` 只用于选择其他
loopback 端口。

sidecar 必须绑定一个精确 OBS 进程：

```console
dcc-mcp-obs --host-pid <obs-pid>
```

## Agent 命中

内置 `obs-control` Skill 覆盖 OBS、Open Broadcaster Software、recording、
场景/source 查看、场景图、场景项、转场、Studio Mode、pause、resume、录屏、
录制视频、按键展示、键盘、鼠标、输入提示等中英文检索词。Agent 先搜索并加载 Skill，再调用场景、场景图、录制、
直播、回放缓冲、虚拟摄像头和输出域的类型化工具。场景图写操作必须经过原生
插件的精确实例校验、截止时间和状态读回。

公共接口不会转发任意 input settings。版本 `1.0` 只评审并开放
`color_source_v3` 的有界 `width`、`height`、`color`，以及 `gain_filter`
的有界 `db`。source、滤镜、音频和媒体写操作均使用精确名称与有界读回校验。

录制 Agent 演示时，先用 `create_agent_input_overlay` 将同一个共享
`DCC-MCP Agent Input` source 分别挂到三个游戏场景，再在动作前调用
`emit_agent_input_activity`。提示只接受白名单快捷键、鼠标按键、滚轮方向或打字
字符数；不会监听全局输入，也不接收任意文本。代码仍由正常的窗口/屏幕 source
展示，Agent 在运行代码前显示对应的语义提示。详见[内置 Agent 输入提示](../agent-input-overlay.md)。

原生插件会在 OBS 顶层菜单栏注册 `DCC MCP`。`Server Status...` 展示精确的
插件/OBS 版本、原生桥接就绪状态、活动输出和当前场景；`Add Agent Input Overlay`
把共享的内置提示 source 挂到当前场景；`Open Gateway Admin` 只打开 loopback
Gateway 地址（默认 `127.0.0.1:9765`，也可使用合法的 `DCC_MCP_GATEWAY_PORT`）。
菜单在 OBS UI 线程幂等注册，并在插件卸载时清理。

关闭 OBS 时使用 `request_graceful_shutdown`，不要强制结束进程。录制、直播、
回放缓冲或虚拟摄像头仍活动时，原生插件会拒绝退出；全部停止后仅返回“已接受并
排队”的终态确认，再由调用方在连接外验证 OBS 进程和插件实例均已消失。

OBS 控制始终原生插件/WebSocket 优先。仅当某个纯视觉操作没有类型化契约时，
才允许通过 DCC-MCP `ui-control` 使用项目自有 DCC-CUA，并且必须绑定精确 PID/HWND、
执行 fresh snapshot 和操作后读回。不得回退到通用 Computer Use。

## 开发与验证边界

本地测试命令与三平台构建方式见英文 README。单元测试、fake protocol、原生编译、
安装包 smoke 和普通 CI 不等于真实 OBS 宿主验收。独立的一次性真实 OBS 门禁会在
Windows、macOS 和 Linux 上启动实际 OBS，验证精确绑定、场景图与录制状态读回，
并且只发布不含 PID、密码、端口和本地路径的隐私安全证据。详见
[验收契约](../real-obs-acceptance.md)。

## 许可证

GPL-2.0-or-later。原生模块链接 OBS Studio，并保留官方 OBS WebSocket API header
的原始版权声明。
