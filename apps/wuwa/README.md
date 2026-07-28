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
退本与传送回血能力，再按实际成功吸收声骸数计算剩余次数。补跑
期间只临时修改 `Repeat Farm Count`，结束后原字节恢复为固定的 5；若再次死亡，
仍先安全退本回血，并在同一个 1 小时截止时间内继续补剩余吸收数。截止时间防止
无限循环。该流程不修改 OK-WW 源码，也不消耗复苏药。

讨伐后续任务只有实际吸收声骸达到 `5/5` 才判定完成。击杀、重开界面和
`farm echo ... None` 均不增加该完成进度；日常主流程不足 5 次时会在清理前补齐。
吸收阶段最多运行 1 小时，到达上限后按实际吸收数报告部分完成并执行完整清理。
日常补齐、死亡恢复及其后续重试属于同一个业务事务：中间尝试只保存日志和截图，
不发送飞书；达到 `5/5`、耗尽 1 小时或遇到不可恢复错误后，才以合并结果发送
唯一一条最终通知。独立人工补跑是新的业务事务，不用于拼接已结束的日报。

## 汇报规则

飞书卡片按“日常、周常、后续事件、异常记录”组织。

- 邮件完全不进入报告。
- 只写本轮真正执行的事项；检查后发现早已完成的项目不写。
- 先约电台只有进入实际奖励领取分支时才写。
- 基础事实保留“讨伐强敌第2项”等原始编号。
- 讨伐次数只按可见重开界面或成功吸收声骸计数；`start wait in combat` 和
  `farm echo ... None` 都不能单独证明击杀。完成状态只认实际吸收声骸 `5/5`；
  死亡后成功补跑时，同时写明退本回血与实际补吸收次数。
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
全局互斥锁；周常遇到仍在运行的每日总控时最多等待 3.5 小时，释放后才启动。
日常与周常各自收敛并各发一条最终飞书，不共享日志切片或报告。日常计划任务仍
以 3 小时为硬保险；周常计划任务的 8 小时上限包含锁等待和自身运行时间。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_chain_task.ps1
powershell -ExecutionPolicy Bypass -File scripts/register_weekly_garden_task.ps1
```

计划任务要求当前用户的交互式桌面会话，并以最高权限运行。
