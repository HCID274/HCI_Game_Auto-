# 一次性任务：鸣潮 0818 晨 FarmEcho 恢复层失败——探索、修复、真机全绿（连续执行，无停顿点）

> 本文是自包含的一次性执行任务书。执行体只读一次输入、无法追问、只交付一次。
> 本任务**没有任何等待用户确认的检查点**：遇到不确定，按证据自行决策并继续；只有外部硬阻塞（真机被占用且无法安全获得、凭据缺失）才允许按【阻塞】节处置。文中没写的不做；写含糊的会自由发挥，故本文全部使用可复制命令与硬性判据。

## 【角色】

你将一次性完成下述任务。你无法提问，也没有第二次机会。所有决策自己做完，最后一次性交付。失败的不许写成通过，没跑的不许写成跑了。**连续执行到验收全绿为止，中途不得停顿等待任何人。**

## 【背景】

- 仓库：`D:\1_Projects\07_MyAutoScript\game-automation`（Windows 11，Git Bash + PowerShell 7 以下兼容，uv workspace，单仓多应用）。
- 先读：`HANDOFF.md`（权威交接：§8.8 双时钟改造、§9 排障手册、§11 敏感信息规范）。本文与其冲突以本文为准（事实更新到 2026-08-18 晨）。
- 事实链：0816、0817 两晨 05:30 全链绿（DailyTask 双时钟阶梯稳定运行）；**0818 晨鸣潮段红**：FarmEcho 首 Worker 3/5 后，两次 `party_member_unavailable` 恢复失败（原因均为 `recovery task returned without a host completion marker`），一次客户端冷启动，3 次连续无进展耗尽（`recovery_flow.py:43` 的 `MAX_CONSECUTIVE_NO_PROGRESS_RETRIES = 3`），05:56 合成失败、DailyTask 被跳过，总控 final=20。链路放弃时距 05:30 仅 26 分钟，3 小时预算还剩约 2.5 小时。
- 已知张力（不要推翻，要解决）：用户既定裁定“时间预算是主要约束，不得因有限次数失败而全失败”（0815 已在 DailyTask 落地为双时钟）；但 FarmEcho 恢复层目前仍是**次数上限为主停止条件**（3 次）。本任务要求先查清根因，再决定修复形态。
- 机器为自动化专用机，白天允许任意真机测试（真机运行接管鼠标键盘约 30–60 分钟/次；运行前必须过 C1 前置检查）。今天是工作日，无人值守机，不需要向任何人确认。

## 【基线】

1. 记录起点：
   ```
   cd /d/1_Projects/07_MyAutoScript/game-automation && git status --short --branch && git log --oneline -3
   ```
   期望：main 干净、HEAD=`8857874` 或其后代。工作树不干净时已有改动属于用户，禁止 `reset --hard` 或覆盖。
2. 测试基线：
   ```
   uv run --project apps/wuwa pytest apps/wuwa/tests -q
   ```
   期望：`234 passed`（2026-08-18 复核）。你只对**自己引入的**失败负责。
3. 0818 晨失败证据（只读，不许改动）：
   - 总控分段：`runtime/orchestrator/20260818_053002/result.json`（星铁 0/0、鸣潮 1、final 20，05:30:02–05:56:29）。
   - 鸣潮 run 序列：`apps/wuwa/runtime/runs/` 下 `20260818_053810_farm_echo_confirmed_retry`（首 Worker，3/5）、`20260818_054912_farm_echo_confirmed_retry`、`20260818_055104_farm_echo_confirmed_retry`、合成 `20260818_053810_farm_echo_confirmed_retry_recovery` 与 `..._recovery_daily`。
   - 合成失败原因：`FarmEcho recovery incomplete: absorbed 3/5; worker_retries=2 (progress-driven); combat_rebinds=0; client_restarts=1; recoveries=2; retry=maximum consecutive FarmEcho no-progress retries exhausted: 3`。
   - 恢复失败截图：`apps/wuwa/runtime/evidence/ok_farm_echo_party_member_recovery_1_failed_20260818_055102_379804.png`。
   - 对照组（前两晨全绿）：`runtime/orchestrator/20260816_053002/result.json`、`runtime/orchestrator/20260817_053002/result.json`。
4. 不要修与本任务无关的既有失败；确有必要修的，单独在报告里说明理由。

## 【任务】

四个阶段按序执行；每阶段完成判据达成后才进入下一阶段。**阶段之间不停止、不请求确认。**

### 阶段 A：查清 0818 晨 FarmEcho 恢复层死因（探索阶段，产出必须是引用日志行号/文件行号的结论）

A1. 对比命令（今晨失败 vs 前两晨绿的同阶段客观差异）：
   ```
   for d in 20260818_053810 20260818_054912 20260818_055104; do echo "== $d =="; grep -n "farm echo walk_find_echo\|party_member\|Revive Failed\|char dead\|Daily Task\|not in combat\|realm" apps/wuwa/runtime/runs/${d}_farm_echo_confirmed_retry/ok-current-run.log | tail -15; done
   ```
A2. 必须回答四个问题（每条结论引用日志行号、result.json 字段或源码 `文件:行号`）：
   a) 首 Worker（`20260818_053810`）3/5 后**为什么退出**：死因 marker 是什么、`confirmed retry returned early` 的直接原因？
   b) 两次 `party_member_unavailable` 恢复为何都以 `recovery task returned without a host completion marker` 失败：读 `apps/wuwa/src/wuwa_auto/okww/recovery_worker.py`（`:449` 的 `_require_recovery_completion` 及其调用链、party_member 恢复 mode 的完整流程），结合失败截图，回答恢复 Worker 实际走到了哪一步、期望的完成 marker 是什么、为什么没出现。这是本次的核心待答问题。
   c) 无进展计数 3 是怎么累计的（`recovery_flow.py:364-395`：两次恢复失败+第几次什么构成 3）？`retry_deadline`（`:295-303`，时间窗）在放弃时还剩多少分钟？即：**如果以时间预算为主停止条件，今晨是否本可继续？**
   d) 今晨失败模式 vs 已有恢复清单（`logs.py` 各 `is_recoverable_*`）：是新失败特征，还是既有特征落入了错误的恢复 mode？
A3. 完成判据：交付报告含“阶段 A 结论”一节，逐条回答 a/b/c/d 且每条带行号引用。**没有行号引用的结论视为未完成。**

### 阶段 B：修复（形态由阶段 A 证据决定，但边界如下）

B1. 若阶段 A 发现**确定性根因**（如恢复 mode 分类错误、完成 marker 判定缺陷、恢复动作到达错误界面）：做**最小外科修复**（单一函数 + 负向单测），保持既有已验证恢复语义不变。
B2. 若阶段 A 证明失败是**瞬态多样**（同类位置不同表现、重启有概率修复）：把用户已裁定的双时钟语义**对齐**到 FarmEcho 恢复层——无进展主停止条件从“连续 3 次”改为“时间窗为主 + 次数为辅”（复用 `recovery_flow.py:295-303` 已有的 `retry_deadline` 时间窗，参考 `daily.py` 双时钟实现的既有模式），保持：有进展即重置、整层 workflow 硬截止兜底、每轮结构化记录、禁止无限循环。两条路径都允许时**优先 B1**；B1 与 B2 不互斥（确定性根因修复 + 时间预算对齐可都做）。
B3. 硬约束（禁止事项）：
   - 不修改 OK-WW 安装目录（`D:\2_Software\...\ok-ww`）；
   - 不用虚拟 HID 接管战斗；
   - 不改 `Repeat Farm Count=5`；不动星铁应用、不动 `config/automation.psd1`；
   - **不新增任何计划任务**（用户既令）；
   - `recovery_flow.py`/`recovery_worker.py` 的**既有已验证语义只允许最小必要修改**，不推翻重写；
   - 时钟相关实现遵守 `time.perf_counter` 同域规则（见 `windows-game-automation` Skill 看门狗节：`time.monotonic` 在本机可能为 GetTickCount64@15.6ms）。注意 `recovery_flow.py:303` 现用 `time.monotonic`，若你的改动触碰该行，一并对齐。
B4. 新增确定性单测（无真机）：① 阶段 A 根因的负向复现（修复前红、修复后绿）；② 若做 B2：时间窗未到不放弃、次数到但时间窗未到且最近有进展不放弃、时间窗到才终局；③ 既有恢复路径回归不变。
B5. 完成判据：`uv run --project apps/wuwa pytest apps/wuwa/tests -q` 输出 `0 failed` 且用例数 ≥ 234+新增数。

### 阶段 C：真机验收（连续执行，同日可重复，全程按生产路径）

C1. 每次真机前两条都过才启动：
   ```
   powershell -NoProfile -Command '$p=Get-Process -Name "Client-Win64-Shipping" -ErrorAction SilentlyContinue; if($p){"GAME RUNNING - STOP"}else{"game clear"}'
   ```
   互斥锁检查：`ls -t runtime/orchestrator | head -1` 的最新 run 已有 `result.json`。
C2. 真机触发方式（无 UAC 交互，与 05:30 生产路径完全相同）：
   ```
   printf 'farm-echo' > runtime/orchestrator/next-run.mode && powershell -NoProfile -Command 'Start-ScheduledTask -TaskName "Game_Daily_0530"'
   ```
   （验收 FarmEcho 修复；随后如需全链，把 `farm-echo` 换成 `wuwa-daily`。）
C3. 完成判据：新 `runtime/orchestrator/<run_id>/result.json` 中 `wuwa_exit_code=0` 且 `exit_code=0`；最新 `apps/wuwa/runtime/runs/*farm_echo*/result.json` 的 `status` 为 `success`。**若红：读新 run 证据回到阶段 A/B 循环，修复后再次真机，直到绿或【阻塞】成立。今日当日体力/吸收状态导致的“资源不足跳过”是既有正常语义——如实汇报，禁止为凑绿放宽判定。**

### 阶段 D：收尾

D1. `HANDOFF.md` §8 追加 0818 晨事实与阶段 A 结论；§10 优先级同步；只追加不删除。
D2. 全部验收绿后：中文提交信息、`fix(wuwa):`/`test(wuwa):`/`docs:` 前缀、分批提交、提交前 `git diff --check`；随后 `git push origin main`。**验收未全绿禁止 push。**
D3. 报告末尾写“明晨检查三步”：① 读最新 `runtime/orchestrator/<run_id>/result.json` 分段归因；② 若鸣潮红，用 `uv run --project apps/wuwa wuwa-auto elevate daily-resume` 补救；③ 证据路径索引。

## 【影响面】

以下位置引用了被改对象，逐个检查并在报告说明是否需同步修改：
- `apps/wuwa/src/wuwa_auto/okww/recovery_flow.py`（`MAX_CONSECUTIVE_NO_PROGRESS_RETRIES:43`、主循环 `:317-505`、时间窗 `:295-303`）
- `apps/wuwa/src/wuwa_auto/okww/recovery_worker.py`（`_require_recovery_completion:449`、party_member 恢复 mode 流程）
- `apps/wuwa/src/wuwa_auto/okww/logs.py`（`is_recoverable_farm_echo_party_member_unavailable:234-236` 及各 classifier）
- `apps/wuwa/src/wuwa_auto/okww/confirmed_retry.py`（`MAX_FARM_ECHO_RUNTIME_SECONDS:44`、无进展 deadline `:192-338`）
- `apps/wuwa/src/wuwa_auto/daily.py`（`maybe_recover_farm_echo_death` 调用点、双时钟参考实现 `_retry_daily_after_any_failure`）
- `apps/wuwa/src/wuwa_auto/reporting/day_rollup.py`（同日多次尝试合并语义，验证不被破坏）
- `apps/wuwa/tests/test_farm_echo_recovery*.py`、`test_daily_workflow.py`（相关既有断言）
改完全局搜索确认无遗漏，命令与输出贴进报告：
```
grep -rn "MAX_CONSECUTIVE_NO_PROGRESS_RETRIES\|party_member_unavailable\|_require_recovery_completion\|maybe_recover_farm_echo_death" apps/wuwa
```

## 【验收】（全部机器断言；逐条贴真实输出）

```
cd /d/1_Projects/07_MyAutoScript/game-automation
```
1. `uv run --project apps/wuwa pytest apps/wuwa/tests -q`  # 期望：`0 failed`
2. `uv run --project apps/wuwa python -m compileall -q apps/wuwa/src && echo COMPILE_OK`  # 期望：`COMPILE_OK`
3. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1`  # 期望：退出 0（改动前先跑一次记录基线）
4. C3 断言：`grep -c '"wuwa_exit_code":  0' runtime/orchestrator/<C2的run_id>/result.json`  # 期望：`1`（PowerShell JSON 冒号后两个空格）
5. 最新 farm_echo 状态：`uv run --project apps/wuwa python -c "import json,glob; p=sorted(glob.glob('apps/wuwa/runtime/runs/*farm_echo*/result.json'))[-1]; print(p); print(json.load(open(p,encoding='utf-8'))['status'])"`  # 期望：`success`
6. 抗条件变化：新增单测必须覆盖根因负向复现；真机断言不依赖当日体力/邮件/活跃度状态；若做了 B2，必须有时间窗语义的单测（假时钟注入，参考 `test_daily_workflow.py` 的 `now_fn` 模式）。

## 【自检循环】

交付前必须做，做完再交：
1. 重新通读你改的全部 diff，当作第一次看到它。
2. 重跑全部验收命令（1–6）。
3. 显式回答：还有哪些地方可能受影响但我没检查？逐个检查（至少：day_rollup 同日合并、Feishu 卡片对部分成功语义、`elevate` 链路、`recovery_worker` 其余恢复 mode 是否被波及）。
4. 发现问题就修，修完回到第 1 步；直到一轮无新问题。

## 【阻塞】

1. 先在授权范围内自查：读上游 OK-WW 源码（只读）、写最小复现测试、查 marker 定义。
2. 仍不通：完成其余全部任务，把阻塞项单独列出，写清卡在哪、试过什么、需要人做什么决策。
3. 真机被占用（C1 不过）：等待并重试（最长等到当日 22:00），仍占用则该项标阻塞；代码/单测/文档照常交付，**不得伪造真机结果**。
4. 不许猜测性实现，不许为绕开阻塞扩大改动范围。

## 【交付报告】

1. 阶段 A 结论（带行号引用，a/b/c/d 逐条）。
2. 阶段 B 每项改动：文件+行号+意图；选择了 B1/B2/both 的证据依据；影响面清单逐条结论+全局搜索输出。
3. 验收命令 1–6 的真实输出（原文，含通过/失败数与真机 result.json 断言）。
4. 自检循环跑了几轮，最后一轮发现了什么。
5. 阻塞项与未覆盖风险；明晨检查三步（D3）。
6. 禁止项重申：失败不许写成通过；没跑不许写成跑了；引用截图必须同时贴机器断言命令与输出；报告与提交中不得出现 `.env` 密钥真实值。
