# 游戏自动化项目开发与运维交接（Hand off）

> 快照日期：2026-08-13（Asia/Tokyo）  
> 基线分支：`main`  
> 基线提交：`d46387e`（`fix(wuwa): validate target page and resume daily`）  
> 适用范围：崩坏：星穹铁道（下称“星铁”）、鸣潮、UU 加速器、Windows 桌面自动化、程序生成的日报与飞书通知（2026-10-01 起不再调用 DeepSeek，见 §7）。

本文不是一份只讲“怎么启动”的 README，而是当前项目的工程交接、事故复盘和运维准则。后续维护者应先读本文，再读对应应用的 README、架构文档、运行结果和测试。文中的“已完成”必须有结构化结果或日志证据支持；“推断”与“待验证”会明确标注，不能把猜测写成确定性事实。

## 1. 当前状态摘要

项目是一个单仓多应用的 Windows 游戏自动化系统。根 `pyproject.toml` 定义单一 uv workspace，成员包括：

- `apps/starrail`：星铁业务、March7thAssistant（M7A）生命周期、UU、训练计划、日报。
- `apps/wuwa`：鸣潮业务、OK-WW 外置上游、FarmEcho、DailyTask、周常、虚拟 HID、日报。
- `packages/game-automation-core`：跨游戏 Windows/UAC、日志、汇报传输等公共基础设施。
- `orchestrator`：只通过 CLI、退出码和清理后置条件编排两个应用，不导入游戏内部模块。
- `config/automation.psd1`：正式调度、命令、互斥锁和时间上限的权威配置。

截至本快照：

- 2026-08-12 的星铁没有失败；星铁 daily 和 cleanup 都返回 0。当天总控最终码 20 来自鸣潮，不能把聚合退出码反向归因给星铁。
- 鸣潮 8 月 12 日的“无音清剿”页面误选问题已修复，并用 `daily-resume` 完成一次真实端到端验收：进入正确页面、打完两轮、消耗 120 体力、日活到 140、领取奖励、清理成功、DeepSeek 和飞书成功。
- 2026-08-13 星铁仍成功；鸣潮三轮新 Worker 均为 0/5，DailyTask 被跳过。现有证据不是“服务器维护页”，而是客户端启动阶段出现“获取远程配置失败”。更重要的是，脚本记录为“重试”的模板/点击位置实际落在左侧“退出”按钮附近，点击后客户端消失，随后 Worker 仍等待约 45 分钟。这是当前最高优先级问题。
- 截至 2026-08-13 的网络检索，没有找到鸣潮官方在当天进行版本停服维护的公告；可查到的 3.5 版本维护公告对应 7 月 10 日，而非 8 月 13 日。因此“今天更新维护导致失败”目前没有证据，最多只能把远程配置服务异常列为外部可能性，不能以此结案。

## 2. 不可破坏的工程边界

### 2.1 业务事实与自动化动作分离

程序解析器拥有事实裁决权，日报文字也由程序按固定规则生成。成功、失败、完成数量、消耗体力、清理状态、是否发送飞书，都必须来自当前运行切片的确定性证据，不得用语义猜测改写结构化事实。

同样，宿主负责启动、准入、恢复、证据和清理；上游 M7A/OK-WW 负责游戏内业务。尤其鸣潮战斗逻辑属于 OK-WW：宿主不应模拟攻击、索敌、放技能或自行设计战斗策略。虚拟 HID 只用于经过明确设计的外围入口、聚焦或恢复动作，且必须有正向页面后置条件。

### 2.2 “Fail fast”不是“完全不恢复”

本项目采用的是“有界恢复后快速暴露根因”：

1. 偶发波动允许重试，连续无进展上限为 3。
2. 只要 FarmEcho 的真实吸收进度仍增长，就重置无进展计数，允许继续启新 Worker，直至累计吸收 5 个声骸。
3. 战斗劣化但没有死亡证据时，可以重新触发上游战斗状态，或做一次完整客户端/Worker 重启，使上游恢复正常；不能调用 HID 接管战斗。
4. 只有识别到复活框、挑战失败页，或精确失败标记并有队伍 HUD 佐证，才进入死亡/安全世界恢复。普通丢目标、技能失败、短暂停滞都不等于角色死亡。
5. 恢复仍失败时，保存截图、日志、结构化阶段和进程状态，抛出明确错误；禁止 broad catch 吞掉错误后从未知页面盲点通用按钮。

### 2.3 不做全量回滚

事故修复以最小、可验证的外科手术为原则。实习生或历史提交中可采纳的部分应保留，例如：运行切片、结构化结果、进度驱动恢复、受限重试、统一汇报、定向续跑。只回退被证据证明有害的路径，不因为一次失败抹掉整个迭代。仓库保留了若干历史基线（如 `a0f3d94` 的 progress-driven 恢复，以及客户端重启实验相关提交），用于比较而非无脑回滚。

### 2.4 启动所有权必须唯一

鸣潮的正确启动顺序是：清理残留官方客户端/启动器与 Worker → UU 确认鸣潮卡片已加速 → 启动 OK-WW → 由 OK-WW 的 `start_exe=True` 唯一拉起官方客户端。不能先手动打开鸣潮，再打开 OK-WW；这既破坏进程归属和窗口绑定，也可能触发官方对异常组合的检测。

游戏需要更新时是唯一例外：先执行 `client prepare`，人工完成官方更新并关闭客户端，然后把下一次冷启动所有权归还给 OK-WW。宿主只能在 OK 窗口稳定后聚焦一次，不点击登录或日常业务按钮。

## 3. 调度、互斥和退出码

正式每日任务为 `Game_Daily_0530`，每天 05:30 启动，计划任务执行上限 3 小时。链路是：

1. 星铁 daily。
2. 星铁 cleanup。
3. 鸣潮 daily。
4. 鸣潮 cleanup 与最终汇报。

星铁业务失败本身不阻断鸣潮，但星铁 cleanup 失败会阻断桌面交接。总控等待自己直接启动的 CLI 进程，取真实退出码，不通过轮询后代进程猜测完成。无论成功或异常，`finally` 都要在 `runtime/orchestrator/<run_id>/result.json` 落盘并释放互斥锁。

全局互斥锁是 `Global\HCID274_GameAutomation`。周日鸣潮花园任务为 `Game_Wuwa_WeeklyGarden_Sunday`，08:00 启动，锁等待 210 分钟，实际执行上限 8 小时。旧架构文档曾写 4 小时，已经过期；`config/automation.psd1` 和实际计划任务的 8 小时才是权威值。

总控最终码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 全链路成功 |
| `10` | 星铁业务失败 |
| `20` | 鸣潮业务失败 |
| `30` | 星铁清理或桌面交接失败 |
| `40` | 鸣潮清理失败 |
| `75` | 全局锁冲突/等待超时 |
| `99` | 总控自身异常 |

必须同时查看 `starrail_exit_code`、`starrail_cleanup_exit_code`、`wuwa_exit_code`、`wuwa_cleanup_exit_code`。例如 8 月 12 日 `final=20`，但星铁两个阶段都是 0，所以结论是“鸣潮失败”，不是“两款游戏都失败”。这是本轮调查中最重要的归因纠错之一。

计划任务应为 Enabled/Ready、Interactive、Highest、Hidden PowerShell、`IgnoreNew`、`StartWhenAvailable`。退役的 `StarRail_Main_0600`、`StarRail_Cleanup_0800` 不应存在。旧 Universe 任务目前禁用且可能仍指向旧路径，启用前必须重新注册，不能直接点启用。

## 4. 星铁链路与经验

### 4.1 M7A 生命周期和完成判定

当前 M7A 通过 `March7th Launcher.exe main -e` 启动。每轮开始前记录当日日志字节偏移，只解析本轮新增切片；随后等待游戏进程、可见窗口和本轮 Assistant。不能扫描整份旧日志后把昨天的“完成”当作今天成功。

`main` 不设武断墙钟硬超时。看门狗依据日志空闲与 CPU 判断：日志约 10 分钟不增长，或 CPU 低于约 2% 持续 15 分钟，才判定卡死。卡死前先保存证据，只终止确认属于本轮的 Assistant，保留游戏和 Launcher 便于现场调查。

成功必须同时满足当前切片中的业务完成语义和正常停止边界。“每日实训已完成”可成功，“每日实训未完成”应失败；“尚未刷新”只能按明确定义处理，不能用模糊关键词覆盖失败。凌晨 05:00 前还要考虑日常尚未刷新，不应无业务证据地报告完成。

### 4.2 UU 和网络边界

星铁在 UU 加速前后都检查公网 DNS 和 TCP 443，避免 Fake-IP 或网络守卫导致 M7A 启动异常。复用已有 UU 会话时，必须同时识别星铁卡片身份和“停止加速”状态；只看到通用按钮不能证明目标游戏已经加速。失败时可以断开并整轮重试，最多 3 次重启，即初始尝试加 3 次恢复尝试。

清理顺序是关闭游戏/M7A → 点击 UU 断开 → 精确终止全部受管 UU 进程。任何受管残留都应阻止向鸣潮交接。不能用模糊进程名大范围杀进程，更不能把别的用户进程当作本轮资源。

### 4.3 星铁 8 月 12 日和 13 日实况

8 月 12 日 M7A 实际完成了每日实训、体力消耗和委托补做，日报为 `completed`，DeepSeek 200、飞书 200，清理完整。8 月 13 日报告同样显示每日 500/500，体力 255→15，计划副本完成 6 次；剩余体力 15 小于下一项所需 40，跳过下一项属于正常资源不足，不是失败。今天总控中的星铁 daily 和 cleanup 仍为 0。

历史常见问题包括：防火墙弹窗导致前置失败、DNS/Fake-IP 导致 code 24、旧逻辑把“尚未刷新”或未达标判断错误。排查顺序应是：总控分段结果 → 星铁应用日志 → 报告归档 → 按归档 offset 读 M7A 原始切片 → evidence → 计划任务。

M7A 实机版本已从旧审计文档中的 `v2026.6.8` 演进到 `v2026.7.26`。升级上游后必须重做日志覆盖审计；不能假设旧 marker 永久有效。

## 5. 鸣潮链路与核心经验

### 5.1 OK-WW、UU 与虚拟 HID

OK-WW 是外置上游，当前实机版本为 `v3.5.27`，本仓不直接修改其安装目录。宿主通过运行时包装、结构化日志和有限入口钩子扩展行为。每次升级 OK-WW，都要重新验证方法签名、日志 marker、窗口类名、模板和页面布局。

UU 复用规则与星铁一致但目标必须是鸣潮：只有“鸣潮卡片身份 + 停止按钮”能证明加速已生效。通用启动按钮必须先绑定到同一卡片，所有重试共用一个有界预算。清理必须确认 OK、游戏和 UU 均退出。

虚拟 HID 通过本机随机 token 的 localhost 服务执行真实输入，并用系统光标位置闭环确认到达。它不是战斗机器人。每一次 HID 行为应记录：目标 frame 坐标、窗口原点、换算后的 absolute 坐标、实际 reached 坐标、点击前后截图和后置条件。

### 5.2 FarmEcho 的完成和恢复语义

正式目标始终是吸收 5 个声骸，历史配置曾被误改为 1 次，必须恢复并保持 `Repeat Farm Count=5`。击杀、重新挑战、进入战斗或上游返回 `None` 都不计数，只有可审计的吸收确认才增加业务进度。

恢复按“总进度单调增长”管理：首 Worker 4/5，后续 Worker 再补 1，应合成为 5/5 成功，而不是把第二个 Worker 的局部数量当作总数。每获得一个新吸收，重置无进展计时和连续无进展计数；只要仍增长，就可以继续新 Worker，直到 5/5。只有连续 3 个无进展窗口才停止。`attempt_limit` 是战斗循环预算，不是完成数量，不能混为一谈。

角色死亡边界必须严格。复活框、挑战失败页，或者精确 `failed switch chars` 且队伍 HUD 佐证，才可认定死亡/队伍不可用。恢复只负责退出副本、回到安全世界态、由上游锚点加血、重新开一个干净 Worker；恢复动作本身不等于业务成功。

### 5.3 8 月 12 日“无音清剿”事故

早晨正式批次的首 Worker 完成 4/5，下一 Worker 补 1，FarmEcho 总目标实际已经完成。随后 DailyTask 在无音清剿页面失败：旧宿主点击了 OCR 识别到的“无音清剿”行，却没有确认页面真的切换；接着又把底部无关的“前往查看”当作目标导航，落入“合鸣图鉴”，上游找不到 `boss_proceed`。

调查期间尝试过“删掉前往查看、直接交回上游”的较小修改，但它只覆盖页面成功切换的情况。若点击只获得焦点、正文仍停在“素材获取/所需材料”根页，通用 `boss_proceed` 会匹配右侧培养材料的“前往”，例如 y≈780 的按钮，随后导航到错误地图并在 `team_start_challenge` 超时。因此通用按钮存在、数量正确、左栏白色描边都不是可靠后置条件。

最终修复采用语义状态机：

1. 在限定左侧区域定位并点击“无音清剿”行，点击位置采用实际行中心而不是模糊文字边缘。
2. 最多尝试 3 次。
3. 页面必须同时满足：内容区标题为“合鸣套装筛选”；正文至少命中“无音区”或“声骸套装”语义行；右侧限定区域存在 `boss_proceed`。
4. 任一条件不满足，不允许交给上游扫描通用按钮。
5. HID 点击后的 OCR、模板或坐标检查异常必须转成 `BookTabSelectionError`，不能被 broad catch 吞掉后 fallback。
6. 宿主只验证页面，不点击最终 boss；目标 index=3 仍由上游选择，战斗仍由上游执行。

第一轮定向实机因为标题 OCR 区域过窄产生 false negative，系统安全失败，没有误点、没有消耗体力；扩大标题 ROI 后，`20260812_212242_daily_resume` 首次确认成功，随后上游点击正确 `boss_proceed`、进入队伍挑战、完成两轮 Tacet、消耗 120 体力、日活从 130 增至 140、完成奖励/邮件/电台并清理。该轮 DeepSeek 输入约 28,208 tokens，飞书返回 200，整日汇总为绿色 completed。鸣潮测试当时为 211 passed，compileall 成功。

这次经验说明：安全的 false negative 可以接受并观察，错误页面上的 false positive 不可接受。页面后置条件必须表达业务语义，而不能只表达“看到了一个长得像按钮的东西”。

### 5.4 `daily-resume` 为什么存在

测试失败在日常中段时，不应每次重跑已完成且耗时的 Nightmare/FarmEcho。`daily-resume` 仍运行父 `DailyTask`，但在本进程内临时设置 `Farm Nightmare Nest for Daily Echo=False`，并从 Additional Tasks 中临时移除 `Auto Farm all Nightmare Nest`，通过 `try/finally` 恢复内存配置，不写坏 installed JSON。

它保留 DailyTask 的第 3 个 Tacet 目标、每日体力语义、奖励、邮件、电台和 `Daily Task Completed`，且结果身份仍是 `daily`，因此同日 rollup 能合并早晨的 FarmEcho 5/5 与最新 Daily 成功。恢复链必须携带 resume 标志，禁止一次异常后偷偷退回完整 Daily。

不能用直接运行 `TacetTask` 替代：直接任务可能读取第 1 个配置目标，按普通体力规则继续刷，不领取日常奖励，也不会被整日汇报认作 Daily 成功。定向命令是：

```powershell
uv run --project apps/wuwa wuwa-auto elevate daily-resume
```

如果某一步已有确定性成功证据，后续只模拟或续跑断点步骤，不重复消耗整条真机流程。这既节省时间，也避免用一次新失败污染已验证结果。

## 6. 显示器、DPI 与坐标调查

本机存在一个 2560×1440 横屏主显示器和右侧竖屏。即使竖屏物理断电，Windows 仍可能因为 EDID/缓存保留逻辑显示器，所以 `all_screens=True` 的 4000×2560 证据图会包含右侧桌面；这不等于游戏运行在另一显示器。

8 月 12 日失败和成功运行都显示游戏 HWND 位于 `DISPLAY1`，客户区原点 `(0,0)`、尺寸 2560×1440。问题点击的 frame 坐标 `(456,1023)` 换算 absolute 后仍是 `(456,1023)`，VIIPER 实际到达约 `(453,1021)`；成功修复后目标 `(614,1023)` 也准确到达。窗口 API 使用 `GetClientRect`、`ClientToScreen` 和 per-monitor DPI，未发现统一 +2560 横向偏移或缩放比例错误。

因此这次无音误选不是“游戏跑到另一个屏幕”或“NVIDIA 配置改变”，而是页面切换未确认、模板语义过宽。显示器仍是环境风险：部分代码假设主屏 2560×1440 且游戏位于左上；后续应保留窗口原点、虚拟桌面边界和 after-click evidence，但不能用显示器猜测替代日志证据。

## 7. 飞书日报（程序生成）

完整设计见 [docs/汇报架构.md](docs/汇报架构.md)。2026-10-01 起移除 DeepSeek 改写层：
它每天消耗 1.2 万到 6 万 Token，只是把程序已有的事实换个说法，失败日还会被丢弃；
星铁 09-29 的 AI 报告把教程弹窗卡死误判成“内存不足”。现在卡片完全由程序生成。

### 7.1 数据边界

每轮报告必须只读取当前运行切片；整日汇报再按稳定阶段键合并同一自然日的最新有效结果。父 Daily 失败不能覆盖已完成的 FarmEcho 5/5；4/5 加恢复 Worker 的 1/1 必须显示“吸收声骸 5/5 … 已自动恢复”，同时保留后续 DailyTask 的真实失败。重放早先的运行时只合并当时已结算的运行。

飞书失败不能修改游戏业务退出码。

### 7.2 卡片规则

一张卡片：标题（结论 + 完成时间）、用时、异常记录（只在没全部完成时、排最前）、今日任务（每任务一行，行首 ✅/❌/⚠️/⏸️/ℹ️）、星铁另有养成待办和提醒。任何 ❌/⚠️ 行或异常都会把“全部完成”降为“部分完成”。失败原因由应用翻译成中文（星铁 `STAGE_TEXT`，鸣潮 `reasons.py`），未收录的原因原样截短展示，不猜测。

飞书只在完整链路末尾发送一张最终卡片；`starrail-auto report` / `wuwa-auto report` 用归档重建卡片做本地预览，不发送。归档中的报告 JSON、应用日志里的 HTTP 状态和 `sent` 标记要一起看，因为部分旧星铁归档不持久化 `sent`，仅看 JSON 可能误判未发送。

## 8. 2026-08-13/14 鸣潮晨间故障：已定位、修复并真机验收

### 8.1 三天三处死因（不要合并成一个 bug）

- 8-12：DailyTask 无音清剿页面导航误选（`d46387e` 语义状态机修复）。
- 8-13：启动器“获取远程配置失败”弹窗重试模板误点“退出”（`aa571c8` 右半区钉住修复）。
- 8-14 晨：FarmEcho 首 Worker 2/5 战斗劣化返回；补跑 Worker 报“队伍成员不可用”（角色已倒地，界面已切到挑战失败页）；恢复 Worker 只等 5 秒“战斗中退出控件”就抛错，而恢复流程首次失败即 break——3 次无进展预算和客户端重启都没用，11 分钟放弃，DailyTask 被跳过。

### 8.2 修复内容（`d5261cf`）

- 恢复 Worker 以**当前可见界面**为准做三态重分类（挑战失败页 → 复活框 → 大世界），不再信任几秒前的 Worker 异常分类；每次动作前重新采样。
- 恢复失败计入共享无进展计数（上限 3），不再首次失败即 break；失败触发一次性 OK 冷启动重建干净入场边界；合成结果显式记录 `final_state_safe`。

### 8.3 真机验收（2026-08-14 20:34–21:10，wuwa-daily 模式全链）

冷启动 → UU → OK-WW → FarmEcho：首 Worker 1/5 提前返回（真实复现晨间失败模式），恢复层启动第二 Worker 连补 4 个，20:49 合成 5/5；DailyTask 完成（活跃度奖励领取已验证）；cleanup 全绿（OK/游戏关闭、加速断开）；DeepSeek+飞书 200。总控 exit 0，总时长 35 分 50 秒。证据：`runtime/orchestrator/20260814_203450/`、`apps/wuwa/runtime/runs/20260814_203538_*`。

### 8.4 遗留事实

用户明确要求**不新增**计划任务（2026-08-14 曾实现 06:35 同日重跑任务并已注册，随后按用户要求注销并完整 revert，见 git 历史 `5a4f638`→`d8c896a`）。因此当前语义：05:30 单次定输赢；若恢复层耗尽仍失败，当日保持红色，由人工 `elevate daily-resume` 或重跑 `daily` 补救。若日后想要同日自动重跑，优先考虑在现有 05:30 任务内部重试，而不是新任务。

> 8-13 事故原始证据（弹窗截图、`(855,903)` 点击坐标、45 分钟假等待）存档于 `apps/wuwa/runtime/runs/20260813_053815_farm_echo_confirmed_retry/` 及当日 evidence 目录；当日无官方维护公告佐证，结论以代码缺陷为准。

### 8.5 2026-08-15 晨：第四种死法——`TacetTask.walk_to_treasure` 抛 `WaitFailedException`

0815 晨 FarmEcho 恢复层真实生效（4 个 Worker 累进 0→1→3→5、一次客户端重启、5/5 成功），失败挪到 DailyTask 阶段：06:28 `TacetTask.walk_to_treasure` 抛 `WaitFailedException`，该特征不在任何恢复清单里，链路放弃时 3 小时预算还剩约 2 小时。证据：`apps/wuwa/runtime/runs/20260815_061006/ok-current-run.log`（trace 行 634–672、traceback 行 675 起）、截图 `apps/wuwa/runtime/evidence/ok_daily_failed_20260815_062826_007281.png`。

阶段 A 对比结论（0815 失败 run 对 0814 晚全绿 run `20260814_204909/ok-current-run.log` 同阶段，行号均指各自日志）：

- a) 超时发生在当日第一个清剿目标（0815 行 649 `Teleport to Tacet Suppression 2`，即配置 `which_tacet=3` 的 index 2）。上游体力门槛只有 `total(=当前+备用) < 60`（`TacetTask.farm_tacet`），104+238=342 通过；"两次 Tacet 共需 120 > 104"从未被评估——daily 模式按 `must_use=180-used_stamina` 强制消耗，备用波片自动补位。体力与本次死亡无关。
- b) 06:10–06:26 是健康的 NightmareNest 全清（4 巢 0/41→41/41 等，行 94/340/444/551/621，画像与 0814 晚一致）；06:27:17 传送并开挑战后，06:27:52 首次切人检查时队伍 HUD 置信度仅 0.117（行 663），战斗约 4 秒即 "not in_team while switching" 误退（行 664/666）；战斗从未发生→宝箱不存在→06:27:55 起 `walk_to_treasure` 找图标 30 秒超时（行 669/670/672）抛异常炸穿 DailyTask。截图任务计数 0/3 未动，正是战斗从未发生的直接后果。
- c) 晨/晚 DailyTask 输入状态客观差异极小：两日 daily progress 都是 0（0815 行 80 / 0814 行 82）、巢穴计数都是全 0（0815 行 94 / 0814 行 96）——0814 晚同样是"当日首次"。真实差异只有三点：①体力 104/342 对 191/429（0815 行 644 / 0814 行 643，均过门槛，非死因）；②挑战入场加载画像：0814 晚两次 WGC 丢帧重启、开挑战到战斗 67 秒（行 652/655/665），0815 晨零丢帧、仅 30 秒（行 651/660）；③首次切人 in-team 判定 True 对 0.117（0814 行 666 / 0815 行 663）。结论：这是"场景加载完成度"与"首次 in-team 检查"之间的时序竞态，与"早上"无必然联系——四天四处互不相同的死因证明枚举式补丁打地鼠必输。

### 8.6 修复：DailyTask 有界通用重试（不再只认已知 marker）

- 任何 `status != "success"`（无论异常类型）进入有界恢复：每次尝试 = 退出挑战/世界态校验（复用 `okww/recovery.py` 的 `run_world_state_recovery`）→ 若世界态恢复失败则从整轮共享预算花一次 OK 冷启动（`restart_client_once` 模式，全轮至多一次，与 FarmEcho 恢复共享）→ 保持 resume 语义（`daily_resume` 随结果传递）重跑 DailyTask；重试上限 2 次，耗尽后按现行失败路径汇报。
- 每次重试写结构化 `daily_state_recoveries` 记录（`kind=generic-bounded-retry`，含 `attempt`/`failure_reason`/`client_restarted`/`retry_status`）；无 broad catch 吞错。
- 原有两个 marker（`DAILY_START_BOOK_FAILURE`/`TACET_DEATH_RECOVERY_FAILURE`）专门路径保留；其世界态恢复失败不再终点红，而是落入通用层继续有界恢复。
- 未改 `recovery_flow.py`/`recovery_worker.py` 语义、未新增计划任务、未动 `Repeat Farm Count=5`、未动星铁与 `automation.psd1`。
- 单测新增 5 例（未知异常 `RuntimeError('surprise')` → 恢复 → 绿；两次重试耗尽 → 红且记录完整；世界态恢复失败 → 冷启动一次 → 重试绿；marker 恢复失败贯穿通用层；generic 重试保持 daily_resume 语义），全套 230 passed。

### 8.7 真机验收（2026-08-15 晚，wuwa-daily 与 farm-echo 双链全绿）

- 19:31–20:01 经生产任务 `Game_Daily_0530`（`runtime/orchestrator/next-run.mode=wuwa-daily`，任务以 Highest 权限运行 `run.ps1`，与 05:30 生产路径相同）全链：FarmEcho 首 Worker 3/5、次 Worker 0/2、第三个 Worker 补齐 2/2，恢复层合成 5/5（worker_retries=2）；DailyTask 19:53–20:00 一次通过（晨间已完成的 4 个巢穴自动跳过、两个清剿体力 239→120、20:00:47 `Daily Task Completed`）；总控 `runtime/orchestrator/20260815_193134/` `wuwa_exit_code=0`、`exit_code=0`。今晚 DailyTask 一次通过，通用重试层未被真机触发（行为由 5 例单测覆盖）。
- 20:06–20:19 farm-echo 复读：恢复层合成 5/5（worker_retries=1），总控 exit 0，最新 farm_echo run `status=success`（`20260815_201654_farm_echo_confirmed_retry`）。
- 当晚 day_rollup 以最新已结算为准合并"晨败晚成"，overall=completed、单条通知（`apps/wuwa/runtime/reports/20260815_200644_farm_echo_confirmed_retry_recovery_daily_rollup.json`）。

### 8.8 策略再升级：次数上限改为双时钟时间预算（2026-08-15 深夜，用户裁定）

用户裁定：3 小时晨间预算是主要约束，"有限次数失败就全失败"不可接受；同类问题 **60 分钟无进展** 才可判失败。§8.6 的 2 次重试上限据此废除，改为双时钟阶梯（`_retry_daily_after_any_failure`）：

- **无进展时钟**：每次重试用 `_daily_progress_fingerprint`（巢穴已击败对、按序体力读数、完成 marker、吸收数）与上次比较；指纹变化=有进展→时钟归零。连续 60 分钟（`DAILY_NO_PROGRESS_TIMEOUT`）无进展才终止（记录 `kind=generic-bounded-retry-exhausted`）。
- **硬截止**：自 workflow 开始 165 分钟（`DAILY_RETRY_HARD_DEADLINE`）强制结算，保证不烧穿晨间窗口。
- **冷启动不再限 1 次**：daily 阶梯专用 `restart_client_for_daily`（无 done 标志，由时钟约束）；FarmEcho 保持原 `restart_client_once` 整轮 1 次预算不变。
- **时钟域**：阶梯全部用 `time.perf_counter`（本机 `time.monotonic` 为 GetTickCount64、分辨率 15.6ms，快迭代会读到相同 tick）；含"时钟停滞即终止"守卫。
- **防御性继承**：`_compose_recovery_result` 在 retry 缺失时从 initial 继承 `daily_resume`/`workflow_task`，防止未来 runner 改动把 resume 阶梯静默退化为全量重跑。
- 单测 234 passed（新增：无进展 80 分钟才停、进展归零时钟、硬截止立即停、时钟停滞停、冷启动两次、resume 标志跨合成继承）；DeepSeek Worker 两轮只读审查均 pass（第一轮两个发现即上述守卫与继承，已修）。
- 真机复验：2026-08-15 22:40–22:58 经生产任务 wuwa-daily 全链 exit=0（FarmEcho 5/5 + DailyTask success，`runtime/orchestrator/20260815_224000/`），确认改造未破坏生产路径。

### 8.9 0818 晨 FarmEcho 恢复层双重死因：恢复自毁 + 次数上限早停（2026-08-18 晚修复）

**事实链（0818 晨 05:30–05:56，`runtime/orchestrator/20260818_053002/`，wuwa=1、final=20）**：首 Worker `20260818_053810` 正常耗尽自身战斗预算退出（absorbed=3/5，无死亡标记，`confirmed_retry_worker.py:372` 有界退出路径）；重试 Worker `054912` 在 05:50:44 撞上 `HOST_FARM_ECHO_PARTY_MEMBER_UNAVAILABLE_CONFIRMED`；party_member 恢复 Worker 仅活 14 秒即死；客户端冷启动后重试 Worker `055104` 团灭（05:54:49 realm defeat）；realm_defeat 恢复 05:56:14 成功治疗；但 3 次无进展已凑满，链路在距 05:30 仅 26 分钟、时间窗还剩约 53 分钟时放弃，DailyTask 被跳过。

**根因一（确定性，B1 修复）**：party_member 恢复的 active_challenge 分支自毁。`recovery_worker.py` 的 `_heal_after_party_member_unavailable` 先用 `in_combat()` 探测（置位战斗态），再 `send_key("esc", after_sleep=1)`——Esc 打开退出菜单后战斗 HUD 消失，`after_sleep` 走 `BaseCombatTask.sleep_check`（上游 `BaseCombatTask.py:782-785`）发现"not in combat"即抛 `NotInCombatException`，恢复 Task 被 OK-WW 执行器内部吞掉，`recovery_completion` 保持 None，`_require_recovery_completion` 报 "recovery task returned without a host completion marker"。完整 traceback 在 `runs/20260818_054912.../farm-echo-recovery-1.log:136-158`；失败截图显示屏幕正是「确认离开」对话框——恢复差一步点击就被战斗守卫杀死。**该分支从未成功过**：0815/0816 两晨（全绿日）同位置同一异常（`runs/20260815_053927.../farm-echo-recovery-1.log:127-147`、`runs/20260816_053832...`），当时被"客户端重启+重试 Worker 有进展"掩盖。修复：在 Esc 前设 `task.skip_combat_check = True`（上游 `BaseChar.py:354` 同款自设模式）——恢复的本意就是离开战斗，不应被战斗守卫终止。负向单测 `_CombatGuardedTask` 桩忠实重放上游 sleep 契约（修复前红、修复后绿）。

**根因二（策略错位，B2 修复）**：FarmEcho 恢复层仍以"连续 3 次无进展"为主停止条件（`recovery_flow.py` 旧 `MAX_CONSECUTIVE_NO_PROGRESS_RETRIES=3`），与用户 0815 裁定的双时钟语义冲突。0818 的 3 次 = 054912 零进展(1) + party_member 恢复失败(2) + 055104 零进展(3)；恢复成功不归零计数（仅吸收进展归零）。对齐后：**无进展时间窗（3600s，有吸收进展即重置）为主停止条件**；次数不再终止（仅结构化记录 `consecutive_no_progress_retries`）；保留 worker 前时间检查与"截止前最后一次恢复留安全态"的既有语义；新增时钟停滞守卫（同 daily 阶梯）与 30s 节奏下限（仅对未启动 Worker 的快速失败迭代生效，防快转死循环）；时钟域从 `time.monotonic` 对齐到 `time.perf_counter`（§8.8 同域规则）；复合结果新增 `no_progress_window_seconds` 字段（旧 `no_progress_retry_limit` 移除，无外部消费者）。

**验收**：单测 238 passed（+4：战斗守卫负向重放、时间窗内零进展远超 3 次仍续、0818 反例（进展后连 3 零进展不死）、时钟停滞停、快转节奏）；compileall OK；validate.ps1 exit 0。真机 2026-08-18 19:41–19:56 farm-echo 生产路径全绿（`runtime/orchestrator/20260818_194149/` wuwa=0、exit=0；复合恢复 run `20260818_194242_..._recovery` status=success：首 Worker 4/5 → 恢复 → 重试 1/5 → 5/5，零客户端重启，`no_progress_window_seconds=3600.0` 新字段生效）。本次真机未触发 party_member 分支（负向路径由 0815/16/18 三晨实况+桩测覆盖）。

### 8.10 0820-0821 版本日:鸣潮版本更新纳入脚本体系(机制全量落地;当晚被 CDN 死尾阻断)

**事实链(0820 晨,`runtime/orchestrator/20260820_053002/`,wuwa=1、final=20)**:3.6 版本日上午服务器维护,05:41 链照常让 OK-WW 直拉旧客户端,"获取远程配置失败/网络重试"点满 3 次仍失败,FarmEcho 0/5,DailyTask 跳过(6.7 分钟即终,红卡)。崩铁阶段正常(exit 0)——版本日晨间鸣潮需要"先更新客户端"而非"硬跑日常"。

**当晚真机过程(22:42–02:00,全程截图监督)**:启动器打开即弹**模态自更新弹窗 `KRUpdateReminderDlg`**(库洛启动器自更新,1600x950@物理(480,215),125% 缩放;左列=自动后台下载进度+退出,右下=大号黑色"更新"主按钮;确认后按钮隐藏、进入下载态)。当晚三个 bug 连环暴露并全部修复:

- **B1 弹窗分支与坐标校准**:首版模板用**非 DPI 感知进程的逻辑坐标 (384,172) 裁剪**,裁的是错误区域;模板"匹配成功"实为错像素自洽,点击点 (724,817) 偏离真实按钮(屏幕 (1762,1039),弹窗右下)**1038 像素整晚空点**。教训入库:裁剪必须用 DPI 感知进程的物理像素;新模板用"真实坐标裁片→屏幕反查匹配→中心落在预期按钮矩形内"三步校准,视觉模型坐标只作候选不作依据。修正后 00:01:16 首发命中,弹窗正确转入下载态。
- **B2 `ensure_client_updated` 版本日门禁**(每日工作流 UU 之后、OK-WW 冷启动之前):正常日子启动器即见"开始游戏"(确认+关闭~半分钟);版本日两段式——先点弹窗"更新"(自更新,启动器自动重启),再由主界面锚点点右下"更新"(游戏版本下载)。自更新点击后 **15 秒窗口哈希无变化=空点立即重试**(用户裁定);下载停滞以**启动器进程自身磁盘 IO 1MB 分桶 5 分钟不变**判定——系统级网络计数器被后台噪声每秒推高会导致停滞时钟永远重置(01:23 真机实测),全窗口哈希会被加载动画欺骗(01:07 真机实测 99% 死锁态仍在动画)。停滞→关启动器重开续传,≤6 次;总预算 4h;未知状态 5 分钟快败。**永不代替 OK-WW 启动游戏**。
- **B3 分流**(`version_day.py`):周中白天(17 点前)更新完即退,注册当晚 20:00 一次性任务(`scripts/version_day_evening_rerun.ps1`,只触发一次,跑 `orchestrator -Mode wuwa-daily`;原"触发自删除"已于 1001 删除,见 §8.11)+飞书黄卡(`report_version_day_deferred`);周末/晚间更新完直接继续日常。`Game_Daily_0530` 执行上限 3h→6h(已重注册生效,PT6H)。

**当晚阻断项(非代码缺陷)**:启动器自更新包 CDN 尾段死——5+ 次尝试分别死在 90/95/99%(有无 UU 均如此),进程 CPU/磁盘/网络全零但 UI 动画不停;杀进程重启只恢复"99% 死下载"态(无按钮可点);UIA 无障碍树为空(CEF);官网 SPA 拿不到安装包直链;本机无安装包。IO 停滞看门狗 01:38:39 真机首发(`restart 1/6 to resume`),阶梯按设计运转,但续传通道本身已死。**待 CDN 恢复后由次日 05:30 链自动完成全部流程**(周中晨间将按 B3 自动改约当晚 20:00)。

**验收**:单测 253 passed(自更新分支 3 例、15s 空点重试、IO 分桶停滞阶梯、未知状态快败、分流决策/注册脚本、延迟工作流);真机已验证:弹窗识别与正确按钮点击(00:01:16)、点击后下载态转换(10%→35%→60%→85%→98%)、IO 看门狗自动停滞重启(01:38:39)。版本日完整链(游戏版本下载→就绪→日常)留待 CDN 恢复后由计划任务真机验收。

### 8.11 0930 版本日首次真机:改约成功,当晚重跑自终止(已修);1001 晨首次开新版卡死

- **0930 晨**:启动器下载新版本 05:38–06:53,周中按 B3 改约并注册 20:00 一次性任务,黄卡已发。
- **0930 20:00 重跑没跑**:TaskScheduler/Operational 事件 107/100/200 触发 → 141(任务被删除)
  → 111(实例被终止),1 秒内结束,`runtime/orchestrator` 无当晚目录。根因:
  `apps/wuwa/scripts/version_day_evening_rerun.ps1` 第一行 `schtasks /Delete` 删的是
  **正在运行的自己**,Windows 删除任务时会终止其运行实例。每个周中版本日都会复现。
  已修复(1001 用户批准):删掉自删除行(`-Once` 触发器本就只触发一次,下次注册用
  `-Force` 覆盖;旧注册残留为无下次运行时间的就绪任务,无害)。下一个周中版本日
  20:00 的 `runtime/orchestrator/<当晚>` 目录与鸣潮卡片即真机验收判据。
- **1001 晨鸣潮红**:启动器 05:41 显示已就绪,OK-WW 05:42:05 拉起游戏(这是更新后第一次开游戏),
  `Client/Saved/Logs/Client.log` 05:42 新建后始终 0 字节,600 秒无窗口。05:47 有显卡驱动超时
  事件(LiveKernelEvent 141),但此类事件 09-09 起每天 5–20 次、成功日也有,不能单独归因。
  游戏进程卡在哪一步现有日志定位不到;下一次 05:30 结果是判据。
- **1002 晨再红,根因查明(1003 凌晨)**:游戏在 OK-WW 直接拉起后约 9 秒自己崩溃——
  `Client/Saved/Crashes/_0000/` 每次启动都被覆盖,断言 `Fatal error: [File:Unknown] [Line: 54]`,
  即上游 issue #1714/#1715 的 `kuro: Use launcher to start game!`。**鸣潮 3.7 起直接运行
  `Wuthering Waves.exe` 必须带资源包参数 `-krqlv=<sd|hd|uhd>`**,OK-WW v3.6.7 以 `None`
  参数拉起(日志 `try execute ...Wuthering Waves.exe None with start`),上游已修
  (提交 0e53ca3「支持3.7启动游戏」),含修复的正式版 v3.7.2 于 1003 03:19 才发布,本机仍是 v3.6.7。你手动经启动器
  进游戏正常,是因为启动器自己带参数。0930 之前能直接拉起,是旧版本不检查。
  与内存、UU、窗口识别无关;GPU 超时事件(LiveKernelEvent 141)自 0909 起天天有,是旁证噪声。
- **修复(1003)**:`okww/game_launch.py` 在运行时包装 OK-WW 的 `start_controller.execute`,
  对游戏本体补 `-krqlv=hd`(本机装的是高清资源包,见 `ManifestResource_HD_*`/`Video/hd`;
  在启动器里换档要改 `GAME_PACKAGE`),已带参数不重复追加;三个 worker 与兼容性探针都挂了它,
  上游改名会在每次日常开头的兼容性检查里直接失败。不改 OK-WW 安装目录。OK-WW 发布含修复的
  正式版后删除该模块即可。真机验收见下一条。
- **1003 真机验收(05:38 手动重跑 `Game_Daily_0530`)**:
  - 星铁:05:30 那趟在 UU 步骤 4 次找不到 `uu_01.png` 整体失败(0902、0912 同类),根因是鼠标残留
    在星铁卡片上使卡片变成悬停样式、卡片原图模板失效;`uu/service.py` 现在定位前先
    `park_cursor_for_detection()`(鸣潮早有同样做法)。重跑 05:46 星铁日常 exit 0。
  - 鸣潮:启动参数修复生效(游戏不再启动即崩、热更新重启已被恢复流程处理)。但 3.7 进世界后立即弹出
    玩法教程「咎锁遗患」(两页,底部"切换至最后一页后可关闭界面"),ESC 关不掉,上游 `ensure_main`
    只会反复按 ESC 到超时。`confirmed_retry_worker.py` 在 `is_main`/`walk_after_boss_teleport` 里
    OCR 识别该提示,按 D 翻页后 ESC(`farm_echo_state.dismiss_tutorial_overlay`,标记
    `HOST_FARM_ECHO_TUTORIAL_OVERLAY_DISMISSED`);真机 06:08 约 9 秒关掉,随后月卡弹窗与
    讨伐入口正常。
  - **未解决**:传送到讨伐点后角色落在 3.7 新增的任务区域内(HUD 追踪任务「御剑飞往倚枝岭」,
    离开区域会出现"已离开任务区域,请尽快返回"倒计时),上游向前走 20 秒只爬上悬崖,
    `Teleport to boss failed: can not walk to combat or F`。OK-WW v3.7.2(1003 03:19 发布,
    含 `-krqlv` 与无音区适配)没改 FarmEcho 走路逻辑,升级不能解决;需用户先在游戏里处理该
    3.7 任务状态或换讨伐目标。证据:`apps/wuwa/runtime/runs/20261003_061*_farm_echo_confirmed_retry`。
    1003 傍晚用户把讨伐改为「讨伐强敌第1项」(OK-WW `FarmEchoTask.json`:`Boss Challenge`/1)。
- **1004 晨:讨伐 5/5 通过,日常卡在无音区**:3.7 在 F2 无音区列表第一组顶部插入「沉心域」「烬心域」
  两项(上游 7d1a5a9 把结构 `[2,5,5,7]` 改为 `[4,5,5,7]`),原第 2 项「玄幽东岳无音区」移到第 4 项。
  配置仍是 2,选中的烬心域显示"附近信标无法快速到达"、「前往」置灰,`click_team_challenge` 等不到
  `team_start_challenge` 而 `WaitFailedException`;通用重试按设计撞满 60 分钟无进度(34 次)才停。
  OCR 对照见 `runs/20260929_063536` 与 `runs/20261004_055512` 的 `book_tab_target_page_check.row_names`。
  修复:OK-WW `configs/DailyTask.json` 的 `Which Tacet Suppression to Farm` 2→4(序号 ≤4 时上游直接点
  第 N 个可见「前往」,不依赖结构表,v3.6.7 可用)。**每次版本更新后先核对 F2 列表是否插入了新项。**
  同日日报"35 处没传送过去"是计数错误:同一巢穴 `go_nest:48:18` 被首轮与 34 次重试各记一次,
  `reporting/parser.py` 改为按巢穴去重。
- **1004 08:00 周常乐园红**:OK 日志 `try execute ...Wuthering Waves.exe None`,游戏启动即崩,
  `Start task failed: 🎡 自动周常乐园`。1003 的 `-krqlv` 补丁只挂在日常/讨伐/恢复 worker 上,
  周常和独立 `farm-echo` 直接调上游 `main.py`,漏了。现在 `runner._build_task_command` 对这两类
  任务改走 `okww/ok_main_worker.py`:先装补丁,再用原参数执行上游 `main.py`。已在 OK-WW 自带
  Python 里空跑确认补丁生效、`config` 解析到上游工作目录、参数原样传递(未启动游戏);
  真机判据是下周日 08:00 周常的 OK 日志出现 `-krqlv=hd`。

## 9. 验证、发布与排障手册

### 9.1 常用命令

```powershell
# 全仓验证：locked sync、pytest、CLI health、dry-run、互斥锁测试
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1

# 总控 dry-run
powershell -NoProfile -ExecutionPolicy Bypass -File orchestrator/run.ps1 -Mode daily-chain -DryRun

# 真实外围、替代核心业务的集成烟测；不把它当正式飞书成功
powershell -NoProfile -ExecutionPolicy Bypass -File orchestrator/run.ps1 -Mode integration-smoke

# 安装/刷新防火墙规则/注册计划任务
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1

# 应用 CLI
uv run --project apps/starrail starrail-auto --help
uv run --project apps/wuwa wuwa-auto --help

# 鸣潮中段续跑
uv run --project apps/wuwa wuwa-auto elevate daily-resume

# 鸣潮客户端人工更新前准备
uv run --project apps/wuwa wuwa-auto elevate client prepare
```

在改代码前先记录 `git status --short --branch` 和 HEAD；工作树不干净时，已有改动默认属于用户，禁止 `reset --hard` 或覆盖。改动后至少执行定向测试、相关应用全测、compileall 和 `git diff --check`。风险较高的 UI 修复还需要一轮受控真机，但已完成阶段应使用 resume/定向路径，不应为了“看起来完整”重复跑数小时。

### 9.2 标准排障顺序

1. 读取最新 `runtime/orchestrator/<run_id>/result.json`，拆分四个阶段退出码。
2. 根据失败应用读取其 `runtime/runs/<run_id>/result.json`，不要先读整天混合日志。
3. 按 result 中的日志路径和 offset 提取当前切片。
4. 对齐 evidence 截图、模板裁剪、窗口位置、进程退出和 HID reached 坐标。
5. 区分业务失败、恢复失败、cleanup 失败、飞书发送失败。
6. 与前一天、前两天以及十几天前成功样本比较同一阶段，不比较无关总日志。
7. 查上游版本和契约变化；上游升级后先做兼容预检与覆盖审计。
8. 提出最小修复和明确后置条件，先补负向/正向单测，再做定向真机。
9. 真机成功必须看到最终业务 marker、结构化 success、cleanup 完成和报告发送；不能用“进度看起来动了”代替。

### 9.3 日报必须忠于的确定性事实

退出码、完成数量、前后体力、吸收数、日活值、清理布尔值、HTTP 状态、是否发送、错误阶段、原始否定 marker 都只能如实呈现，措辞调整也不能改变它们的含义。

例子：首 Worker 4/5、第二 Worker 1/1，结构化累计为 5/5。日报写“吸收声骸 5/5；途中续跑 1 次，已自动恢复”，不能因为父 Daily 后续失败写成“讨伐没完成”。反过来，DailyTask 找不到 `boss_proceed` 时，不能因为 FarmEcho 5/5 就写“今日全部成功”。最可靠的日报应同时表达两者：FarmEcho 已完成，DailyTask 在导航阶段失败，整日状态为 partial success。

## 10. 已知技术债与优先级

### P0（已清零）：晨间链路三连故障

8-12 页面导航、8-13 弹窗误点、8-14 恢复误判均已修复（`d46387e`、`aa571c8`、`d5261cf`）并以 8-14 晚全绿真机验收（见 §8.3）。新的观察哨：连续保持 05:30 绿色；首个红色日按 §9.2 顺序归因，不回滚已验证路径。

### P0 更新（2026-08-15）：第四故障与策略转向

8-15 晨死于 `TacetTask.walk_to_treasure` `WaitFailedException`（归因见 §8.5，加载时序竞态而非可枚举缺陷）。策略已从"枚举失败特征再补丁"转向"DailyTask 有界通用恢复"（§8.6），并以 8-15 晚双链真机验收（§8.7）。新观察哨：连续保持 05:30 绿色；若再现红色，先读该 run 的 `daily_state_recoveries` 记录确认恢复动作（世界态校验/冷启动/重跑）是否按预期执行与耗尽，而不是先新增 marker。

### P0 更新（2026-08-18）：第五、六故障——party_member 恢复自毁 + FarmEcho 次数上限早停

8-18 晨鸣潮段红（归因见 §8.9）：party_member 恢复的 active_challenge 分支被上游战斗守卫睡眠确定性杀死（0815 起就存在、被重启+进展掩盖），且恢复层"连续 3 次"上限在时间窗还剩 53 分钟时放弃。双修复（B1 `skip_combat_check` + B2 双时钟对齐）已真机验收（§8.9）。新观察哨：①若 farm_echo 复合结果再现 `recovery task returned without a host completion marker`，读对应 `farm-echo-recovery-*.log` 的内层 traceback——战斗守卫类自毁应已绝迹，出现即新根因；②party_member 恢复实战首秀仍未发生（0818 晚真机未触发该分支），首次实战时确认 `HOST_FARM_ECHO_RECOVERY_STATE ... active_challenge` 后能走到 `HOST_FARM_ECHO_PARTY_MEMBER_HEAL_RECOVERY_COMPLETED`；③双时钟下恢复循环可运行至多 1 小时无进展才停——晨间报告若显示长时间零进展续跑，属预期语义而非卡死。

### P1：稳定性与可观测性

- 语义 OCR 仍可能偶发 false negative；这会安全失败而非误点，需积累样本后改善，不得放宽成通用按钮确认。
- 每次 UI 动作统一记录窗口原点、DPI、虚拟桌面边界、目标/reached 坐标和 after-click evidence。
- 客户端或游戏窗口已消失时，所有 wait main 应感知进程终止，避免长时间假等待。

### P2：文档漂移

- 根架构文档的周常 4 小时应改为实际 8 小时。
- 鸣潮旧 README/架构蓝图仍含“战斗点击走 HID”、旧死亡条件、12/60 次数、Boss 失败仍继续 Daily 等过时描述，应以当前实现和本文为准并逐步修订。
- OK-WW 覆盖审计仍记录 v3.5.18，实机为 v3.5.27；M7A 覆盖审计也落后于 v2026.7.26。
- 对 pre-daily FarmEcho 失败后的策略，旧文档与当前实现不一致。当前实现为未达 5/5 则跳过 Daily；若产品策略要改变，必须显式审批并补测试，不能靠旧文档暗改。

## 11. 安全与敏感信息

两应用的 `.env` 包含飞书 webhook/secret 等，已由 Git 忽略（旧的 DeepSeek 变量已不再读取，可以从 `.env` 删除）。交接文档、提交、飞书卡片和测试快照只能列变量名，禁止写真实值。日志和截图可能包含绝对安装路径、用户名、PID、窗口句柄、命令行或个人养成计划，公开前必须脱敏。

允许记录的环境变量名包括：`FEISHU_WEBHOOK_URL`、`FEISHU_WEBHOOK_SECRET`，以及鸣潮独立的 `WUWA_FEISHU_*`、发送开关等。密钥缺失应让飞书层明确降级，不能让游戏业务事实消失。

## 12. 接手检查清单

- [ ] 确认分支、HEAD、工作树，保留用户已有修改。
- [ ] 读取最新总控 result，按阶段归因，不从 final code 猜游戏失败。
- [ ] 核对计划任务仍为 05:30 daily、周日 08:00 weekly、全局锁唯一。
- [ ] 确认星铁 M7A/鸣潮 OK-WW 的实机版本与覆盖审计一致。
- [ ] 确认 UU 复用同时有目标游戏身份和停止按钮。
- [ ] 鸣潮只由 OK-WW 拉起；更新完成后也先关闭再交回 OK。
- [ ] FarmEcho 目标保持 5，进度按吸收确认累计；连续无进展最多 3。
- [ ] 不把普通战斗劣化当死亡，不用 HID 接管上游战斗。
- [ ] 所有通用按钮点击前都有页面语义后置条件或严格弹窗/ROI 锚点。
- [ ] 已完成阶段使用定向 resume，不重复跑整条耗时流程。
- [ ] 改了日报规则后，用 `report` 命令重放近期真实归档，逐张核对卡片。
- [ ] 业务、cleanup、飞书三层状态分别归档并准确汇报。
- [ ] 真机验收后再提交；提交后检查 diff、测试、日志证据和敏感信息。

## 13. 关键证据索引

- 总控配置与正式命令：`config/automation.psd1`
- 总控阶段与退出码：`orchestrator/run.ps1`
- 安装与计划任务：`scripts/install.ps1`、`scripts/register_tasks.ps1`
- 全仓验证：`scripts/validate.ps1`
- 星铁运行与看门狗：`apps/starrail/src/starrail_auto/m7a/runner.py`、`logs.py`、`watchdog.py`
- 星铁日报：`apps/starrail/src/starrail_auto/reporting/`
- 鸣潮日常链与恢复：`apps/wuwa/src/wuwa_auto/daily.py`
- FarmEcho 进度恢复：`apps/wuwa/src/wuwa_auto/okww/recovery_flow.py`
- 死亡边界：`apps/wuwa/src/wuwa_auto/okww/farm_echo_state.py`
- 无音页面语义确认：`apps/wuwa/src/wuwa_auto/okww/daily_trace.py`
- 定向续跑：`apps/wuwa/src/wuwa_auto/okww/daily_worker.py`
- 同日汇总：`apps/wuwa/src/wuwa_auto/reporting/day_rollup.py`
- 日报公共卡片：`packages/game-automation-core/src/game_automation_core/reporting/report.py`
- 8 月 12 日成功真机：`apps/wuwa/runtime/runs/20260812_212242_daily_resume/`
- 8 月 13 日当前失败：`apps/wuwa/runtime/runs/20260813_053815_farm_echo_confirmed_retry/` 及后续两轮 recovery 目录
- 8 月 13 日总控：`runtime/orchestrator/20260813_053002/`

最后强调：本项目最容易犯的错误不是“少重试一次”，而是没有先定义成功后置条件，就用一个看起来相似的按钮、日志关键词或聚合退出码替代真实业务状态。接手者应始终沿着“当前运行切片 → 结构化阶段 → 视觉/进程证据 → 最小修复 → 定向真机 → 完整汇报”的链路工作。
