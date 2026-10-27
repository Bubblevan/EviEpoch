# MemOS — 榜单系统档案

**来源口径：** Cycle 1 / Textual / Commercial Products；公开快照读取于 2026-10-27。榜单版本 **v2.0.1**；提交方式 **Evaluated**；Overall **45.89**。

| A 事实 | B 组合 | C 时间 | D 治理 | E 个性化 | G 执行 | H 安全 |
|---:|---:|---:|---:|---:|---:|---:|
| 68.95 | 53.41 | 56.53 | 44.30 | 48.73 | 9.82 | 56.13 |

## 公开机制与 EviEpoch 启发

论文以 MemCube 统一封装 plaintext、activation、parametric memory；系统层面组织接口、调度、生命周期、权限和来源。公开 v2.0.1 仓库描述图式记忆、Memory Feedback/Correction、删除、MemScheduler 与多 Cube 隔离。论文机制、公开 tag、实际参赛配置是三层证据，不能直接画等号。

对 EviEpoch 的启发：把记忆作为有类型、来源、状态、权限、生命周期的对象管理；调度和治理成为一等模块。AML 的固定 Answer 无法让参赛方注入其 KV/参数记忆，迁移重点是文本记忆资源与操作接口。

## 能力解释

C 56.53、H 56.13 较高，但 G 仅 9.82。D 44.30 下，删除/遗忘为 59.65，矛盾/冲突为 0。说明总治理分和模块名称都不能替代叶子能力检验；程序/规则证据与冲突处理是 EviEpoch 要独立建立的能力。

## 原始来源

- [官方榜单](https://agentmemories.ai/zh-cn/docs)
- [可复核原始 JSON](sources/aml-cycle1-leaderboard-2026-10-27.json)
- [与榜单同名的公开 tag](https://github.com/MemTensor/MemOS/tree/v2.0.1)
- [系统论文](https://arxiv.org/abs/2507.03724)
- [对应论文六问卡](../cards/12-memos.md)

资料中的产品机制说明与榜单分数分别记录；没有版本级消融时，不把某一模块认定为分数的原因。
