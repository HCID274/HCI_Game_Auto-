# 公共能力提取记录

## 稳定后提取公共 UU/桌面包

状态：已完成。跟踪：
[GitHub Issue #1](https://github.com/HCID274/HCI_Game_Auto-/issues/1)。

共享包为 `packages/game-automation-core`，根级 uv workspace 使用单一锁文件。

实施顺序：

1. 对比两边 UU、进程识别、窗口前置、DPI、DesktopGuard、截图与错误模型。
2. 区分真正相同的机制与游戏专属模板、状态和恢复策略。
3. 建立独立 `packages/game-automation-core`，先迁移无业务语义的基础能力。
4. 用契约测试保证两个应用在迁移前后行为一致。
5. 逐个应用切换依赖，不进行一次性双边替换。
6. 通过 `integration-smoke` 验证真实 UU/交接/清理，以替身隔离游戏核心任务。

验收标准：公共包不包含游戏名、模板名、计划时间、完成日志或第三方任务索引；
任一应用仍可单独运行和测试，总控只依赖其 CLI 与退出码。
