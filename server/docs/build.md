# 构建与发布

固定源码版本和 SHA256 位于 `recipe/sources.json`。构建时下载并校验，已有缓存也须重新核对；不自动追踪上游 main。

## Windows 本机构建

安装 MSYS2 CLANG64，安装以下包及其依赖：

```bash
pacman -S --needed git mingw-w64-clang-x86_64-toolchain \
  mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja \
  mingw-w64-clang-x86_64-python mingw-w64-clang-x86_64-qt6-base \
  mingw-w64-clang-x86_64-qt6-websockets mingw-w64-clang-x86_64-qt6-httpserver
```

在仓库根目录的 PowerShell 中执行，替换实际 CLANG64 路径：

```powershell
.\windows\build-native.ps1 -Toolchain C:\msys64\clang64
```

脚本依次准备固定源码、编译服务与账号工具、运行上游 CTest、使用 Windows 自带 .NET Framework 编译并校验 WPF 管理窗口、打包运行库、验证中文空格路径下的运行、仅打包无缝服务、账号持久性、旧普通模式拒绝及遗留文件保留、STUN 两种格式、召唤流程、双向 Relay、本机登录地址，再通过管理管道测试账号增删改、原生 UTF-8 登录、数据库备份和 GUI 进程回收，最后输出 `windows/dist/TheHuntServer-Windows-v版本.zip`。

在同一工具链环境执行 `python scripts/archive-source.py`，生成根目录 `dist` 下的 Windows ZIP、完整固定源码 ZIP、Linux 配方 ZIP 和 SHA256SUMS.txt。源码包只读取 Git 已登记文件和固定清单中的已校验下载，排除账号、私钥、日志和测试状态。

源码包解压后再次构建，Windows 会复用 `windows/downloads`；Linux 可将同一缓存复制到配方 `.build` 的对应位置，参见部署教程。操作系统依赖仍需安装。

MSYS2 编译器、Qt、Python 由包管理器提供；版本记录在 BUILD-INFO.json 和 licenses/runtime-packages.txt。当前不承诺不同时点的二进制逐字节一致。

## Linux stdin 账号助手

Linux 配方使用 `account_helper.py` 对固定 clientsample CMake 校验后加入 `shadnet-admin`，与 Windows helper 使用相同源码。独立 recipe 内的 `fixes/stdin_account.cpp` 必须与 `windows/stdin_account.cpp` 保持逐字节一致；完整源码树的准备程序和回归会拒绝漂移。Docker context 白名单显式包含安全脚本、helper 生成脚本及离线测试源。

新镜像构建并安装 `/usr/local/bin/shadnet-admin`，供客户端 SSH 管理和 `recipe/create-account.py` 通过 stdin JSON 注册／验证账号。测试目标 `test_stdin_account` 使用真实 helper、固定客户端与客户端 Protobuf，只把传输层替换成内存夹具，不打开套接字。CTest 中的 `stdin_account` 检查 UTF-8／转义 JSON、成功／失败结果、无凭据 argv 和安全输出。

Windows 与 Linux 使用相同原生用例。隔离容器完整构建、原生运行和合成账号交互的最终结果见[本轮加固记录](../../docs/hardening-20261105.md)；未据此部署私人服务器。

## Relay v2 协议回归

完整构建加入原生 `test_relay_auth`，覆盖账号、房间权限、吊销、限速、连接上限和持续慢接收者。`tests/relay_v2_integration.py` 使用实际原生注册、登录和 Matching 命令创建隔离房间，核对 HTTP 所有权、定向入房前位置、撤销、MAC 与重放。它要求已经运行的临时服务及明确端口；`--state-dir` 只允许对本次新建的合成账号执行吊销测试。

Windows 另可运行 `tests/test_relay_auth_runtime.py --build-dir windows/build --output 新临时目录`；加入 `--native-client-dll` 可使用客户端生产 C++ 协议代码的测试适配器。Linux 使用独立 Python 对端，不能将其描述为在 Linux 上运行了 Windows 游戏客户端。

## GitHub Actions

- push main、PR、手动运行：构建并测试 Windows 原生包；在全新 Linux runner 构建云端镜像并检查 HTTP 路由和 STUN。
- Linux 测试使用 runner 临时回环接口上的示例地址，离线模拟 Tailscale 地址空间，不接入私人 tailnet。
- Windows 产物包含完整源码包、Linux 配方和校验表；Actions 产物保留 14 天。
- 推送与 VERSION 一致的 `v版本` 标签，待两端构建通过后自动发布 Release。含连字符的版本标记为预发布。
- 自动构建不部署到私人服务器，不更改现有账号和客户端。

发布前更新 VERSION 和 `docs/releases/v版本.md`，提交、推送并确认主分支通过，再推送标签。测试版本不会自动变成稳定版本。
