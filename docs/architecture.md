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
├── config/                总控声明式配置
├── orchestrator/          跨应用状态机
├── scripts/               安装、注册与验证
├── docs/                  总仓架构
└── runtime/               总控运行证据，不入 Git
```

暂不设置空的 `shared` 包。只有当两边存在经过验证且语义完全一致的实现时，才把
它提取为稳定公共包；外观相似但状态机不同的 UU 与桌面代码继续由应用拥有，避免
为了消除重复而制造运行时耦合。

## 运行规则

- 所有入口共享 `Global\HCID274_GameAutomation` 互斥锁。
- 星铁业务失败仍允许鸣潮运行，但星铁清理失败会阻止桌面交接。
- 总控等待直接 CLI 进程并读取退出码，不等待游戏、UU 等后代进程树。
- 日常计划任务保留 8 小时上限，鸣潮周常保留 4 小时上限。
- `.env`、运行日志、截图和报告不提交；第三方程序保持外置安装。
- 旧仓库在迁移验收后只作为回滚来源，不再承担正式调度；旧星铁日常与清理
  计划任务直接删除，注册和总控入口会清理意外重建的同名任务。
