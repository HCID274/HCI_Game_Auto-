# WW_Auto

鸣潮无人值守日常自动化：本地虚拟 USB HID、网易 UU、OK-WW 命令行、
当前运行日志验收、DeepSeek 汇报、飞书卡片和星铁联动调度。

## 日常入口

```powershell
uv sync
uv run wuwa-auto elevate daily
```

独立补跑入口：

```powershell
uv run wuwa-auto elevate farm-echo
uv run wuwa-auto elevate weekly-garden
```

流程只读取本轮新增的 OK-WW 日志。无论成功或失败，都会先保存截图和日志，
随后关闭鸣潮与 OK、断开鸣潮加速并退出 UU。

讨伐强敌若出现角色死亡，宿主恢复流程只在本轮同时确认 `char dead` 与
`Revive Failed` 后介入：保留截图，停止已失败的 OK 工作进程，复用 OK 的副本
退本与传送回血能力，再按重开界面或成功吸收声骸等击杀证据计算剩余次数。补跑
期间只临时修改 `Repeat Farm Count`，结束后原字节恢复为固定的 5；最多补跑一次，
第二次死亡只做安全退本回血，避免无限循环。该流程不修改 OK-WW 源码，也不消耗
复苏药。

## 汇报规则

飞书卡片按“日常、周常、后续事件、异常记录”组织。

- 邮件完全不进入报告。
- 只写本轮真正执行的事项；检查后发现早已完成的项目不写。
- 先约电台只有进入实际奖励领取分支时才写。
- 基础事实保留“讨伐强敌第2项”等原始编号。
- 讨伐次数只按可见重开界面或成功吸收声骸计数；`start wait in combat` 和
  `farm echo ... None` 都不能单独证明击杀。死亡后成功补跑时，同时写明退本
  回血与实际补跑次数。
- Boss、角色、用途及措辞偏好由 `UserContext` 下的 Markdown 补充。
- 程序决定事实和状态；DeepSeek 只能润色及依据明确上下文追加说明。
- DeepSeek 不可用时自动使用确定性中文报告。
- 未显式设置 `WUWA_FEISHU_SEND_ENABLED=true` 时只保存飞书预览。

可以直接编辑：

- `UserContext/汇报偏好.md`
- `UserContext/培养背景.md`

历史运行预览：

```powershell
uv run wuwa-auto report 20260727_001843
```

## 星铁联动

正式总控位于总仓 `orchestrator/run.ps1`；本目录的
`scripts/run_daily_chain.ps1` 仅为兼容转发器。总控使用全局锁按顺序执行：

1. 星铁日常
2. 星铁安全清理
3. 鸣潮日常（星铁业务失败时仍继续）
4. 鸣潮自身收尾

只有星铁桌面环境无法安全清理时才跳过鸣潮，避免两个游戏或两种 UU 加速
发生冲突。注册脚本会创建每天本地时间 05:30 的 `Game_Daily_0530`，并删除
已退役的 `StarRail_Main_0600` 与 `StarRail_Cleanup_0800`。

幻梦游园不再放在每日 OK-WW 配置中，而由
`Game_Wuwa_WeeklyGarden_Sunday` 每周日 08:00 独立执行。两项计划任务复用同一
全局互斥锁，周日任务不会与延长运行的每日总控抢占桌面。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_chain_task.ps1
powershell -ExecutionPolicy Bypass -File scripts/register_weekly_garden_task.ps1
```

计划任务要求当前用户的交互式桌面会话，并以最高权限运行。
