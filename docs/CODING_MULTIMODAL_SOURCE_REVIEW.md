# Coding / Multimodal 扩展：源码审阅与迁移依据

审阅日期：2026-11-02，Asia/Shanghai。对应 [TRD §18–§23](TECHNICAL_DESIGN.md#18-三赛道共享核心与扩展边界)。

本报告沿实际输入、写入/管理、表示、候选检索、上下文恢复及最终使用路径记录源码依据。以下文件是本次具体阅读的关键路径；运行、界面、其他可选算法和完整数据集不在阅读完成声明中。没有运行参考系统或新赛道评测。排名与百分比来自用户提供的调研上下文，不用于证明当前公开 commit 的具体算法贡献。

## 1. 版本锁定与本地资料

| 参考 | 审阅版本 | 本地状态与归属 |
|---|---|---|
| claude-mem 历史基线 | v12.1.1，commit `06b997e3d03e68792e964e3b81c259c00bceb0dc` | 本地 claude-mem 已有 tag，使用 `git show v12.1.1:<path>` 阅读；该版本 LICENSE 为 AGPL-3.0 |
| claude-mem 当前目录 | `52a1dfea2556e126c959c26d8e25f6fa7da14521`，package 15.8.2 | 当前目录不是比赛旧版；本次核心链路结论按旧 tag 核对，不把新功能归入历史成绩 |
| ATM-Bench / MMRAG | `ef4e5dff1a47ec71213a06e359f02753defa8fb1` | 已有本地仓库；MIT |
| causal-memory | `39053ff6fd7a18771d59f0c10240b19c37f412b6` | 本次补充浅克隆，检出 crates 与 docs/benchmarks；Apache-2.0 |
| Mem-Gallery | `a93959e1e978a6a7d77798ae92c2ffe41c538c62` | 本次补充关键路径稀疏检出：README、LICENSE 和七个 benchmark 文件；源码逐文件与 commit 的 Git blob hash 校验一致；MIT |
| MemoraX Code | 详见[已有五系统报告](../literature/reference/README.md#1-版本与证据边界) | 本次复核 ARCHITECTURE、AGENTS 和 Codex session registry；引用其客户端身份/来源机制，不推断服务端排名公式 |

固定源码入口：[claude-mem 历史基线][cm-root]、[ATM-Bench][atm-root]、[causal-memory][causal-root]、[Mem-Gallery][gallery-root]。图像、对话数据集、模型权重和评测服务没有随新参考源码下载。

## 2. claude-mem v12.1.1：从工具事件到分层恢复

### 2.1 调用链

    PostToolUse
      → observationHandler
      → /api/sessions/observations
      → SessionRoutes: 过滤与 privacy 检查
      → SessionManager.queueObservation / PendingMessageStore
      → Agent 消费队列，buildObservationPrompt
      → parseObservations / parseSummary
      → processAgentResponse
      → SQLite storeObservations 原子存储
      → Chroma 字段级异步同步
      → search: 索引 / timeline: 上下文 / get_observations: 详情
      → Coding Agent 使用恢复的观察与工程上下文

| 文件与关键入口 | 具体行为 | EviEpoch 的迁移含义 |
|---|---|---|
| [observation.ts:16][cm-hook] | PostToolUse 取得 session、cwd、tool name/input/response；排除项目；worker 不可达时 hook 继续执行 | 原生采集可采用不阻塞工具的捕获方式；官方 Add 成功合同仍需等待最终发布 |
| [SessionRoutes.ts:507][cm-session] | 根据配置跳过工具、自身 session-memory 文件等 meta 操作；执行来源/session/privacy 检查，排队后回 queued | 队列入站成功不等于所有观察和索引完成；不能直接复用为 AML Add 的成功响应 |
| [SessionManager.ts:206][cm-manager]、PendingMessageStore | 将工具观察放进持久 pending 队列，由 session 对应 Agent 消费 | 保留事件关联和恢复语义，不用最新一个 session 猜测工具归属 |
| [prompts.ts:91][cm-prompts] | 将工具名、发生时间、cwd、参数和返回串为观察输入，允许输出多个观察或跳过 | 要迁移观察字段和完整原事件关联，不能只复制一段“总结经验”提示词 |
| [parser.ts:33][cm-parser] | 解析 type/title/subtitle/facts/narrative/concepts/files_read/files_modified；过滤空观察与无效类型 | 解析器和类型表属于行为包；生成的 facts 还需 EviEpoch 原始 span 依据 |
| [ResponseProcessor.ts:48][cm-response] | 解析观察/摘要，SQLite 批次存储；成功后确认处理消息；后续 Chroma 同步可失败而继续 | 该系统允许 SQLite 与向量索引暂时不一致；EviEpoch 沿现有同 generation 表示发布实现同步合同 |
| [ChromaSync.ts:123][cm-chroma] | narrative、facts 等分别成为向量 document，metadata 回指同一个 SQLite observation | 值得保留的机制是字段级表示和父对象恢复；不是一个观察只做一条总摘要向量 |
| [SearchOrchestrator.ts:81][cm-search] | 有文本 query 时优先 Chroma；失败后删除 query 转结构化过滤；无 Chroma 时该文本查询路径返回空结果 | EviEpoch 保留可独立工作的 BM25，不把过滤回退描述成有质量保证的关键词检索 |
| [SessionSearch.ts:41][cm-sqlite] | FTS5 表/触发器为兼容保留，注释明确当前不用于 search | “SQLite FTS5 + 可选 Chroma”不能准确描述该 tag 的普通文本查询默认路径 |
| [ChromaSearchStrategy.ts:93][cm-strategy] | Chroma 有界候选，经 90 天 recency 过滤、doc type 分类，再回 SQLite hydrate/filter/order | 候选窗口、年龄过滤和 hydrate 排序都影响行为；EviEpoch 改为版本适用性时要作为显式差异做对照 |
| [mcp-server.ts:250][cm-mcp]、TimelineService | search 返回短索引；timeline 恢复 anchor 周围的观察/会话/prompt；get_observations 批量读取完整详情 | AML Search 内执行三个阶段，最终交付内容；原生 Agent 端可暴露三个工具 |

### 2.2 观察结构提供了什么、还缺什么

`plugin/modes/code.json` 区分 bugfix、feature、refactor、change、discovery、decision，观察强调有持续价值的工程变化和调查发现；Session Summary 记录 request/investigated/learned/completed/next_steps。它们能帮助恢复做过什么、发现什么及下一步方向。

这些字段没有建立 Git 提交 DAG、dirty overlay、符号版本或精确测试快照。文件名、project、会话时间和“completed”描述不足以证明旧 patch 对当前仓库适用，也不足以证明测试在本次代码上通过。TRD 新增版本/测试对象与 applicability assessment 的依据就在这里。

旧版分层工具的节省比例是作者工具说明中的主张，本次没有做 token/效果测量。设计迁移的是按需恢复流程，不预先写入某个提升百分比。

## 3. causal-memory：决策/结果类型与真实评测入口

| 文件 | 阅读到的实现 | 对 TRD 的作用 |
|---|---|---|
| `crates/causal-memory-cli/src/amc.rs` | raw/distill 写入模式；同步 Add；按 user 物理分库；当前默认 retrieval 为 fused，另有 spread/merge；可选 options 查询扩展 | 不能从项目名或因果图能力推断所有 AML 检索都走因果传播 |
| `crates/causal-memory/src/memory/fused.rs` | BM25 seed IDs 与 chunk semantic 排名独立计算后 RRF；每臂深度至少 50、最多 200；返回原文 chunk；不触发图重建或 Hebbian 记忆更新 | 原文证据臂和纯读 Search 是实用模块边界；保留候选深度和分数/来源映射 |
| `crates/causal-memory/src/store/types.rs` | CausalEntry 包含 decision/outcome、relation、polarity、event_time、valid_to、superseded_by、context fingerprint、influenced_by 等 | Coding 经验需要结果极性、上下文和修订依据，不能只有向量化的成功总结 |
| `crates/causal-memory/src/reasoning_extractor.rs` | 从显式 assistant 文本抽取最多三个决策/经验，经模型判断后记录；当前路径还存在前 2000 字符截取及把 reasoning 存为 outcome 的选择 | EviEpoch 只处理实际可用的文本来源；完整 span 独立保留，推断关系与实验结果分别定型 |
| `crates/causal-memory/src/query_router.rs` | 用规则识别 fact、causal、chain、directory、unified 意图 | 可用作便宜候选路由对照；不代表模型已经完成领域根因或适用性判断 |
| `docs/benchmarks/amc-2026.md` | 明确区分冻结 v0.9.0-amc1 与 main；旧轮的适配器曾有独立 lexical/semantic 路径 | 不将当前实现、历史 Textual 配置与用户粘贴的 Coding 榜数字混为同一实验 |

完整固定目录见 [causal-memory 当前审阅 commit][causal-root]。本次没有重新验证用户提供的 Coding 52.67% 及分项结果对应哪一个提交和配置；设计选择来自代码结构，不来自对分数的因果推断。

特别要保留“决策 → 观察结果 → 适用前提”的区别：模型对一段决策说明判断为 causal，不足以把其理由升级成真正执行后的 outcome。TRD 将 TestRun、真实执行结果和 hypothesized 因果分开，另以新证据修订经验；访问频率不作为独立支持。

## 4. ATM-Bench / MMRAG：多视图索引与原始媒体使用

### 4.1 输入、表示、检索到回答

    image_processor: EXIF / OCR / caption / tags / entities
      → batch JSON 与真实 image/video 路径
      → retrieve.utils: MediaTextConfig / RetrievalItem
      → Retriever.build_index / encode_items / encode_query
      → 原始召回候选与 recall 记录
      → Text / VL Reranker
      → build_text_messages 或 build_multimodal_messages
      → baseline Answer 模型与 judge

| 文件与入口 | 代码事实 | 对 EviEpoch 的约束 |
|---|---|---|
| `memqa/mem_processor/image/image_processor.py` | process_single_image 汇总 EXIF、short caption、caption、tags、entities、OCR、敏感标记等；多种模型调用分开进行 | 保留字段、处理模型和来源种类；拍摄元数据、用户说明与 VLM 观察不能扁平成权威事实 |
| `memqa/mem_processor/image/config.py` | prompts 分别要求描述、标签、实体和 OCR，并可注入已知日期/位置 | 字段生成依赖提供的元数据，不能再把生成 caption 当成独立支持 |
| `memqa/retrieve/utils.py` | MediaTextConfig 控制 ID/type/time/location/short caption/caption/OCR/tags；RetrievalItem 绑定 text、modality、image/video path、metadata | 多个视图共享媒体身份，独立字段开关适合受控消融；生产 key 使用内容与表示身份，而非只依赖文件名或 mtime |
| `memqa/retrieve/retrievers.py` | BaseRetriever 做缓存、编码和向量内积 top-k；有 Text、SentenceTransformer、Qwen3-VL、dual、CLIP/VISTA 等配置 | 参考脚本是实验内存索引；EviEpoch 仍需 PG/pgvector 的 namespace/事务/规模机制 |
| 同文件 Qwen3VLRetriever | encode_items 能携带图片/视频；encode_query 接收 str，只构造 text payload | 不能把原函数直接称为已支持图片 Query；目标 adapter 必须补齐 query parts |
| 同文件 Qwen3VLDualRetriever | 图像/文字分别编码再拼接并归一化，query 向量复制后拼接 | 保留等权 dual 的复现臂；EviEpoch 多路融合是另一种明确登记的方法 |
| `memqa/retrieve/rerankers.py` | Text 与 Qwen3-VL reranker 分别评分；VL documents 带 text 与真实媒体，query 仍为文本 | 提供多模态候选评分的参考；混合 Query、逐 obligation 覆盖及来源核验是新增工作 |
| `memqa/qa_agent_baselines/MMRag/mmrag_retrieve_rerank_answer.py` | 分 retrieve、rerank、answer 阶段保存中间结果，分别计算检索与重排 recall | 原始候选、重排结果与最终答案应分别测量；gold IDs 仅供离线计算指标 |
| `memqa/qa_agent_baselines/MMRag/mmrag_utils.py` | 给候选插入证据标签及 Base64 原图，视频可采帧；build_multimodal_messages 同时加入问题与 answer prompt | 迁移媒体恢复/顺序，官方 Search 不复制 baseline 的 Answer 阶段；实际 MIME 不能一律硬写 JPEG |

完整固定目录见 [ATM-Bench commit][atm-root]。以上关键路径阅读不等于已经运行所有模型组合，或已重新验证 README 的论文结果。

### 4.2 设计上的新增部分

ATM 的 RetrievalItem 没有 EviEpoch 所需的 part ordinal、精确区域 provenance、独立的用户授权/generation 和 typed visual proof。TRD 的 Region、原始 part、媒体发布屏障、图像 Query、图文统一删除和原始视觉义务属于 EviEpoch 新增设计。

一张图片命中并不代表正确使用了它。Reranker 可以找出相关图片，而 Answer 仍可能漏看颜色或时间。新增评测因此同时检查原图召回、视觉支持、证据组完整性和最终回答，不能只看 R@K。

## 5. Mem-Gallery：原图/Caption 对照和跨会话评估

| 具体文件/入口 | 真实行为与设计启发 |
|---|---|
| [run/run_bench.py][mg-run] 的 DialogueAgent.memory_store | is_multimodal 为真时保存原图记录；文本 memory 把 image ID/caption 加到文字；适合建立输入受控的 caption 对照 |
| 同文件 memory_recall | 多模态 query 为 text+image dict；文本 memory 把 query caption 加到文字；可直接核对“Query 图像是否真的被使用” |
| 同文件 process_conversation | 合并 user/assistant 一轮对话，取 input_image 的第一张并附日期/dialogue_id；EviEpoch 官方 adapter 需要保留全部有序 parts，不能复制这一处单图折叠 |
| 同文件 run_mm_bench | 先存全部会话，再遍历 human-annotated QAs；从 point 读取分类，从 clue 读取离线评价来源 ID |
| 同文件 VLMAgent.fast_run_with_mm_memory | 将恢复的文本和实际 Base64 图片送给 VLM，Query 图像单独附加 |
| [memory/MMMemory.py][mg-memory] | LinearStorage + MultiModalRetrieval；store_op/recall_op 编排；manage/optimize 是空实现，不提供完成的演化治理机制 |
| [operation/Store.py][mg-store] 的 MMMemoryStore | 原始 observation 入 storage，再编码入 multimodal retrieval；输入支持 text、image 或混合 |
| [operation/Recall.py][mg-recall] 的 MMMemoryRecall | embedding 排序 ID → storage hydrate → utilization，记录 last_retrieved_ids；原始多模态利用而非只输出 caption |
| [function/MultiModalEncoder.py][mg-encoder] | CLIP 对图文分别编码后平均、归一化；GME 有自己的混合编码路径；这与 ATM dual-concat 是不同对照 |
| [function/MultiModalRetrieval.py][mg-retrieval] | tensorstore 累积向量、query 同 encoder、cosine/dot/L2 排序并 top-k；没有 PG generation 或媒体治理 |
| [evaluate/evaluation.py][mg-eval] | calculate_retrieval_metrics 用 clue IDs 算 Precision/Recall/HitRate；文件还组织答案 F1、judge 等评估入口 |

本快照的 `MultiModalRetrieval.py` 导入 `MMEmbedEncoder`，但同提交的 `MultiModalEncoder.py` 没有该类定义。因此不能仅按 README 宣称这条 baseline 已可运行。后续复现应使用有记录的 compatibility patch 处理该未定义导入，并保存原始 commit；本次没有修改参考算法或运行结果。

该 benchmark 的数据集需独立从 Hugging Face 获取；源码下载并未下载图片/对话数据。其 runner 的全部会话先写入方式也不自动覆盖 Streaming；TRD 另定义前缀会话与 generation 测试。

## 6. 官方合同核对与模型配置

来源：[AML 官网与 API Guide](https://agentmemories.ai)，核对日期 2026-11-02。

| 项目 | 本次看到的明确内容 |
|---|---|
| Coding 正式计分 | CAMBench Coding：150 个任务 × relevant/noisy = 300 次尝试 |
| SWEContextBench | 参考目录，当前不作 AML Coding 正式计分 |
| 图文输入 | Add content、Search query 及 Search response content 均为有序 ContentPart[]；Textual/Coding 使用字符串 |
| 图片格式 | JPEG/PNG/WebP，inline Base64 Data URI，不接受远程 image_url |
| 图片大小 | 单张解码后 ≤10 MiB，Add 合计 ≤30 MiB，Search response 合计 ≤30 MiB |
| Query 聚合图片限制 | 该公开说明未单列数字；TRD 将本方 30 MiB Query 上限明确标记为部署配置 |
| 返回与上下文 | 候选按相关性顺序；正式外部 top_k=100；Answer 输入总预算 117,760 tokens |
| 模型 | 详细双语文档要求开源 LLM=gpt-4o-mini、embedding=text-embedding-v4、reranker 不限；商业产品不受上述限制 |

短 checklist 只概括 Add 的模型要求，详细文档覆盖 LLM 相关组件。TRD 使用详细规则描述 competition 模型配置；视觉/代码专用 embedding 在 research 下单独记录。不同模型政策的结果各自统计。

用户提供的 99/51 和四档噪声用于 Coding 分层配置；三组 7/4/3 多模态子能力用于工程验收组织。具体每个数据集标签的枚举、样本分配和映射由公开数据 manifest 固定，本次没有把它们推断为尚未读取的底层标签表。

## 7. 本地实现差距与可检验的迁移

当前 EviEpoch 的 MessageBody、SearchBody、Message、Source 和 Evidence 的内容仍是字符串；provider 的规划、绑定、披露也以字符串准备输入。单独让 API 接收数组不会使后续 .strip/render/切片/token 路径支持图片。

现有独立 span、图/时间通道、Qwen 重排、来源证明与原子 generation 可以提供核心协议；新域必须扩展来源和证据身份。代码版本不等于一个日期字段，图片支持不等于 caption 有一个 source_id。TRD 因而明确增加 RepositorySnapshot 和 EvidenceAtom，并升级来源依赖、集合选择和最终装配。

直接迁移按完整 behavior capsule 对照：原始输入、prompt/解析器、默认参数、索引、排序、预算、错误/降级和最终恢复。设计明确改变的地方分别列组，不能以函数同名或大体相似代替语义对照。相关实现与实验需求见 TRD §21–§23。

### 本次本地读取快照的 SHA-256

这些哈希对应本次审阅时的文件内容，用于识别含未提交进展的工作区，不声称是冻结发布版本。

| 文件 | SHA-256 |
|---|---|
| eviepoch/api/app.py | `bd83735885948582bb8911ec282cd2ae2bcc1c86329d4f362a52d8628209636b` |
| eviepoch/api/audit.py | `0ab790b66f814f9bc54eb1c41c250a92db780ba40fe6d5f730cdcdd42aaaf916` |
| eviepoch/domain/requests.py | `5cd3d7f0335615a54b6c0192e29515a1feddbcfc7d2ccf3478d888c94a8c04be` |
| eviepoch/domain/models.py | `280d57119a1c1e76453a31b76769a0b52f73d9c7e3f83d27809fb8b8cc0c2be6` |
| eviepoch/pipeline.py | `f1332d86f837d90ddd5784f3b5d16d5af4cd49c2e693f7241de8299dded3e75d` |
| eviepoch/providers/client.py | `43f94c30e15176e110a899a40f55eb1397dfa160cd86aedf3174399ee3d375cd` |
| eviepoch/retrieval/engine.py | `38231830e870cb674e8382513db312c5573c9f51159d5c2cf6b97260713d4c54` |
| eviepoch/retrieval/selection.py | `4efc8de7d9f3eb04075085796faa31495f0b7578a6561e2de3fa71fe30a751d7` |
| eviepoch/retrieval/evidence.py | `ff8409ef1c8cbc63a52969186be44fe603efdf3f400d329dcd06b76ab787f2ff` |
| eviepoch/storage/schema.py | `b8182576311e1552942390016a002a585f3f86d613058f9dc61ad8462ad41162` |
| eviepoch/governance/lifecycle.py | `1603149c7d8d4e5ab8973e0a28caeecb9cb7025fceb9d816f8577be34dc23bbd` |

[cm-root]: https://github.com/thedotmack/claude-mem/tree/06b997e3d03e68792e964e3b81c259c00bceb0dc
[cm-hook]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/cli/handlers/observation.ts#L16
[cm-session]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/worker/http/routes/SessionRoutes.ts#L507
[cm-manager]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/worker/SessionManager.ts#L206
[cm-prompts]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/sdk/prompts.ts#L91
[cm-parser]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/sdk/parser.ts#L33
[cm-response]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/worker/agents/ResponseProcessor.ts#L48
[cm-chroma]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/sync/ChromaSync.ts#L123
[cm-search]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/worker/search/SearchOrchestrator.ts#L81
[cm-sqlite]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/sqlite/SessionSearch.ts#L41
[cm-strategy]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/services/worker/search/strategies/ChromaSearchStrategy.ts#L93
[cm-mcp]: https://github.com/thedotmack/claude-mem/blob/06b997e3d03e68792e964e3b81c259c00bceb0dc/src/servers/mcp-server.ts#L250
[atm-root]: https://github.com/JingbiaoMei/ATM-Bench/tree/ef4e5dff1a47ec71213a06e359f02753defa8fb1
[causal-root]: https://github.com/JingxuanC/causal-memory/tree/39053ff6fd7a18771d59f0c10240b19c37f412b6
[gallery-root]: https://github.com/YuanchenBei/Mem-Gallery/tree/a93959e1e978a6a7d77798ae92c2ffe41c538c62
[mg-run]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/run/run_bench.py
[mg-memory]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/memory/MMMemory.py
[mg-store]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/operation/Store.py#L228
[mg-recall]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/operation/Recall.py#L428
[mg-encoder]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/function/MultiModalEncoder.py
[mg-retrieval]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/function/MultiModalRetrieval.py
[mg-eval]: https://github.com/YuanchenBei/Mem-Gallery/blob/a93959e1e978a6a7d77798ae92c2ffe41c538c62/benchmark/memengine/evaluate/evaluation.py#L230
