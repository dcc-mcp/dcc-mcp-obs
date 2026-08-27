# 安装与生命周期

## 安全模型

Python 包与原生插件是两个独立 Release artifact。只安装与 Python 版本来自同一个
GitHub Release 的原生包，并把公布的 SHA-256 传给安装器。bundle manifest 会绑定
产品、版本、平台、每个目标路径和每个文件哈希。

安装器拒绝路径穿越、链接、多链接 receipt、平台不匹配、成员漂移和非托管升级。
文件在目标旁暂存；发布失败时恢复此前由 receipt 管理的安装。

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

安装、升级或卸载已加载的原生插件前应关闭 OBS。安装完成后启用 OBS WebSocket，
只通过 `DCC_MCP_OBS_WEBSOCKET_PASSWORD` 设置密码，重启 OBS，再用精确 OBS PID
启动 sidecar。
