# APEX-MEM — 六问研究卡

- **论文：** *APEX-MEM: Agentic Semi-Structured Memory with Temporal Reasoning for Long-Term Conversational AI*
- **版本：** ACL 2026 Long Paper
- **原文：** [PDF](<../original/APEX-MEM - Agentic Semi-Structured Memory with Temporal Reasoning for Long-Term Conversational AI.pdf>)
- **阅读重点：** 事实/事件图表示、append-only 写入、GraphSQL/Search 消融；特别检查 append-only 本身是否有直接对照。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对结构化记忆在写入时合并或覆盖同一事实的旧值，从而丢失“何时成立、被哪段证据更新”的演化轨迹，导致历史状态查询和冲突判断失败。它不是单纯的向量噪声问题，而是 temporal fact-evolution failure。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 带说话人、锚定时间、文本及上下文的对话文档；查询时是自然语言问题。
- **内部状态：** SQLite 后端有向属性图，节点包含 Entity、Event、Fact。事实以主体—属性—值表示，附数据类型、有效期区间、置信度与证据出处；事实锚定到事件。
- **Add：** 从对话抽取实体、事件、事实并只追加，保存新旧演变，不在写入时裁定唯一当前值。
- **Search：** ReAct Agent 使用 SchemaViewer、EntityLookup、GraphSQL、Search 四类工具检索、过滤/遍历时序图并聚合证据。
- **输出：** 基于检索子图生成答案；论文中的答案生成 Agent 不属于 AML Search 输出契约。

## 03｜它作用于 Add、Manage 还是 Search？

Add 和 Search 都涉及；关键设计是把传统的写入时 **Manage** 延后到查询时：Add 追加保存历史事实与时间，查询时才按提问时间判断当前/过去状态并处理冲突。

## 04｜作者的哪一个消融实验真正支持其创新点？

Table 3 中加入 GraphSQL 后，Temporal 从 72.92% 升到 82.29%（+9.37 个百分点），Overall 77.19%→79.45%；加入 Search 后 Overall 达 87.00%，但 Temporal 回落到 79.17%。这说明图 SQL 时态查询对时间题有针对性价值，Search 混合工具对总体检索有帮助。

**因果边界：** 作者没有做“append-only 保留全部历史”与“写入时 consolidate/overwrite”的直接对照。GraphSQL 的增益支持时间结构可被查询利用，不单独证明“不覆盖”本身带来增益。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

可迁移原文/派生事实并存、事实带时间与证据指针、Search 按 current/as-of/history 选择候选。Search 应输出来源证据，让固定 Answer/Eval 作答。风险在于多次 LLM 抽取和多工具查询的成本；同步 Add 要明确抽取失败或持久化失败时的返回语义，并实测延迟。显式用户删除不能以“append-only”为由拒绝执行。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 保存原文和可选原子事实，当前向量路径没有显式事实有效区间、事件锚定、事实—来源链或时态 SQL。可先增加派生时间/来源元数据并保持原文不变，测试 current/historical 取证；不得把用户提供的 ACL 成绩摘要或图模型设计写成已验证的 EviEpoch 能力。
