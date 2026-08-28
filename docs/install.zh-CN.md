# 安装与生命周期

## 安全模型

Python 包与原生插件是两个独立 Release artifact。只安装与 Python 版本来自同一个
GitHub Release 的原生包，并把公布的 SHA-256 传给安装器。bundle manifest 会绑定
产品、版本、平台、每个目标路径和每个文件哈希。

安装器拒绝路径穿越、链接、多链接 receipt、平台不匹配、成员漂移和 Windows
非可移植别名。receipt 会记录精确的受管理文件路径；verify 忽略无关条目，但任何
受管理路径漂移仍会失败关闭。upgrade 与 uninstall 只修改已验证的受管理文件，保留
操作者自有条目，并且仅在受管理目录为空时才移除它。文件在目标旁暂存；发布失败时
恢复此前由 receipt 管理的安装。

## 命令

```console
dcc-mcp-obs-install install --plugin-archive <bundle> --sha256 <digest>
dcc-mcp-obs-install upgrade --plugin-archive <bundle> --sha256 <digest>
dcc-mcp-obs-install status
dcc-mcp-obs-install verify
dcc-mcp-obs-install uninstall
```

所有命令都支持 `--plugin-dir` 指定操作者拥有的 OBS 插件目录，并支持 `--dry-run`
生成零修改计划。每次调用只输出一个 Install SOP v1 JSON 文档，稳定退出码族为：
`0` 成功、`10` 预检、`20` 获取、`30` 安装、`40` 验证。
文件安装结果为 `requires_restart`，仅文件层的 status/verify 结果为 `partial`；
在 sidecar 观察到精确的真实 OBS 插件会话前，两者都保持
`verify.directly_usable=false` 和 `LIVE_OBS_VERIFICATION_REQUIRED`。

在 Linux 和 macOS 上，成功的文件系统验证是同步的时间点验证，不代表持久锁。
未特权 POSIX 进程既不能撤销已打开写描述符的能力，也不能在操作者自有父目录保持
可写时固定受管理根目录名称。因此安装器会在返回前恢复内部验证 guard，并在
`next_steps` 中发布 `POSIX_REVERIFY_BEFORE_USE`。依赖已安装文件前应立即重新运行
`status` 或 `verify`；之后发生的任何命名空间或内容漂移都会在该后续命令中失败关闭。

安装、升级或卸载已加载的原生插件前应关闭 OBS。安装完成后启用 OBS WebSocket，
只通过 `DCC_MCP_OBS_WEBSOCKET_PASSWORD` 设置密码，重启 OBS，再用精确 OBS PID
启动 sidecar。
