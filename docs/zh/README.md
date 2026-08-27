# dcc-mcp-obs

DCC-MCP 生态中的原生、类型化 OBS Studio 控制产品。

本产品由 OBS 原生插件和 DCC-MCP sidecar 组成。C++ 插件运行在精确的 OBS
进程内，负责宿主生命周期、UI 线程派发，并通过官方 OBS WebSocket 5.x API
注册有界 vendor requests。Python sidecar 负责 MCP、Gateway、Install SOP v1
CLI 和内置 Agent Skill。

OBS WebSocket 只承担鉴权传输，不提供不受限制的 raw request 或任意脚本工具。

## 首个功能切片

- 精确插件版本、OBS 版本、PID、实例 ID、readiness 和事件序号
- 有界场景枚举和当前场景读回
- 当前场景或精确指定场景的有界 source 枚举
- 录制状态
- 开始、停止、暂停、继续录制
- 每次写操作后独立的类型化状态读回
- 稳定脱敏错误、有界 UI 派发和跨实例漂移拒绝

其余域由机器可读的[能力矩阵](../../contracts/obs-capabilities-v1.json)跟踪；在类型化
契约落地前不会伪装成已支持工具。

## 完整控制路线图

- [直播、回放缓存、虚拟摄像机和输出](https://github.com/dcc-mcp/dcc-mcp-obs/issues/2)
- [配置、场景集合、有界热键、截图和操作者状态](https://github.com/dcc-mcp/dcc-mcp-obs/issues/3)
- [可丢弃真实 OBS 验收](https://github.com/dcc-mcp/dcc-mcp-obs/issues/4)
- [输入、属性、滤镜、音频和媒体](https://github.com/dcc-mcp/dcc-mcp-obs/issues/5)
- [场景图、切换、转场和 Studio Mode](https://github.com/dcc-mcp/dcc-mcp-obs/issues/6)

## 要求

- OBS Studio 28+，并启用 OBS WebSocket 5.x
- Python 3.10+
- `dcc-mcp-core>=0.20.14,<1.0.0`
- 与 Windows、macOS 或 Linux 匹配的原生插件包

## 安装

先安装 Python 控制面，再使用同一 Release 公布的 SHA-256 安装原生插件包：

```console
python -m pip install dcc-mcp-obs
dcc-mcp-obs-install install \
  --plugin-archive dcc-mcp-obs-plugin.zip \
  --sha256 <release-sha256>
dcc-mcp-obs-install verify
```

安装器只输出一个 Install SOP v1 JSON 对象。`--dry-run` 只执行包与所有权预检，
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
场景/source 查看、pause、resume、录屏、录制视频等中英文检索词。Agent 先搜索并
加载 Skill，再调用首个切片精确交付的八个类型化工具。直播与场景切换仍属于路线图，
不是当前已发布的原生控制工具。

OBS 控制始终原生插件/WebSocket 优先。仅当某个纯视觉操作没有类型化契约时，
才允许通过 DCC-MCP `ui-control` 使用项目自有 DCC-CUA，并且必须绑定精确 PID/HWND、
执行 fresh snapshot 和操作后读回。不得回退到通用 Computer Use。

## 开发与验证边界

本地测试命令与三平台构建方式见英文 README。单元测试、fake protocol、原生编译、
安装包 smoke 和 CI 都不等于真实 OBS 宿主验收。首个交付不声称 real-OBS PASS；
可丢弃真实宿主验收仍是独立发布门禁。

## 许可证

GPL-2.0-or-later。原生模块链接 OBS Studio，并保留官方 OBS WebSocket API header
的原始版权声明。
