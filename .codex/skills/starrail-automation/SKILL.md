---
name: starrail-automation
description: Maintain and debug this repository's Windows Star Rail automation across Task Scheduler, UU Accelerator GUI recognition, March7thAssistant launch/watchdog behavior, evidence preservation, DeepSeek log summarization, and Feishu cards. Use for failed or missed daily runs, UU image/retry problems, proxy or DNS failures, resolution/DPI issues, incorrect success reporting, schedule changes, and architecture modifications in this project.
---

# 星铁自动化维护

## 工作顺序

1. 先读 `docs/架构蓝图.md`，再检查 `git status --short --branch`。不要根据旧根目录入口推断现状。
2. 先取证再修改：检查 `runtime/logs/`、`runtime/evidence/`、M7A 当日日志和计划任务状态。
3. 将问题归入桌面/权限、UU/网络、M7A/看门狗、AI/飞书之一，只加载对应参考文档。
4. 使用 `uv` 管理依赖和执行命令。正式入口统一为 `uv run starrail-auto ...`。
5. 优先补回归测试，再修改最小责任模块；不要把业务逻辑重新塞回 CLI 或单一大文件。
6. 依次运行 `uv run pytest -q`、`uvx ruff check --select E,F,I --ignore E501 src tests scripts`、`uv build`。
7. GUI 实测只在用户明确要求时执行。先做无副作用检查，说明将影响哪些进程和窗口，再进行一次受控测试。

## 不可破坏的约束

- `main` 不能使用任意硬超时；只根据本轮日志边界内的每日实训结果确定成功。
- 看门狗失败时保留游戏和 Launcher 现场，只允许停止本轮 Assistant，并保存截图和日志。
- UU 识图必须在管理员、交互式桌面、DPI aware 和 2560x1440 主屏环境运行。
- UU 每步按 0.25 秒轮询；一次链路最多重启 UU 3 次，总共 4 轮。成功、失败和异常出口都尽力最小化 UU。
- AI 只组织已解析事实，不修改业务成功状态，不检查自己的输出，不直接修改养成计划。
- 固定提示词只能放在 `src/starrail_auto/reporting/prompting/system/`；用户可编辑内容只能放在 `UserContext/`。
- 计划任务必须使用交互式登录和最高权限；同名覆盖注册，禁止创建重复任务。
- 不读取、输出或提交 `.env` 中的真实密钥。

## 按需参考

- Windows 权限、DPI、Apollo 分辨率和计划任务：读 [Windows桌面与计划任务.md](references/Windows桌面与计划任务.md)。
- UU 更新弹窗、重试、网络守卫、代理和 Fake-IP：读 [UU与网络排障.md](references/UU与网络排障.md)。
- M7A 启动、当前运行验收、看门狗和证据保留：读 [M7A看门狗与证据.md](references/M7A看门狗与证据.md)。
- 日志解析、DeepSeek、养成上下文和飞书卡片：读 [AI汇报与飞书.md](references/AI汇报与飞书.md)。
