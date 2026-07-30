# 游戏自动化总仓库架构

## 边界

本仓库是同一台 Windows 机器上的统一部署单元。`orchestrator` 只通过应用 CLI、
退出码和清理后置条件编排，不导入游戏内部 Python 模块。两个应用各自拥有配置、
日志事实、报告、测试和第三方工具适配。

```text
Task Scheduler
  -> orchestrator/run.ps1
       -> apps/starrail: starrail-auto daily
       -> apps/starrail: starrail-auto cleanup
       -> apps/wuwa: wuwa-auto daily

Sunday Task Scheduler
  -> orchestrator/run.ps1 -Mode weekly-garden
       -> apps/wuwa: wuwa-auto weekly-garden
```

`config/automation.psd1` 是总控唯一配置入口，只保存应用相对路径、CLI 契约、
互斥锁和计划任务定义。游戏路径、模板、完成标记和用户培养映射仍留在对应应用。

## 目录

```text
game-automation/
├── apps/
│   ├── starrail/          星铁独立应用
│   └── wuwa/              鸣潮独立应用
├── packages/
│   └── game-automation-core/  Windows、UU 与汇报公共机制
├── config/                总控声明式配置
├── orchestrator/          跨应用状态机
├── scripts/               安装、注册与验证
├── docs/                  总仓架构
└── runtime/               总控运行证据，不入 Git
```

`game-automation-core` 只拥有跨游戏且语义一致的机制：交互桌面门禁与提权、
UU 精确进程生命周期/窗口图像操作/有限重启，以及飞书传输、Markdown 读取和
原子报告归档。游戏卡片识别、模板、完成日志、任务索引、计划时间和报告术语仍由
应用拥有。根目录是单一 uv workspace 和锁文件，两个应用通过工作区依赖共享核心包。

## 运行规则

- 所有入口共享 `Global\HCID274_GameAutomation` 互斥锁。
- 星铁业务失败仍允许鸣潮运行，但星铁清理失败会阻止桌面交接。
- 星铁清理先尝试断开加速，再精确退出全部 UU 受管进程，鸣潮始终从干净 UU 状态启动。
- 每次真实 GUI 总控在启动游戏外围设施前刷新 Codex 包 SID 与当前全信任程序的
  入站阻止规则，并关闭唯一的遗留防火墙提示，避免应用更新后的新路径阻塞桌面。
- 总控等待直接 CLI 进程并读取退出码，不等待游戏、UU 等后代进程树。
- 日常总链路保留 3 小时上限，鸣潮周常保留 4 小时上限。
- `.env`、运行日志、截图和报告不提交；第三方程序保持外置安装。
- `integration-smoke` 真实运行 UU、桌面交接与清理，但使用应用替身，绝不调用
  M7A/OK-WW 核心入口，也不发送飞书；预演证据使用 `.preview.json` 独立归档。
- 旧仓库在迁移验收后只作为回滚来源，不再承担正式调度；旧星铁日常与清理
  计划任务直接删除，注册和总控入口会清理意外重建的同名任务。
