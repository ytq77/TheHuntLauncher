# 当前角色、恢复详情与选项预览

从安装目录运行。界面调用 Python 时加 `-B`。文中的槽号从 0 起算；对玩家显示时加 1。

## 首页

`python -B world_recovery.py recovery-details` 只读当前安装。成功退出 0，返回 `ok、schema、status、slots、reasons、current_action`。现有 `recovery-status` 继续提供简短状态。

- `schema` 为 1。`status` 为 `clear`（无恢复待办）、`safe`（已有凭据或等待游戏更新备档）、`blocked`（有待处理项）。启动时仍独立核验。
- `slots` 按槽号列出十项。`primary` 和 `backup` 各含 `state、name、level、echoes、issue`。代次状态为 `readable、empty、missing、unreadable`。`empty` 是已确认的空槽文件；`missing` 指缺少该代文件。`unreadable` 表示当前解析器无法识别已有内容。`issue` 可为 `unrecognized、stats_unavailable、name_unavailable`。读取不到的姓名或数值用 `null`，实测零用 `0`。
- `identity_relation` 为 `same、different、unknown`。`summary_equal` 只在同角色且姓名、等级、回响齐全时比较这三项。`bytes_equal` 比较两份实际存在的文件，两者都在时才返回布尔值。个人世界的核验结果取自 `reasons`。
- `reasons[]` 每项有 `code、state、slot、message`。代码为 `world-recovery、choice-review、import-pending、restore-pending、choice-pending、save-operation-pending、conversion-pending、primary-check-failed`。每条对应实际读到的原因。未关联到槽号时 `slot=null`。世界记录使用 `verified、waiting-backup、review-required`；其他阻断项使用 `blocked`。
- `current_action` 默认 `null`。当真实处理计划给出 `accept-bound` 或 `use-current` 时，包含 `id、slot、risk、digest、selection、requires_confirmation`。前者会停止该角色这次未核验的旧世界恢复，门、Boss、商店或任务可能保留联机影响。后者结束独立的重新选择提示，继续正常启动检查。读取、接受当前进度和提交修复是不同操作。
- 无法稳定读取、游戏仍在运行或安装被其它操作占用时，退出 1 并返回 `{"ok":false,"schema":1,"status":"unknown","retryable":true,"error":"…"}`。等占用结束再查询；缓存的备份不能代替当前读数。

## 处理页

`python -B save_resolution.py` 从标准输入读取 JSON。`{"action":"plan"}` 返回默认选择。切换来源时，把列表中同一行的 `kind、id、fingerprint` 作为 `selection_kind、selection_id、selection_fingerprint` 一起发给 `plan`。

`options[].effects` 包含 `keeps、loses` 两个文字数组、`reversible`（`available、scope`）、`world_after` 和 `roles`。每个选项覆盖十个槽位，`roles[]` 包含 `slot、before、after、primary、backup`。`before/after` 各有两代的状态和可见读数。顶层的 `primary/backup` 是这两代各自的比较，含 `state、level_delta、echoes_delta`。差值按“选择后减选择前”计算。身份不同用 `different-character`，一端为空或缺失用 `absent`，解析不支持用 `unreadable`，身份或数值未知用 `unknown`。可比较用 `comparable`。数值字段独立核对；不满足身份或数值条件的差值为 `null`。

| 选项 | world_after | 实际影响 |
| --- | --- | --- |
| accept-bound | bound-retired-unverified | 保留全角色，只停止这次绑定的单角色世界恢复 |
| use-current | resume-checks | 结束重新选择提示，保留正常等待事项 |
| restore-all | pending-retired | 所有角色采用所选整套备份，当前待补联机记录归档 |
| restore-case | restored-needs-review | 换回处理前全部角色及当时的联机记录，重新进入复核 |
| accept-all | all-retired-unverified | 保留所有当前角色，结束所有旧联机待补记录 |
| restore-generation-N | needs-review | 只用该槽游戏备档替换主档，联机待办重新检查 |
| reimport-ordinary / restart | pending-retired | 采用所选原版全部角色，或封存后从空档开始 |
| preserve / choose-ordinary | unchanged | 保全现场，或只选择原版来源 |
| abandon | needs-review | 封存中断现场，保留当前角色，重新检查 |

中断的处理继续由 `pending.can_resume/can_abandon` 决定可用操作；可用时分别带 `resume_effects/abandon_effects`。继续的预览从此刻的实际文件与原事务已准备的目标计算。完整换回按处理前的整套现场为单位，期间两个版本的进度不会合并。

执行时沿用现有 `apply` 协议，需明确确认，传递选项本身的 `digest`（无则取计划的）、`option_id` 和本计划返回的来源三元组。可用操作、当前文件和来源在执行时重新核对。`restart` 仍需二次确认。中断继续使用原 `fingerprint`，放弃使用当前 `abandon_digest`。首页查询和预览不会执行这些动作。

退出提示先显示进程的实际返回码。能将本局恢复请求、角色槽号和身份绑定到磁盘主档时，显示“当前主档读到”的姓名、等级和回响；绑定不足时说明无法确认。后续按原有世界核验结果提示复核、等待游戏更新备档或已完成核验。
