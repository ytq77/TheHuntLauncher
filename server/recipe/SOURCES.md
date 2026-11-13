# 配方来源与验证边界

这是给新 Linux 主机准备的构建配方，不是正在运行的服务器的配置备份。
请先阅读上一级服务端教程；不要用此目录覆盖现有部署。

- shadNet：`Wozzardman/shadnet-p2p`，固定提交
  `5a849a192fd4450593c57b10697316ac324c856e`，GPL-2.0-or-later。
- Protobuf、Abseil 及 BBSC 的准确提交和源文件 SHA256 记录在
  [`sources.json`](sources.json)。准备程序先校验再使用。
- `fixes/stun_server.cpp` / `.h` 保留原版权和许可证声明，兼容四／六字节封装，
  并将端点更新限制到当前登录连接的来源 IP。`fixes/trusted_network.h` 限制支持的监听网络。
- `security.py` 对固定源施加可重复的安全补丁：WebAPI 拒绝封禁账户 token、封禁轮换 token、
  召唤删除精确双条件、STUN 端点代次清理以及受信网络监听。离线原生用例在 `tests/`。
  一致性和来源 IP 检查不是身份认证，部署与剩余边界见上一级教程的 `security.md`。
- BBSC 游戏服务取自 `CLSAMG22/BBSC` 的
  `315f76c38b1ca5843b082f93f47927a9193ad94a`。源码调整包括添加
  `--shadnet-webapi`，使召唤地址可以使用独立的 `31415` 端口，并使本机登录 URL 使用实际监听地址；
  另拒绝读坏在线数据后空写、收紧留言／血迹删除条件，并拒绝不受支持的监听和来源网络。
  BBSC 的 LICENSE、NOTICE、CREDITS、SUPPORTERS 和
  `LICENSE.NoDreamForHunter` 一起下载到 `.build/game-app`。
- shadNet 镜像保留校验后的完整源归档、源版本表及 STUN 补丁，位于镜像内
  `/usr/local/share/shadnet/`。系统 Qt 依赖由 Ubuntu 包管理器提供。

本次安全候选的离线检查及兼容边界见 `../docs/security.md`；以下是先前配方基线的验证，不能套用于新增安全逻辑。
配方基线验证：本地固定源材料的离线准备、重复运行保留配置、拒绝冲突配置、
STUN 补丁与已测试版本一致、BBSC 服务与已测试的隔离端口版本一致、
四个召唤 URL 均指向 `31415`，以及 Compose 和 Shell 语法检查。
独立 Dockerfile 已通过 GitHub 全新 Linux runner 的构建、上游 CTest、HTTP 登录、召唤路由、两种 STUN 封装和 Relay 监听检查；目标服务器的网络和双人游戏仍需实测。
Ubuntu 基础镜像使用固定日期标签，APT 软件包未锁定快照版本，因此这不保证
每次构建的二进制逐字节一致。

`prepare.py` 支持 `--offline`，要求 `.build/vendor-source` 中的三份归档，
以及 `.build/bbsc-original` 中的 BBSC 原始文件已缓存且摘要正确。
它不启动容器，不创建玩家账号，也不更改防火墙。
生成后的配置和数据属于本机私人状态；不要提交或上传。
Linux 上把状态目录所有权交给 UID/GID 10001 后，再次准备或管理账号通常需要
`sudo` 才能读取权限为 `600` 的配置。

`create-account.py` 只用于注册新账号，随后进行一次登录验证；请在该账号没有
运行游戏时使用。用户名使用 3–16 位 ASCII 字母、数字、下划线或连字符，
每个账号需要不同的邮件地址字段。此配方关闭邮件验证，不发送邮件。
密码交互输入，不写入 Shell 历史或工具输出。新镜像构建 `shadnet-admin`，
`create-account.py` 与客户端 SSH 管理入口都经 `docker compose exec -T` 的 stdin JSON 传递凭据，
不会回退到把密码／注册密钥放入 `shadnet-sample` 参数。旧镜像缺少 helper 时应停止并先核对账号状态。

`fixes/stdin_account.cpp` 是 `windows/stdin_account.cpp` 的逐字节副本：helper 本身使用 Qt JSON
与标准 C++，无 Windows 专用代码；固定 clientsample 的连接层已有 Windows/POSIX 分支。
独立配方仅携带 recipe 目录，因此保留此副本。修改 Windows 原件后必须同步此文件；
`account_helper.py` 在完整源码树内拒绝两份不一致的输入，回归也检查一致性。
它校验固定 clientsample CMake 原文摘要后加入目标，使用客户端自己的 `shadnet.proto`，
链接 Qt6::Core、libprotobuf、Threads，只在 WIN32 下链接 ws2_32 和添加 Windows 定义。
Docker 镜像将 helper 安装到 `/usr/local/bin/shadnet-admin`。本轮实际构建及容器检查见源码包 `docs/hardening-20261105.md`。

此配方的 `20444/TCP` 由原生认证 Relay v2 监听，按实际房间与房主权限转发；
客户端与服务端需一起更新，Python 旧广播 Relay 通过 `--relay-port 0` 关闭。`--no-bbhost` 已关闭另一套 bbhost 服务；
此配方不需要开放 `18671`、`3478` 或账号网站端口 `31316`。
