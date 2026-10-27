# Vectorize Hindsight Cloud — 榜单系统档案

**来源口径：** Cycle 1 / Textual / Commercial Products；公开快照读取于 2026-10-27。榜单版本 **v1.0.1**；提交方式 **Evaluated**；Overall **38.54**。

| A 事实 | B 组合 | C 时间 | D 治理 | E 个性化 | G 执行 | H 安全 |
|---:|---:|---:|---:|---:|---:|---:|
| 46.43 | 40.43 | 16.87 | 34.83 | 55.69 | 24.32 | 39.35 |

## 公开机制与 EviEpoch 启发

Hindsight 论文区分 world、experience、observation、opinion 四类记忆，TEMPR 负责 retain/recall，CARA 负责 reflect。TEMPR 包含语义、词法、图扩散激活、时间四路检索与融合/重排；Opinion 是带置信度的主观判断，Observation 是事实归纳，二者不应混成客观来源。Cloud v1.0.1 的部署设置未与论文配置逐项绑定。

对 EviEpoch 的启发：事实、经历、摘要、反思判断必须显式分型；多路召回和图扩散已有充分先例。EviEpoch 应把研究贡献落在“查询条件化状态推断 + 证据依赖集合选择 + 反证驱动修订”，而不将多路融合本身当作新算法。CARA 的最终答案生成不能作为 AML Search 的输出。

## 能力解释

此次补齐第十名：Overall 38.54，C 16.87，D 34.83，H 39.35。论文中的高 LongMemEval 成绩与该商业榜条目不是同一实验；跨数据、模型、版本和 Answer 流程不能直接归因。

## 原始来源

- [官方榜单](https://agentmemories.ai/zh-cn/docs)
- [可复核原始 JSON](sources/aml-cycle1-leaderboard-2026-10-27.json)
- [原作者项目](https://github.com/vectorize-io/hindsight)
- [系统论文](https://arxiv.org/abs/2512.12818)
- [当前检索实现说明](https://github.com/vectorize-io/hindsight/blob/main/skills/hindsight-docs/references/developer/retrieval.md)
- [对应论文六问卡](../cards/14-hindsight.md)

资料中的产品机制说明与榜单分数分别记录；没有版本级消融时，不把某一模块认定为分数的原因。
