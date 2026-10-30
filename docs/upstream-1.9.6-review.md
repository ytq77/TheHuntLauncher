# BBport 1.9.6 差异核对（2026-10-30）

初次审查结论：无需重写 The Hunt 的现有适配，建议下一预览版更新内置核心，以包含部分 Windows 电脑的 HTTPS 证书连接修复。审查阶段未修改运行包或锁定版本；随后用户已授权合入，进展见本文末尾。

## 固定对比范围

- 当前 1.9.5：`ff42e28bbd5ad02939560e039008a7896b26ae41`，来自现有 `upstream.lock.json`。
- 1.9.6 标签：`windows-v1.9.6` → `41fa92047006ed5c78b833c3f68a8ecfdc601361`，实际远端标签与抓取对象一致。
- 功能提交：[`14e75bfd7db7d11735b59efaf0583627654683e8`](https://github.com/Supermedo/bloodborne_pc/commit/14e75bfd7db7d11735b59efaf0583627654683e8)。
- 其后只有 `41fa920` 的发布版本号和更新说明提交。
- [官方发布说明](https://github.com/Supermedo/bloodborne_pc/releases/tag/windows-v1.9.6)。

共六个文件变更，356 行新增、17 行删除：

| 文件 | 改动及影响 |
|---|---|
| `gpu/bbnet/include/windows_trust.h` | 在程序内携带 ISRG Root X1、X2、YE、YR；OpenSSL 拒绝后，Windows 回退校验获得服务端完整证书链和主机名 |
| `gpu/CMakeLists.txt` | 增加独立 `windows-trust-test` 目标，默认不随完整构建运行；沿用现有 OpenSSL 和 Windows 系统库 |
| `tests/test_windows_trust.cpp` | 新增根证书、HTTPS 成功及错误主机名拒绝等测试；此次仅执行离线部分 |
| `launcher/bbport_launcher_win.py` | 版本号由 1.9.5 改为 1.9.6；The Hunt 不使用这套上游 GUI |
| `launcher/bbport_news.py` | 更新说明 |
| `docs/WINDOWS_TROUBLESHOOTING.md` | 说明证书失败来源及此次修复 |

## The Hunt 适用范围

信任辅助函数由游戏 HTTP 层及 The Hunter's Dream 后端使用。The Hunt 连接自建 HTTPS 游戏服务时，游戏 HTTP 层仍可能使用它；连接普通 HTTP 服务时不存在该 TLS 验证环节。它不修改 shadNet 协议、BBSC Relay、轮询间隔或服务端账号系统。

`run.py`、`scripts/mods.py`、`scripts/pc_menu.py`、`scripts/patches.py`、角色保存位置及我方所用的上游 Python 辅助模块均未变化。The Hunt 的 GPU helper 路径、隔离保存目录、菜单汉化层和 FSR 4.1.2 补标记适配原样通过检查。BBSC 与游戏本体版本锁定没有变化。

升级需要构建并分发新核心；单独更新用户的普通版启动器不会替换 The Hunt 随包附带的核心。此次核对没有构建完整新核心，因此不声称已有 1.9.6 包或其游戏内联网结果。

## 已完成检查

1. 用隔离 Git 仓库抓取精确标签并对比对象，未修改原 1.9.5 工作目录。
2. 对 1.9.6 标签中实际运行器和辅助脚本执行现有检查：7 项运行器／保存目录／图形同步测试全部通过。
3. 对该标签执行 19 项退出与 PC 菜单兼容测试，全部通过。
4. 使用 CLANG64 构建上游新增的 Windows 证书测试，`BB_TEST_OFFLINE=1` 执行通过：四份根证书名称、SHA256、自签名关系及内存信任库创建通过。

没有执行单测在线分支，没有把离线通过当作当前电脑或 comp 的 HTTPS 游戏连接证明；也没有启动真实游戏、修改证书存储、账号、存档或服务器。

## 复用的实机反馈

2026-10-30 用户确认：昨晚通过当前候选内置开服，经 Tailscale 能成功入房。此前另有 comp 在旧亚楠联机的反馈。当前 1.9.5 WPF 候选的这些路径按已确认范围记为通过，不再要求重复整套 Boss、徽章、掉落和退出矩阵。

下次升级主要补完整核心构建、运行依赖与证书路径检查。只有出现新的具体异常或改动相关路径时，才提出说明缺口的定向实机检查。

## 后续授权合入

2026-10-30：已按用户授权构建 1.9.6 核心并合入奖杯比例修正。新增信任代码对应的 HTTP 与 THD 编译单元及 `bb-probe.exe` 重新编译／链接；未改文件复用隔离复制的构建缓存，原 1.9.5 源码和缓存保持原样。

234 项 Python 检查和原生 WPF 构建检查通过。上游证书测试的在线分支也已通过：仅使用随程序携带的根证书访问公开 `ss.info` 返回 200；Windows 完整证书链校验接受正确主机名；故意传入错误主机名返回 `0x800b010f` 并拒绝；游戏使用的 `TrustWindowsRoots` 路径返回 200。这些是公开 HTTPS 接口及证书库的验证，未启动真实游戏或使用玩家账号。

整合包仍是未发布候选；没有替换玩家已初始化安装或部署服务器。见 [整合说明](desktop-196-candidate.md)。
