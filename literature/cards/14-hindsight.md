# Hindsight — 六问研究卡

- **论文：** *Hindsight is 20/20: Building Agent Memory that Retains, Recalls, and Reflects*，arXiv:2512.12818v1。
- **原文：** [PDF](<../original/Hindsight is 20-20 - Building Agent Memory that Retains, Recalls, and Reflects.pdf>)
- **版本边界：** 系统论文、当前开源实现与商业榜 Cloud v1.0.1 分开记录。

## 01｜针对哪个具体 Memory Failure？

普通记忆层混淆来源事实与 Agent 推断，长历史组织不足，难以解释推理。它把记忆作为结构化推理底座，区分客观事实、Agent 经历、实体归纳和主观信念。

## 02｜输入、输出和内部状态是什么？

四个逻辑网络：world、experience、observation、opinion。TEMPR 的 retain 抽取保留参与者、时间范围和明确理由的 narrative facts，进行实体消解并建立 entity/temporal/semantic/causal links；recall 在 token 预算下运行语义、词法、图扩散激活与时间四路，融合后交 cross-encoder 重排。CARA reflect 接收记忆和行为 profile，生成回答并形成/强化带置信度的意见；Observation 是无性格偏置的实体摘要。

## 03｜作用于 Add、Manage 还是 Search？

retain 覆盖 Add 与结构化，recall 是多路 Search，reflect 与意见强化/实体归纳属于 Manage/Reasoning。原文有异步生成 observation，论文 temporal parser 的回退模型也不是 gpt-4o-mini；不能无修改套用第二届学术榜。

## 04｜哪一个消融真正支持创新点？

最清晰的整体对照是相同 OSS-20B 下 LongMemEval 从 Full Context 的 39.0% 到 83.6%，为系统整体提供证据。未找到逐一关闭四种检索、四网络或 CARA 的完整组件消融，因此不能由此证明因果边、意见强化或反思可靠性各自带来多少收益。其其他系统基线部分引用外部报告，模型与 judge 口径还需保留，不能把最高分当作完全匹配条件的消融。

## 05｜AML 固定 Answer/Eval、同步 Add 下适用什么？

时间/实体来源结构、多路召回、图扩散和 token 预算有直接价值。所有 LLM 部分应使用 gpt-4o-mini、embedding 使用 text-embedding-v4；反思只能帮助内部检索计划与可验证状态维护，Search 不输出 CARA 生成的最终答案。意见不能伪装成用户事实，Search 问句与选项不能作为新事实写回。异步摘要的可见性需由同步 Add 事务合同明确定义。

## 06｜相对 EviEpoch 新增什么？

新增 narrative/event 表示、实体/因果/时间图、词法与时间通道、图扩散、事实/观察/意见分型。它与原拟方案高度相关，故 EviEpoch 的研究差异应转向“状态与完整来源集合联合选择”“反证驱动修订”“巩固和遗忘的保真合同”，不能把四路融合或 Postgres 图存储单独当成原创。
