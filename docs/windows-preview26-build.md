# preview.26 Windows 构建与发布

版本锁位于 `upstream.lock.json` 和 `server/VERSION`。客户端沿用一个 Windows x64 安装包。发布的两个二进制 ZIP 与关键成员摘要见 [审核清单](releases/v0.1.0-preview.26.windows.json)，操作说明见 [版本说明](releases/v0.1.0-preview.26.md)。

## 重建源码与 Windows 组件

1. 使用仓库的 Windows build 工作流获取锁定的 BBSC、BBport 和服务端依赖，并按工作流顺序应用原有补丁。客户端原生输入也可以取自 [preview.25 对应源码](https://github.com/ytq77/TheHuntLauncher/releases/download/v0.1.0-preview.25/TheHuntLauncher-sources.zip)，该文件的 SHA-256 为 `383ffda96d1e4c2834d60508f5560336455a1bb61dec722dbde3b8e86c5ede0c`。
2. 对 BBport 运行 `python scripts/prepare_windows_join.py bbport <bbport>`。客户端模组由 `python scripts/build_windows_join_mod.py <bbsc> <bbport> <clang64> <output>/bbsc.dll` 生成。它应用剩余补丁并重新编译八个单元。两个补丁的输入和输出摘要在 `patches/windows-join/inputs.json`。已审客户端使用 CLANG64 Clang 23.1.5。核心使用 BBport 的 `gpu` CMake 项目、Ninja、RelWithDebInfo、`BB_PGO=off` 和 `BB_LTO=OFF`。为保持源码位置中性，C 和 C++ 使用 `-ffile-prefix-map=<bbport绝对路径>=B: -fno-file-reproducible`。发布核心移除调试段。
3. 服务端先由 `server/windows/prepare-sources.py` 准备固定版本，再按 `server/windows/build-native.ps1` 应用 Windows 补丁。运行 `python server/windows/build-matching-service.py <准备好的服务源码> <clang64> <输出>/shadnet.exe --tests` 重建实际分发的服务，并执行本机回环回归。已审服务使用 Clang 23.1.3、Qt 6.11.2 和 protoc 36.2。独立服务和客户端内置服务使用相同的文件。
4. WPF 使用 `scripts/build-entry.ps1`。存档分步诊断使用公开 preview.25 中的原核心，`scripts/stock_conversion_core.py` 对原 ZIP 和成员分别校验。原核心仍以 `bb-probe.exe` 运行，使进行中的诊断继续受游戏占用检查保护。普通联机使用当前版本核心。

## 发布经过实测的文件

这次预览版将两份经过审核的二进制 ZIP 放入同一版本的 GitHub Release 草稿。CI 正常从源码构建并回归。旧版 C# 编译器会重建模块标识，公开提供的客户端与服务端 ZIP 取自审核清单指定的草稿原件。

获得本次发布授权后，先用相同版本号建立草稿、上传两份原件，再将指向同一审核提交的版本标签送入现有 `v*` 工作流。标签任务读取草稿，核对两个 ZIP、包内联机运行文件与内置服务，附上本次构建的客户端源码、服务端源码、图形组件源码、工具链清单和 SHA-256。发布前再次逐项核对远端草稿的文件名称、大小和摘要。此次公开下载项目中，游戏安装包的名称沿用 preview.25 的更新规则。
