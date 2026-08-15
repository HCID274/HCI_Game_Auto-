# 一次性任务：鸣潮晨间链路必须全绿（2026-08-15 派发）

> 本文是自包含的一次性执行任务书。执行体只读一次输入、无法追问、只交付一次。
> 文中没写的不做；写含糊的会自由发挥，故本文全部使用可复制命令与硬性判据。

## 【角色】

你将一次性完成下述任务。你无法提问，也没有第二次机会。所有决策自己做完，最后一次性交付。失败的不许写成通过，没跑的不许写成跑了。

## 【背景】

- 仓库：`D:\1_Projects\07_MyAutoScript\game-automation`（Windows 11，Git Bash + PowerShell 7 以下兼容，uv workspace，单仓多应用）。
- 先读：`HANDOFF.md`（权威交接文档：工程边界、§9 排障手册、§11 敏感信息规范）。本文与其冲突时以本文为准（本文事实更新到 2026-08-15 中午）。
- 为什么要做：正式计划任务 `Game_Daily_0530`（每天 05:30，链路：星铁→星铁清理→鸣潮→鸣潮清理）中，鸣潮段连续四个清晨失败，且**每天死在不同阶段**：0812 晨死于 DailyTask 无音清剿页面导航（已修 `d46387e`）；0813 晨死于启动器弹窗误点退出（已修 `aa571c8`）；0814 晨死于 FarmEcho 恢复误判（已修 `d5261cf`，当晚 20:35 全链真机验收绿）；**0815 晨 FarmEcho 恢复层真实生效**（4 个 Worker 累进 0→1→3→5、一次客户端重启、5/5 成功），**但失败挪到 DailyTask 阶段**：`TacetTask.walk_to_treasure` 抛 `WaitFailedException`，该失败特征不在任何恢复清单里，链路 06:28 放弃时 3 小时预算还剩约 2 小时。
- 结论性判断（已由证据支撑，不要推翻重来）：逐阶段"枚举失败特征再补丁"的策略输了打地鼠。本任务要求换策略：**DailyTask 阶段实现与 FarmEcho 同等的"有界通用恢复"——任何失败都可恢复重跑，而不是只有已知失败才恢复**。
- 今天是周六白天，机器为自动化专用机，**允许任意真机测试**（含开着游戏反复跑）。真机运行会接管鼠标键盘约 30–60 分钟/次；运行前必须确认全局锁空闲且用户未在手动游戏（判据见【任务】C1）。

## 【基线】

1. 记录起点：
   ```
   cd /d/1_Projects/07_MyAutoScript/game-automation && git status --short --branch && git log --oneline -3
   ```
   期望：main 干净、HEAD=`9198e9d` 或其后代。工作树若不干净，已有改动属于用户，禁止 `reset --hard` 或覆盖。
2. 测试基线：
   ```
   uv run --project apps/wuwa pytest apps/wuwa/tests -q
   ```
   期望：`225 passed`（2026-08-15 复核）。你只对 自己引入的 失败负责。
3. 0815 晨失败证据（只读，不许改动）：
   - 总控分段：`runtime/orchestrator/20260815_053002/result.json`（星铁 0/0、鸣潮 1、cleanup 0、final 20）。
   - 最终合成：`apps/wuwa/runtime/runs/20260815_053927_farm_echo_confirmed_retry_recovery_daily/result.json`（FarmEcho 5/5 success + DailyTask failed）。
   - DailyTask 崩溃日志：`apps/wuwa/runtime/runs/20260815_061006/ok-current-run.log`——第 675 行起 `WaitFailedException` traceback；第 643–673 行 `HOST_OKWW_DAILY_TRACE`（`stamina_read_consensus` 结果 `[104, 238, 342]`、`book_tab_target_page_confirmed`、`tacet_error`/`daily_run_error`）。
   - 失败瞬间截图（已人工判读，供核对）：`apps/wuwa/runtime/evidence/ok_daily_failed_20260815_062826_007281.png`——大世界水边、追踪任务"清理无音区中涌现的残象：0/3"、无宝箱图标、无 F 交互提示、队伍血量正常、无弹窗。
   - 对照组（0814 晚同一 DailyTask 阶段全绿）：`apps/wuwa/runtime/runs/20260814_204909/ok-current-run.log`。
4. 不要修与本任务无关的既有失败；确有必要修的，单独在报告里说明理由。

## 【任务】

四个阶段必须按序执行；每阶段的完成判据达成后才允许进入下一阶段。

### 阶段 A：查清 0815 晨 DailyTask 死因（探索阶段，产出必须是引用日志行号的结论）

A1. 对比命令（两个日志同一阶段的客观差异）：
   ```
   grep -n "stamina_\|walk_to_treasure\|find_treasure\|book_tab_\|tacet_error\|claim\|Daily Task Completed" apps/wuwa/runtime/runs/20260815_061006/ok-current-run.log
   grep -n "stamina_\|walk_to_treasure\|find_treasure\|book_tab_\|tacet_error\|claim\|Daily Task Completed" apps/wuwa/runtime/runs/20260814_204909/ok-current-run.log
   ```
A2. 必须回答三个问题（每条结论引用日志行号或具体截图文件名）：
   a) `walk_to_treasure` 超时发生在第几个清剿目标？当时体力 104（总 342）是否参与了"跳过/不足"逻辑？两次 Tacet 共需 120 > 104，不足如何被处理？
   b) 06:10–06:28 之间角色实际处于什么状态（结合 trace 事件与截图）：为什么 18 分钟内既没找到宝箱图标、也没触发战斗（任务计数 0/3 未动）？
   c) 晨间（日常刚重置、体力满、过夜邮件）与晚间（当天已完成过）的 DailyTask 输入状态有哪些**客观差异**？列出逐条可验证的差异清单——这是"为什么总是早上死"的核心待答问题。
A3. 完成判据：交付报告含"阶段 A 结论"一节，逐条回答 a/b/c 且每条带行号引用。**没有行号引用的结论视为未完成。**

### 阶段 B：把 DailyTask 从"枚举式恢复"改为"有界通用重试"（机械改造）

B1. 主改造点：
   - 文件：`apps/wuwa/src/wuwa_auto/daily.py:129-167`（`_maybe_recover_daily_state` 与其两个 marker 常量 `DAILY_START_BOOK_FAILURE`、`TACET_DEATH_RECOVERY_FAILURE`）；同文件 `_settle_business_transaction`（约 290-325 行）。
   - 现状：只认上述两个失败特征才恢复重跑；0815 的 `WaitFailedException` 直接红。
   - 要求：DailyTask 阶段**任何失败**（`status != "success"`，无论异常类型）都进入有界恢复：
     1. 状态安全重置：退出挑战→世界态校验（复用 `okww/recovery.py` 的 `run_world_state_recovery`）；若世界态校验失败，则执行一次 OK 持有的客户端冷启动重启（复用现有 `restart_client_once` 模式），且整轮任务内冷启动至多一次；
     2. 重跑 DailyTask；重试上限 **2 次**；重试必须保持 resume 语义（`daily_resume` 配置随结果传递）；
     3. 每次重试写入结构化 `daily_state_recoveries` 记录（含失败原因、恢复动作、结果）；
     4. 两次重试后仍失败→按现行失败路径汇报；禁止无限循环、禁止 broad catch 吞错。
   - 保留：原有两个 marker 的专门处理路径不变（回归兼容）。
B2. 若阶段 A 发现确定性根因（例如体力不足路径错误、水边卡位），允许一个**最小外科修复**（单一函数 + 负向测试）；但 B1 的通用重试无论如何必须实现——它是防"下一个未知失败"的保险层。
B3. 禁止事项（全部为硬约束）：
   - 不修改 OK-WW 安装目录（`D:\2_Software\...\ok-ww`）；
   - 不用虚拟 HID 接管战斗；
   - 不改 `apps/wuwa/src/wuwa_auto/okww/recovery_flow.py` / `recovery_worker.py` 的既有语义（刚真机验证过，只能新增调用不能改行为）；
   - 不改 `Repeat Farm Count=5` 目标；
   - **不新增任何计划任务**（用户 0814 明令）；
   - 不动星铁应用、不动 `config/automation.psd1`。
B4. 新增确定性单测（无真机）：
   1. 未知异常类型失败（如直接构造 `RuntimeError('surprise')` 的失败结果）→ 触发恢复→重跑成功→最终绿；
   2. 两次重试均失败→最终红且 `daily_state_recoveries` 记录完整；
   3. 原有两个 marker 路径行为回归不变。
B5. 完成判据：`uv run --project apps/wuwa pytest apps/wuwa/tests -q` 输出 `0 failed` 且用例数 ≥ 225+新增数。

### 阶段 C：真机验收（周六白天允许，全程按生产路径）

C1. 每次真机前检查（两条都过才启动）：
   ```
   powershell -NoProfile -Command "$p=Get-Process -Name 'Client-Win64-Shipping' -ErrorAction SilentlyContinue; if($p){'GAME RUNNING - STOP'}else{'game clear'}"
   ```
   互斥锁检查：确认无其他 orchestrator 在跑（`ls -t runtime/orchestrator | head -1` 的最新 run 已有 result.json）。
   真机命令需要管理员权限：优先在已提权宿主里跑，或用 `wuwa-auto elevate`（会弹 UAC，需用户点一次；若你的环境无法与 UAC 交互，按【阻塞】节处理该项）。
C2. 完整链真机一次（与 05:30 生产路径完全相同的命令）：
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File orchestrator/run.ps1 -Mode wuwa-daily
   ```
   期望：新 `runtime/orchestrator/<run_id>/result.json` 中 `wuwa_exit_code=0` 且 `exit_code=0`；最新 `apps/wuwa/runtime/runs/*_recovery_daily/result.json` 的 `status` 为 `success`，其合成日志含 FarmEcho 5/5 与 `Daily Task Completed`。
   说明：0815 的日常尚未完成（今晨失败），本次真机会真实完成今日日常。若当日体力不足两个清剿目标，"资源不足跳过"是既有正常语义——如实汇报，禁止为凑绿放宽判定。
C3. 战斗/恢复层复读鲁棒性一次（同日可重复）：
   ```
   uv run --project apps/wuwa wuwa-auto elevate farm-echo
   ```
   期望：退出码 0，最新 farm_echo run 的 `result.json` `status=success`。
C4. 完成判据：C2、C3 的断言命令（见【验收】4/5 条）全部输出期望值。

### 阶段 D：收尾

D1. `HANDOFF.md` §8 追加 0815 晨事实与你的阶段 A 结论；§10 优先级同步；只追加不删除。
D2. 全部验收绿后提交：中文提交信息、`fix(wuwa):`/`test(wuwa):`/`docs:` 前缀风格、分批提交、提交前 `git diff --check`；随后 `git push origin main`。验收未全绿禁止 push。
D3. 明晨 05:30 是最终考核但你无法观测。交付报告末尾写"明晨检查三步"：① 读最新 `runtime/orchestrator/<run_id>/result.json` 分段归因；② 若鸣潮红且 FarmEcho 已 5/5，用 `uv run --project apps/wuwa wuwa-auto elevate daily-resume` 补救；③ 证据路径索引。供用户照做。

## 【影响面】

以下位置引用了被改对象，逐个检查并在报告说明是否需同步修改：
- `apps/wuwa/src/wuwa_auto/daily.py`（`_maybe_recover_daily_state`、`_settle_business_transaction`、`_run_boss_then_daily_task`）
- `apps/wuwa/src/wuwa_auto/okww/runner.py`（`run_daily_task`/`run_daily_resume_task` 的失败语义与 marker 输出）
- `apps/wuwa/src/wuwa_auto/okww/daily_trace.py`（`daily_run_error` 埋点已存在，可作通用重试触发依据）
- `apps/wuwa/src/wuwa_auto/okww/recovery.py`（`run_world_state_recovery` 被复用）
- `apps/wuwa/src/wuwa_auto/reporting/day_rollup.py`（同日多次 Daily 尝试必须合并为一条通知、最新已结算优先——现有 `_select_stage` 语义，验证不被破坏）
- `apps/wuwa/tests/test_daily_workflow.py`、`apps/wuwa/tests/test_reporting.py`
改完全局搜索确认无遗漏，命令与输出贴进报告：
```
grep -rn "DAILY_START_BOOK_FAILURE\|TACET_DEATH_RECOVERY_FAILURE\|daily_state_recoveries" apps/wuwa
```

## 【验收】（全部机器断言；逐条贴真实输出）

```
cd /d/1_Projects/07_MyAutoScript/game-automation
```
1. `uv run --project apps/wuwa pytest apps/wuwa/tests -q`  # 期望：`0 failed`
2. `uv run --project apps/wuwa python -m compileall -q apps/wuwa/src && echo COMPILE_OK`  # 期望：`COMPILE_OK`
3. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1`  # 期望：退出 0。改动前先跑一次记录基线；只对自己引入的失败负责。
4. C2 断言：`grep -c '"wuwa_exit_code":  0' runtime/orchestrator/<C2的run_id>/result.json`  # 期望：`1`（注意 PowerShell JSON 冒号后两个空格）
5. C3 断言：`uv run --project apps/wuwa python -c "import json,glob; p=sorted(glob.glob('apps/wuwa/runtime/runs/*farm_echo*/result.json'))[-1]; print(json.load(open(p,encoding='utf-8'))['status'])"`  # 期望：`success`
6. 抗条件变化：新增单测必须覆盖"异常类型未知"的失败（构造 `RuntimeError('surprise')`）也进入重试；C2 断言不依赖当日体力/邮件/活跃度状态。

## 【自检循环】

交付前必须做，做完再交：
1. 重新通读你改的全部 diff，当作第一次看到它。
2. 重跑全部验收命令（1–6）。
3. 显式回答：还有哪些地方可能受影响但我没检查？逐个检查（至少：day_rollup 对重试合并、Feishu 卡片对部分成功语义、`elevate` 链路）。
4. 发现问题就修，修完回到第 1 步；直到一轮无新问题。

## 【阻塞】

1. 先在授权范围内自查：查 marker 定义、写最小复现测试、读上游 OK-WW 源码（只读）。
2. 仍不通：完成其余全部任务，把阻塞项单独列出，写清卡在哪、试过什么、需要人做什么决策。
3. UAC 无法交互时：真机项标记阻塞，代码/单测/文档照常交付，不得伪造真机结果。
4. 不许猜测性实现，不许为绕开阻塞扩大改动范围。

## 【交付报告】

1. 阶段 A 结论（带行号引用）。
2. 阶段 B 每项改动：文件+行号+意图；影响面清单逐条结论+全局搜索输出。
3. 验收命令 1–6 的真实输出（原文，含通过/失败数与真机 result.json 断言）。
4. 自检循环跑了几轮，最后一轮发现了什么。
5. 阻塞项与未覆盖风险；明晨检查三步（D3）。
6. 禁止项重申：失败不许写成通过；没跑不许写成跑了；引用截图必须同时贴机器断言命令与输出；报告与提交中不得出现 `.env` 密钥真实值、不得写死敏感路径以外的用户隐私。
