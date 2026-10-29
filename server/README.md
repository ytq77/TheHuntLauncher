# TheHuntServer · by iytq

[![统一构建](https://github.com/ytq77/TheHuntLauncher/actions/workflows/windows.yml/badge.svg)](https://github.com/ytq77/TheHuntLauncher/actions/workflows/windows.yml)

Bloodborne PC BBSC 无缝联机配套服务端。提供 Windows 原生开服包、Linux 云端部署配方、兼容修复与中文教程。

客户端：[TheHuntLauncher](https://github.com/ytq77/TheHuntLauncher)。本仓库不包含游戏、模拟器、角色存档或预置账号。

![Windows 开服管理窗口](docs/assets/windows-server.png)

## 从这里开始

- **Windows 开服**：从 [Releases](https://github.com/ytq77/TheHuntLauncher/releases) 或 [Actions](https://github.com/ytq77/TheHuntLauncher/actions) 下载 Windows ZIP，解压后双击 `Server.exe`。在窗口中启动服务、管理账号并复制客户端地址。完整步骤见 [Windows 教程](docs/windows.md)。无需 Docker、Python 或 Qt。
- **Linux 云端部署**：阅读 [组件与端口](docs/components.md)，再按 [部署教程](docs/deployment.md) 从固定源码构建。
- **编译和自动打包**：见 [构建说明](docs/build.md)。主分支构建产物在 [Actions](https://github.com/ytq77/TheHuntLauncher/actions)；版本标签通过构建后自动发布 Release。
- **Windows 自动更新**：窗口中的「检查更新」可下载校验并自动替换程序，保留账号库和配置；支持可选 GitHub 加速。见 [更新说明](docs/updates.md)。
- **维护与故障**：[备份升级](docs/operations.md)、[排错](docs/troubleshooting.md)、[客户端管理](docs/client-admin.md)、[验证范围](docs/validation.md)。

## 端口与账号

| 用途 | 无缝联机端口 |
|---|---|
| shadNet 合作服务器 TCP | 31413 |
| STUN UDP | 31414 |
| WebAPI HTTP / TCP | 31415 |
| BBSC 游戏服务 HTTP / TCP | 20443 |
| BBSC Relay TCP | 20444 |

Windows 窗口仅提供无缝开服，可逐项复制客户端地址或一键复制全部信息。监听地址应为开服电脑自己的局域网或 VPN IPv4；两位玩家使用不同账号。账号保存在 `bbsc/db`，正常重启不需要重新创建。

从 preview.15 起不再提供普通联机模式。旧版 `normal/` 目录及数据库不会由更新程序自动删除，新版不读取或转换其中的账号；无缝所需的 shadNet 主服务、STUN 和 WebAPI 继续保留。

Windows 默认仅监听 `127.0.0.1`，配置实际地址后才供其他电脑连接。Linux 配方目前限 Tailscale 地址。局域网或 Tailscale 等虚拟局域网需双向可达，并按端口表放行主机防火墙；工具不自动改防火墙。

此固定版本的 BBSC Relay 向接入者广播，适合**一组互相信任的玩家**，未实现多组独立房间隔离。

## 来源与许可

基于 [Wozzardman/shadnet-p2p](https://github.com/Wozzardman/shadnet-p2p) 和 [CLSAMG22/BBSC](https://github.com/CLSAMG22/BBSC)。固定提交和 SHA256 见 [sources.json](recipe/sources.json)，本项目增加 STUN 4/6 字节兼容、独立召唤 WebAPI 和 Windows 本机登录地址修复。

项目代码采用 GPL-2.0-or-later；上游与运行库遵循各自许可，见 [署名说明](NOTICE.md)。二进制发布同时提供本项目源码、固定上游源码和第三方许可。自动构建通过不代表已完成双人游戏的全部回归，实测范围单独记录。
