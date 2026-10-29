# The Hunt — 无缝联机与开服


从 preview.15 起，客户端与无缝服务端在本仓库统一维护。完整客户端包内置本地开服管理入口；远程 Windows 开服仍可下载独立服务端包，Linux 使用同版本部署配方。原 TheHuntServer 仓库作为历史只读仓库保留。

- 玩家：使用 `TheHuntLauncher-<版本>-windows-x64.zip`，在「本地与云端开服」打开内置管理窗口。
- 专用 Windows 服务端：使用 `TheHuntServer-Windows-v<版本>.zip`，无需安装客户端引擎。
- Linux 服务端：[部署说明](server/docs/deployment.md)与[配方](server/recipe/)。
- 本地开服、远程服务端迁移与账号保存：[开服说明](docs/local-server.md)。

由 **iytq** 维护，适用于 Bloodborne PC 移植版的 BBSC 无缝联机启动器，提供独立运行环境、中文文本、本机配置同步与自动构建。

A Windows launcher and compatibility layer for BBSC seamless co-op on BBport. This is a community project, independent of the original BBSC, BBport, Sony and FromSoftware projects.

窗口中的「检查更新」支持版本查询、自动下载校验、程序备份与替换，并保留账号和存档。网络与升级说明见 [自动更新](docs/updates.md)。

![启动界面](docs/launcher.png)

## 当前支持

| 组件 | 固定版本 |
|---|---|
| 游戏 | CUSA03173 1.09，自备游戏文件 |
| BBport | 1.9.5 |
| BBSC | 0.0.1_items，包含本仓库兼容补丁 |
| 系统 | Windows 10 / 11，x64 |
| 界面与模组文本 | 简体中文 |

这是 **0.1.0 预览版**。preview.14 整合个人商店、历史 NPC 奖励误补发、重复掉落与返家保存核对修复，并适配 BBport 1.9.5。这些 BBSC 修复已在 1.9.4 的独立双机副本中实测通过；1.9.5 已完成固定源码构建与启动/打包兼容检查，沿用未改路径的验收，不把旧实机结果标成新引擎的实机验证。详见[本版说明](docs/releases/v0.1.0-preview.14.md)与[验证范围](docs/validation.md)。

游戏汉化MOD覆盖新增退出文字的问题已加入[退出文本兼容修正](docs/quit-to-desktop.md)，保留其他译文，并沿用普通版的功能开关。

合作退出与世界记录等待的说明见[世界恢复流程](docs/world-recovery.md)。如果刚退出合作，请关闭游戏窗口后再重启加载；不要手动删除待确认记录。

## 使用

1. 从 [Actions](https://github.com/ytq77/TheHuntLauncher/actions) 下载构建产物，或从 [Releases](https://github.com/ytq77/TheHuntLauncher/releases) 下载预览版。完整解压到新目录。
2. 双击 **BBSC-Launcher.exe**。无需安装 Python 或修改普通版启动器。
3. 在「游戏与联机」选择本机普通版 `Bloodborne.exe`、游戏 `eboot.bin` 以及普通版的 `bbport.ini`。
4. 填写服务器管理员提供的四个联机地址与个人账号，保存后点击「启动游戏」。
5. 「功能与玩法」提供完整中文功能说明、开关与难度数值，支持恢复推荐。保存后下次启动生效，详见[功能与玩法说明](docs/features.md)。
6. 自建服务管理员可在「服务器管理」通过 SSH 查看状态与账号、新建及验证账号；普通玩家不需要填写，见[管理说明](docs/server/client-admin.md)。

需要先有能正常运行的 BBport 普通版。首次启动会复制本机存档与模组设置，此后无缝版存档保存在本包 `data/user` 中独立发展。普通版画质、帧率及同目录 `input.ini` 在每次启动时重新读取，因此低配置电脑不会继承其他电脑的画质。首次替换已有按键配置前会保留 `data/input.before-settings-sync.ini`；普通版文件不存在时保留无缝版现有按键。

人偶洗点在无缝版继续关闭。需要时可在普通版洗点，先完成全部免费等级的重新分配并正常保存退出，再导入普通版存档；不要导入洗点到一半的角色。导入会整体替换无缝版全部角色进度，详见[洗点说明](docs/rebirth.md)。普通版设置保持不变。FSR 4.1.1 的 Vulkan/DX12 组件、DLSS 桥接及 NVIDIA SR/FG 运行库随包提供；1.9.5 的 FSR 4.1.2 及 ML-FG 4.0.2 还依赖相应 AMD 驱动与系统支持，功能可用性由上游能力检测决定。

本项目不附带游戏本体、游戏字体/资源、存档、账号或私人服务器配置。游戏主体的汉化来自用户自己的游戏安装；BBSC 道具、联机提示由本项目提供中文文本。

已有安装可通过「检查更新」下载、校验并自动替换程序。首次从不带更新模块的旧公开版升级时，仍需退出游戏和启动器，将新版程序覆盖到原目录；保留这台电脑自己的存档、账号和个人配置。单纯重启不会下载更新，详见[客户端升级说明](docs/upgrading.md)。

## 从普通版更新存档

首次启动自动复制普通版存档。之后普通版进度增加时，先正常退出游戏，在「存档」页核对来源和目标，再点击「从普通版导入」。

导入会校验文件，自动将现有无缝存档备份到本包 `save-backups/`，再用普通版存档**整体替换全部角色**，不合并进度。普通版存档保持原样，画质和联机配置不受影响。游戏运行时禁止导入；复制或切换失败会保留或恢复原无缝存档。备份不会自动删除，可从同页打开备份目录。

「无缝版 → 普通版」提供[离线转换](docs/save-conversion.md)：自动备份并处理副本，清理两栏无缝道具引用，原版联机道具只放背包。可直接导出 ZIP，或备份旧原版后替换；全程无需启动游戏。当前仅支持已验证布局与单角色条件。

新建导入、转换任务会备份并合并真实奖杯记录，按编号去重并保留最早解锁时间；导出包携带记录，替换原版时一同合并。缺少记录会提示，不能只凭角色进度补齐历史奖杯，详见[奖杯同步](docs/trophies.md)。

## 联机

需要支持 BBSC 的 **shadNet + BBSC 游戏服务/Relay**。单有普通 shadNet 服务不等于已经具备无缝联机服务。地址字段的意义及配置示例见 [联机配置](docs/network.md)；自行部署、维护与排错见 [Ubuntu / Docker / Tailscale 服务端教程](docs/server/README.md)。

两端使用兼容版本、同一个服务器和不同账号。服务端与路由环境仍由服务器管理员维护；本启动器不会自动修改路由器、防火墙或部署服务器。

| 猎具 | 用途 |
|---|---|
| 唤猎之铃 | 召来其他猎人，成为房主 |
| 应猎之铃 | 加入另一位猎人的世界 |
| 渴血之铃 | 入侵其他猎人的世界 |
| 纷争之铃 | 在合作会话中切换玩家间战斗 |
| 断鸣空弹 | 结束合作 |

## 本仓库的改动

- The Hunt 图形启动器：旧银色、铃铛蚀刻纹样与 iytq 署名，显示当前配置、启动状态和猎具准备耗时。
- 深色标题栏、原创铃铛程序图标、清晰的页面选中状态与按需存档导入。
- 把猎具初始化从读档后的首次游戏循环提前到安装钩子阶段，取消首次 5 秒等待；保留失败重试与未就绪保护。
- 用安全的内存快照扫描处理页面权限变化，避免扫描时触发宿主的致命异常处理。
- 排除独立环境下重复加载的内置补丁 XML，保留玩家选择的其他补丁。
- 每次启动同步本机画质设置，账号通过 Windows DPAPI 按当前用户加密保存。
- 210 条道具/菜单文本、18 处运行时提示及动态 Boss 提示的简体中文翻译。

## 构建与贡献

见 [构建说明](docs/build.md)、[验证记录](docs/validation.md) 和 [贡献指南](CONTRIBUTING.md)。

GitHub Actions 在推送、PR 和手动触发时执行测试与 Windows 构建。版本标签 `v*` 生成 GitHub **预发布版**，同时附带二进制、完整应用源码和 SHA256 校验文件。上游源码固定到 `upstream.lock.json` 中的提交，升级需要显式修改并重新验证；MSYS2 工具链版本会记录在产物中，当前不承诺逐字节可重现。

## 致谢与许可证

- [CLSAMG22 / BBSC](https://github.com/CLSAMG22/BBSC)：无缝联机主体、加载器、服务器及原始道具机制。
- [Supermedo / bloodborne_pc](https://github.com/Supermedo/bloodborne_pc)：BBport 核心、画面与输入支持。
- [shadPS4](https://github.com/shadps4-emu/shadPS4) 及上游贡献者。
- [MSYS2](https://www.msys2.org/) 与各依赖项目。

本项目代码使用 GPL-2.0-or-later；第三方代码保留原许可证和署名。详见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。不宣称拥有上游项目或 Bloodborne 游戏资产。
