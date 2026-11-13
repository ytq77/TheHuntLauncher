# 服务端安全边界与离线回归

此候选面向互相信任的玩家设备；私服 Relay v2 已认证账号并按原生房间隔离。部署仍将 TCP 31413、31415、20443、20444 与 UDP 31414 限制到实际玩家设备，使用既有 LAN/VPN；工具不修改网络或防火墙。

监听必须填写本机的明确 IPv4，拒绝通配地址与普通公网地址。支持回环、RFC1918 局域网、Tailscale 的 100.64/10 和 Radmin VPN 的 26/8。26/8 只适用于实际 Radmin 网卡，不能视为任意可信公网地址。地址范围检查只是部署护栏；私网地址、VPN 成员身份以及可达端口均不能证明是哪位游戏玩家。

## 已实施的防护

- `online-data.json` 只有确实不存在时才从空数据开始。JSON 损坏、结构异常、读取失败或符号链接均拒绝启动，原文件保持不变。保留原件和备份后由管理员核对恢复；不会自动覆盖或隔离私人数据。
- 管理窗口停服重置密码时，在同一个 SQLite 事务里更新密码、盐值和新 64 字符 token，并清除 reset_token。修改前的完整性校验备份保留旧状态，备份应按凭据材料保管；恢复旧备份会恢复其中的旧凭据。
- WebAPI 的 bearer 验证同时检查 `banned = 0`。原生封禁接口还会轮换 token，解封不会让旧 token 复活。直接改 SQLite 的 banned 标记也会立即阻止 WebAPI 验证，但绕过接口直接解封不会自动轮换凭据。
- 留言和血迹删除必须包含正整数 UserId，并与记录所有者一致；缺少、零值、布尔、字符串、非整数或其他所有者不会删除。召唤删除要求 SessionId 与正整数 UserId 同时匹配；Force/SeamlessLeave 也不能省略或绕过这些条件。有效的旧客户端离房流程保留。
- Relay 拒绝来自上述支持网络范围之外的连接；游戏 HTTP 和 shadNet 登录监听也检查来源。STUN 只有在 NPID 对应当前已登录连接、且 UDP 来源 IPv4 与该登录连接观察到的来源相同时，才能更新端点。登录和断开会清掉旧端点。未登录的 STUN 发现请求仍可收到自身端点回复，以保留两种封装的发现兼容性。

## 认证与兼容边界

私服 Relay v2 使用原生账号令牌、随机挑战、会话 MAC 与递增序号，并从原生房间读取成员和房主权限。四类召唤接口验证 Bearer，将记录绑定到认证账号；正文公共游戏 ID 不再充当私服身份。入房前位置只送实际选中的账号，具有代次、期限与撤销。见[认证协议与兼容说明](relay-auth.md)。

旧公共内容接口和 STUN NPID 仍有自报字段；来源 IP 检查不能阻止同一 NAT、同机或可伪造 UDP 源地址的设备冒用端点。NP 与 HTTP 仍是原有明文传输，Relay 完整性验证不提供内容加密。认证版客户端与私服需一起升级，不对旧私服静默回退。

公网或通配监听现在会拒绝启动；26/8 以外的公网形状 VPN 地址也不受支持。分流使登录 TCP 和 STUN UDP 使用不同来源地址时，端点更新会被拒绝，可能影响招募；应让两种流量使用同一可信 VPN 路径。只在登录前发一次 STUN 且不重试的客户端也不会登记端点；实际客户端的发送时序尚未验证。严格 UserId 检查可能拒绝缺字段或以字符串传 ID 的旧变种客户端。重置密码后依赖旧 token 的客户端需要重新以密码登录。

## 可重复验证

上游版本与下载摘要保持 `recipe/sources.json` 中的锁值；没有改动固定上游、外部仓库或临时下载基线。`recipe/security.py` 对固定源码检查精确锚点，拒绝不匹配输入，可重复应用。Windows 的 `build-native.ps1` 在源码准备后显式调用它；Linux Dockerfile 在解包校验后调用同一补丁，并运行同一原生回归。BBSC Python 安全补丁通过 `recipe.prepare.patch_gameserver` 进入两个生成链。

离线 Python 回归只读固定下载缓存，使用临时合成 JSON/SQLite、假连接和假进程；不会下载、启动服务或发送数据包：

```powershell
$env:SERVER_SECURITY_CACHE = 'C:\path\to\verified\downloads'
python -B -m unittest discover -s server/tests -p test_security.py -v
```

`tests/stage_native.py --cache 缓存 --output 新目录` 校验并解包固定 shadNet 后应用安全补丁。独立 `tests/CMakeLists.txt` 可通过 SHADNET_ROOT 指向该目录，SHADNET_PROTOBUF 指向对应 Protobuf 源码、SHADNET_GENERATED 指向已有生成头文件，构建离线原生用例并编译其他改动单元；随后 `ctest --test-dir 构建目录 --output-on-failure`。完整构建链会自行生成 Protobuf 文件。

早期离线审查的 14 项 Python、2 项 CTest 和 Windows 构建记录保留为历史证据；认证版增加真实原生服务与隔离账号的协议检查。最终验证范围及 Linux 结果统一记录在[本轮加固说明](../../docs/hardening-20261105.md)，不把协议检查当作真实游戏验收。
