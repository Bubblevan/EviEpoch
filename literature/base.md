# EviEpoch 文献研究基线

**整理日期：** 2026-10-27  
**第二届 AML 调研材料截止：** 2026-10-23（沿用用户提供报告口径）  
**用途：** 固定 EviEpoch 当前实现、2026 Agent Memory 研究视角和待验证问题。本文综合用户提供的两段 ChatGPT 对话、赛前调研报告、所给豆包论文总结及本地论文原文。调研报告中的竞赛信息不等于本文件独立核验的官方规则或榜单。

## 1. 两条工作线与资料边界

EviEpoch 研究拆为两条线：

- **接口基线线：** 保护已提交申请所依赖的 Add/Search 接口和证据输出契约。研究假设先放入离线评测或独立实验，不把它们写成已提交版本能力。
- **论文与算法研究线：** 对原文研究论文建六问卡，比较失败模式、算法状态、消融和迁移边界；对综述只做主题综述，集中写在本文件，不给综述建六问卡。

第一段 ChatGPT 对话建议优先研究时间状态、冲突治理与证据检索，并明确区分当前 EviEpoch 和 ActiveMemoryIndex 上游功能。第二段对话补充了 Du 综述的形式化与分类解读。赛前报告进一步提供优先阅读名单和 AML 竞赛上下文。论文卡中的机制和实验以 original/ 的 PDF 正文为准；迁移判断是研究假设，不是论文结论。

## 2. 当前 EviEpoch 实现基线

以下描述以本工作区当前实现为准，不能自动外推为上游 ActiveMemoryIndex 功能或已提交版本的表现。

### Add

- 接收带角色、内容和可选时间戳的消息数组，并用 user_id、session_id、request_id 标识请求。
- 每条非空对话轮次以原文形式保留；启用 LLM 时，从同一批消息提取第一人称原子事实，作为补充通道。
- 原文轮次和派生事实一起生成向量并写入 SQLite；同一 user_id 下重复 request_id 是幂等 no-op。
- Add 在成功返回前完成嵌入和持久化；失败时不应把未提交写入报告为成功。

### Search

- 对原始查询和可选的第一人称 Recall Query 分别嵌入，再按配置融合余弦相似度。
- 结果去重，并可扩展同一 chunk 的邻近原文轮次；应用 top-k、字符和 token 预算。
- 默认原文优先，返回证据条目而不生成最终答案；按 user_id 隔离。
- 当前主路径没有显式的当前/历史状态投影、事实有效期区间、BM25 会话融合、时间条件过滤或证据槽位规划。

### 评测与版本口径

本地 experiments/replay.py 统计 Evidence Recall 与 Complete Evidence Rate。它能比较证据取回情况，但不等价于 AML Overall，也尚未覆盖统一 Answer 模型下的端到端正确率、时间/冲突专项指标和成本长尾。赛前报告提到的 ActiveMemoryIndex 成绩、上游 rc6 能力和其他分支功能，不自动归属于当前 EviEpoch。

## 3. 四篇综述：统一问题地图

### 3.1 Du：Memory for Autonomous LLM Agents

**原文：** [PDF](<original/Memory for Autonomous LLM Agents - Mechanisms, Evaluation, and Emerging Frontiers.pdf>)，arXiv:2603.07670。  
**视角：** 把记忆作为持续运行的工程闭环，而不是一次向量查询。

- **Write–Manage–Read：** 写入决定什么进入记忆；Manage 处理去重、优先级、冲突、更新与删除；Read 按当前任务检索。任一阶段的错误可能污染后续多个决策。
- **三条正交轴：** 记忆的时间/功能范围（工作、情景、语义、程序）；表示载体（上下文、向量、结构化存储、可执行仓库及混合）；控制策略（启发式、提示式、自主/习得式）。图、向量、SQL 是载体，不等于维护策略。
- **机制与评测：** 综述上下文压缩、检索增强、反思、分层虚拟记忆、学习式控制；评估应同时覆盖任务效果、记忆质量、效率和治理。
- **典型 bad case（§7.3 “Staleness, contradictions, and drift”）：** 助手把生日卡寄给用户的前任，且用了旧地址；系统分不清 2024 和 2022 的地址。作者点出的能力包括时间版本、来源归因（用户陈述优先于 Agent 推断）、冲突检测及周期性整理。它是综述中的说明性场景，不是经过独立实验评测的 benchmark。

**对 EviEpoch 的启发：** 把原始证据、派生状态和检索动作分层描述；报告 Recall 时还要看证据是否完整、冲突是否被错误压制，以及固定 Answer 是否因此答对。

**补充核读（2026-10-27）：** §8 比较此前综述与长上下文替代问题；§9.1–9.10 列出十项开放挑战；§10 凝练为巩固不丢失、因果检索、可信反思和安全遗忘四题。此前笔记把 §7.3 旧地址说明性案例当成主线，覆盖不足。完整问题、方法与 EviEpoch 的逐项映射已补入 [综述研究议程](survey-agenda.md)，此议程是新 PRD/技术设计的主轴。

### 3.2 Luo 等：From Storage to Experience

**原文：** [PDF](<original/From Storage to Experience - A Survey on the Evolution of LLM Agent Memory Mechanisms.pdf>)，Findings of ACL 2026。  
**视角：** 用 Storage → Reflection → Experience 描述研究机制从轨迹保存、局部精炼到跨轨迹经验抽象的演进。

- **Storage：** 以线性、向量或结构化方式留存交互轨迹，主要保证保真与持续性。
- **Reflection：** 从轨迹出发，借助内省、环境反馈或协作校验来筛选、纠错或组织记忆。
- **Experience：** 跨轨迹抽象出可复用的经验/策略，可为显式、隐式或混合形态。
- 这是一种综述组织与研究假说，不意味着每个系统都必须线性升级，也不意味着 Experience 总比忠实保存原始证据更适合事实检索。该综述强调主动探索、跨轨迹抽象和面向 Experience 的评测仍有研究空间。

**对 EviEpoch 的启发：** 当前以 Storage 为主并有少量派生抽取。事实检索比赛不应为了追求“更高级的经验”而丢失可核验原文；经验泛化要单独做跨任务评测。

### 3.3 Hu 等：Memory in the Age of AI Agents

**原文：** [PDF](<original/Memory in the Age of AI Agents - A Survey - Forms, Functions and Dynamics.pdf>)，arXiv:2512.13564v2。  
**视角：** 将记忆放在 Forms × Functions × Dynamics 三个层面，避免“短期/长期”二分法概括所有 Agent Memory。

- **Forms：** token-level（扁平、平面、层级）、parametric（模型内部或外部参数化记忆）、latent（生成、复用、变换潜表示）以及适配机制。
- **Functions：** factual（用户事实与环境事实）、experiential（案例、策略、技能、混合经验）、working memory（单轮和多轮任务状态）。
- **Dynamics：** memory formation、evolution（consolidation、updating、forgetting）与 retrieval（何时取、如何构造 query、用何种策略、如何处理候选）。
- 综述还讨论 benchmark、开源框架及自动化管理、RL、多模态、共享记忆和可信治理等方向。

**对 EviEpoch 的启发：** “用户住址”属于 factual memory；“哪种检索策略过去成功”属于 experiential memory；当前问题所需的临时条件属于 working memory。三者不能混成一个相似度索引。对 birthday case，最直接的是 factual memory 的版本维护和 retrieval，而非经验策略本身。

### 3.4 Yang 等：Graph-based Agent Memory

**原文：** [PDF](<original/Graph-based Agent Memory - Taxonomy, Techniques, and Applications.pdf>)，arXiv:2602.05665v1。  
**视角：** 聚焦图结构，按记忆生命周期讨论 extraction、storage、retrieval、evolution，并对短/长期、知识/经验、非结构化/结构化作分类。

- 图的价值在于显式表示实体、事件、关系、层级和多跳路径；可结合时间元数据和 episode/source links。
- 生命周期分别对应实体/关系抽取与消歧，图模式与节点边存储，邻居遍历/路径检索，以及图更新、合并、冲突处理、剪枝和拓扑调整。
- 综述把实体消歧、时间表示、图增长与效率、跨任务评估和可信更新列为挑战。采用图并不会自动保证事实正确；构图成本、时态语义和更新规则仍需单独设计与评测。

**对 EviEpoch 的启发：** 先确认一个关系边或时间字段是否改善某类题，不以“用了图”作为结果。对同步 Add，LLM 实体/关系抽取及慢速图整理是否完成、何时 Search 可见，都是架构约束。

### 3.5 四篇综述合读后的研究定位

Du 提供系统闭环，Hu 提供形式/功能/动态分类，Luo 提供从存储到经验的过程视角，Yang 聚焦图表示的生命周期。它们互相补充，但并没有给出一个普适最佳架构。对 EviEpoch，当前优先问题是：

1. 不丢原始 evidence 的前提下，能否更好地识别当前/历史时间意图？
2. 新事实何时代表真实变化，何时是抽取噪声或来源冲突？
3. Search 如何提交有来源、时间一致且足够完整的证据集合？
4. 新 Manage/Add 操作能否满足同步写入、隐私删除与延迟预算？

## 4. 2026 研究论文阅读地图

研究论文各有一张六问卡，综述无六问卡。cards/ 当前覆盖 14 篇研究论文：

| 论文 | 主要研究对象/贡献 | 与 EviEpoch 的关系 |
|---|---|---|
| APEX-MEM | 时态属性图、事件锚定事实、append-only 历史、查询时多工具推理 | 对时间状态和来源保留高相关；没有 append-only 对 overwrite 的直接消融 |
| PreCoMem | 原始 episodic cache + 置信度 belief store；MAINTAIN / PROFILE / CORRECT | 对变化与噪声区分高相关；可能增加同步 Manage 成本 |
| SimpleMem | 语义压缩、会话内综合、意图感知混合检索规划 | 多视图 Search 值得拆开验证；不可逆压缩有长尾事实风险 |
| A-MEM | 结构化笔记、动态链接及旧笔记演化 | 说明链接/演化有用；不是有效时间区间系统 |
| MemoTime | 时序图、Tree of Time、时间算子和推理经验 | 可借鉴 query 时间意图/约束，不需要整套 temporal QA Agent |
| MemWeaver | 时序图 + 经验 + 原文 passage 的三层记忆和双通道检索 | 结构化检索与来源证据协作，与 evidence-only Search 接近 |
| MAGMA | 语义、时间、因果、实体多图与意图路由 | 关系视图消融可参考；同步 Add 与异步图巩固需区分 |
| MemGuide | 意图对齐检索与 missing-slot filtering | 支持“按缺失证据选取”，但评测是任务型对话 |
| ARTEM | 时间、空间、实体、内容四通道情景记忆与拒答 | 可参考事件字段和拒绝低置信匹配；没有组件消融 |
| ReFind | 原始聊天记录、BM25/session-RRF、多轮受控检索 | 非结构化路线强对照；直接适配原文证据 Search，仍无显式有效期状态机 |
| G-Memory | insight/query/interaction 三层图，多 Agent 协作轨迹经验 | 适合作为跨任务经验背景；目标与 AML 用户事实检索不同 |
| MemOS | 统一记忆资源、调度与生命周期 | 提供 Manage/系统框架，需区分可迁移文本模块与 KV/参数记忆 |
| Mem0 | 抽取后执行显式管理操作；图扩展用失效标记 | 更新/删除语义与来源保留分开验证，论文不等同商业 v3 |
| Hindsight | 四网络、TEMPR 多路/图扩散、CARA 反思 | 是完整设计的重要先例；联合状态/依赖选择与反证修订需要独立贡献证据 |

逐篇方法、实验、消融缺口和迁移判断见 [六问卡目录](cards/README.md)。

## 5. 与 AML 竞赛约束的连接

第二届赛前调研报告将赛道概括为：参赛方控制 Add/Search，平台统一 Answer/Eval；Add 需要同步完成；Search 提供可追溯证据，不自行生成最终答案。正式提交前仍应核对赛事当期官方协议。

报告所列第一届公开开源成绩：InvMem 45.06、ReFind 44.97、ActiveMemoryIndex 44.84。商业产品榜 MemoraX 58.02 只作为不同口径的能力参照，不能与开源榜直接作公平排名。以上数字沿用用户所给报告，不在这里重抓或复算。报告强调时间状态、跨会话更新、冲突治理、多证据组合与 Governance 的不足；单项榜单分数不能替代细项能力审查。

## 6. 当前候选假设（尚未在 EviEpoch 实测）

| 假设 | 预期观察 | 应放弃或收窄的条件 |
|---|---|---|
| H1：查询条件化时间约束优于统一 recency boost | Current-State、Historical-State、Event Ordering 同时改善，Fact Recall 无明显回退 | 仅改善当前状态，却损伤历史证据 |
| H2：原文不可变 + 软状态投影优于硬覆盖或纯追加 | 冲突问答更准，同时仍可恢复旧值；误判率受控 | 错误 supersession 隐藏正确来源 |
| H3：证据槽位规划优于独立 Top-K | 多跳证据覆盖和固定 Answer 正确率一起改善 | 只涨 Recall@K，端到端答案因噪声下降 |
| H4：检索控制可获得接近结构化记忆的收益 | 相同 Answer/Eval 下，BM25/session-RRF/时间过滤提升证据质量 | 延迟或多轮调用超预算，收益不能复现 |

推荐比较必须在相同样本、相同 Answer/Eval、相同预算下进行，并同时报告证据召回、完整覆盖、Current/Historical 状态正确率、冲突误判率、同步 Add 延迟和错误/超时。

## 7. 六问研究卡规范

只对研究论文建立六问卡：

1. 论文针对哪个具体 Memory Failure？
2. 核心算法的输入、输出和内部状态是什么？
3. 它作用于 Add、Manage 还是 Search？
4. 作者哪一个消融实验真正支持其创新点？如果没有直接消融，要明确说明。
5. 在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？
6. 与当前 EviEpoch 代码相比，真正新增了什么？

论文原文结果、用户对话中的二手总结、对 AML 的迁移判断和 EviEpoch 本地实测分开写。没有直接消融或本地证据时，不写成“已证明有效”。

## 8. 文件导航

- [研究区说明](README.md)
- [综述与研究基线](base.md)
- [综述 bad case 与论文方法对照](bad-case-crosswalk.md)
- [14 篇研究论文六问卡](cards/README.md)
- [论文原始 PDF](original/)

original/ 中包含 14 篇研究论文和 4 篇综述 PDF，均按可读的完整论文标题命名。赛前报告列出的其他系统只作为背景，不自动纳入这轮逐篇精读范围。


## 9. 竞争系统与目标设计更新

新增 [商业产品 Top 10 档案与官方原始数据](systems/README.md)，以及 [对 EviEpoch 的启发](systems/eviepoch-implications.md)。另补 MemOS、Mem0、Hindsight 三篇原文与六问卡。第 6 节保留此前以时间检索为中心的假设记录；新产品的完整研究主张以 [综述研究议程](survey-agenda.md) 和 [PRD](../docs/PRD.md) 为准，不再把时间检索作为全部目标。
