# Multimodal Memory：原图与结构化观察共同取证

**版本：** 0.1 实施合同。写法对齐 [技术设计 §19](TECHNICAL_DESIGN.md#19-coding-memory按版本复用工程经验)

**日期：** 2026-11-03，Asia/Shanghai

**上级合同：** [技术设计 §20](TECHNICAL_DESIGN.md#20-multimodal-memory原始图像与结构化观察共同取证)、[§22.3](TECHNICAL_DESIGN.md#223-multimodal-评测)、[§22.4](TECHNICAL_DESIGN.md#224-接口与状态验收矩阵)

**原型边界：** [原型设计 §13.3 的 M0–M3](EXTENSION_PROTOTYPE_INTENT.md#133-支撑主张的消融)

**代码起点：** `eviepoch/adapters/multimodal/`、`eviepoch/storage/multimodal_schema.py`

本文是多模态赛道的实施合同，对应 Coding 使用的 §19。技术设计 §20 仍是对象和协议的完整目标。两边冲突时，以 §20 的不变量为准。本文把这些不变量收成可以逐项实现和验收的条文。

默认 `task_adapter=textual`。文本赛道的 Add/Search 继续返回字符串，不接受图文数组。多模态实验使用 `task_adapter=multimodal`。不启动收费 Full，不改已经绑定的正式参评版本。榜单百分比和论文结论不是本仓库的测试成绩。MemEye、ATM-Bench、Mem-Gallery 的图像和对话数据目前没有随参考源码下载；数据版本钉住之前，不把一次合成图片往返写成评测结果。

当前代码已经能做这些事：校验有序 ContentPart，把 Data URI 解码后按内容哈希写入 namespace 下的媒体目录，写入 `ee_media_assets` 与 `ee_media_occurrences`，用带 `origin` 的观察字段做文本索引，并在 Search 里把原图重新编成 Data URI 放回证据。区域、视觉向量、typed visual proof、遮盖和评测运行器还没有按下面的合同闭合。

## 1. 目标、参考实现与输入边界

目标是让图文记忆在检索时交出可核对的原图，而不是只交出 Caption。颜色、位置、布局、图中文字和跨时间的外观变化，必须能回到当时那一张图。Caption、OCR 和视觉模型观察是带来源的派生字段。它们可以帮着找到图，不能代替图本身完成视觉义务。

参考实现只迁移机制，不迁完整运行时：

| 参考 | 迁入的机制 | 不迁入的部分 |
|---|---|---|
| ATM-Bench，commit `ef4e5dff1a47ec71213a06e359f02753defa8fb1` | 图像与文本分视图索引；原图参与检索和回答 | 其 RetrievalItem 没有 part 序号、区域来源和 generation |
| Mem-Gallery，commit `a93959e1e978a6a7d77798ae92c2ffe41c538c62` | 原图与 Caption 对照；跨会话问答 | 先写完全部对话再提问的脚本，不等于流式 Add |
| MemEye | 原型的第一个独立基准。EviEpoch 作为一个新的 memory method 接入 | 不改写它已有的 Caption-only、多模态 RAG、SimpleMem、M3、MIRIX 入口 |
| MIRIX | 视觉观察、分层存储、原图保留 | 六 Agent 编排和完整桌面环境 |

两种输入共享同一套媒体模型：

- **AML Add / Search。** `messages[].content`、`query` 和 `data[].content` 使用非空有序数组。元素只有 `TextPart` 与 `ImagePart`。图片是 inline Base64 Data URI。远程 URL 不接受。支持 JPEG、PNG、WebP。单张解码后不超过 10 MiB，一次 Add 的图像合计不超过 30 MiB，一次 Search 响应的图像合计不超过 30 MiB。Query 的 30 MiB 合计是本方部署上限，不写成官网额外规定。
- **文本适配器收到图文数组。** 返回明确错误。不把数组的 JSON 文本当作普通记忆正文入库。
- **查询图像。** 只服务这一次 Search。不写入长期记忆，不生成 MediaOccurrence。

```text
TextPart  = {type: "text", text: nonempty_string}
ImagePart = {type: "image_url", image_url: {url: base64_data_uri}}
Content   = nonempty_string | nonempty_ordered_list[TextPart | ImagePart]
```

输入允许只有图、只有字，以及图文交错。每个 text part 按自己的字节保留。相邻的文字和图片不自动表示同一事实。`role`、消息序号、part 序号和原始 timestamp 一起构成来源定位。

公开接口的媒体范围是图片。视频和音频不新增 part 类型。离线视频实验若使用 ATM 的帧，必须单独记录采样协议，以及视频到帧的 locator。

## 2. 图文数据模型

| 对象 | 必须保存的字段与语义 |
|---|---|
| SourceContentPart | source_id、part_ordinal、type、原始 text 或 asset 引用、part_hash。按序号能重建 Add 时的顺序 |
| MediaAsset | namespace、asset_id、原始字节 hash、真实 MIME、decoded_bytes、宽高、存储 locator、持久化状态。asset_id 由 namespace 与内容哈希决定 |
| MediaOccurrence | asset 出现在哪条消息的哪个 part、角色、消息时间。同一字节在不同消息里各留一条 occurrence，不合并情境 |
| PartTextSpan | source_id、part_ordinal、该 text part 内的 UTF-8 byte_start/end、text_hash。偏移不能跨 part 计算 |
| VisualObservation | asset、occurrence、schema_version、字段值、origin、模型与 prompt 身份、置信度、是否对照过原图 |
| OCRSpan | asset、识别出的文本、语言、bbox 或 polygon（可以未知）、OCR 模型与置信度。它是识别结果，不是用户原文 |
| VisualRegion | 父 asset、像素坐标与归一化坐标、朝向或变换、裁剪字节哈希、生成方法、所属 occurrence。没有可定位框时回链整图，不编造框 |
| VisualEntityMention | 实体候选、视觉属性、region 或 asset、跨会话同一性的支持与分歧。外观相似只建立候选，不确认是同一件物品 |
| MediaRelation | 图、文、区域、实体、事件之间的关系，带来源和推导类型 |
| Representation | 所属对象、view_kind、模型与修订、维度、预处理和 schema 版本、namespace、generation |
| EvidenceAtom | text_span、raw_image、region、OCR 或 observation。记录出处、它支持的义务，以及是否允许披露 |

观察字段至少分开保存：场景描述、OCR、对象或实体、颜色与空间关系、标签。每个字段的 `origin` 只能是 `supplied_text`、`supplied_caption`、`exif`、`OCR`、`VLM_observation`、`inferred_relation` 之一。用户写的说明、EXIF 和模型观察互相冲突时各留各的，不先拼成一条“这张图的事实”。

文本 span 能证明引用的字在来源里。原图哈希能证明返回的是哪一张图。两者都不能单独证明 Caption 或 OCR 在语义上正确。视觉支持记录要写明有没有看过原图或区域，结论是支持、矛盾还是不确定，以及用的哪个模型。

`captured_at`、来源声称的事件时间、消息时间和 `stored_at` 分开存放。图里印着的日期、EXIF 时间和这张图被发出的时间可以不同。截图里的旧聊天日期保持为图中内容，不用新消息的写入时间替换。

相同内容哈希在同一个 namespace 里共用一个 MediaAsset。每次出现仍是独立的 MediaOccurrence。另一个 namespace 即使字节相同，也使用自己的 asset 行和自己的授权。

## 3. 写入链路

1. 校验 part 顺序、Data URI、声明的 MIME 和实际解码结果。按解码后的字节累计 10 MiB 与 30 MiB。在读完整缓冲之前先做长度预检。Base64 字符串不进入普通文本切块、BM25 或文本 embedding。
2. 原始字节写入该 namespace 的媒体目录或对象存储，核对哈希和长度，建立尚未对 Search 可见的 staged asset。文本 part 和 occurrence 与这次发布放在同一 generation。
3. 已有的用户文字按 `supplied_text` 或 `supplied_caption` 保存。允许的视觉模型再写场景、OCR、实体、颜色和空间关系。分辨率、旋转和模型身份写入 Representation。模型不可用时，观察字段留空并标明未抽取。完整多模态配置不能在视觉处理失败后，无标记地按 caption-only 宣布写入成功。caption-only 是单独配置、单独记录的实验臂。
4. 需要区分文字、图表、物体或空间关系时，再生成区域和局部表示。原图仍独立保留。重复图片或相似缩略图不删除其他 occurrence。
5. Caption、OCR、实体属性和用户原文分别建 postings。视觉向量只在对应实验臂开启时写入，并且使用自己的模型空间。缺失、不确定和冲突各自编码，不用空字符串冒充“已核对”。
6. 图文事件可以形成导航摘要。摘要依赖原始 part 和 asset。较新的观察可以挑战旧属性，不能覆盖或替换原图字节。
7. 沿用 prepare/publish。校验 generation 之后，来源、asset、occurrence、观察、必需表示、依赖和幂等账本一次提交。提交成功后，这个 generation 上声明过的媒体都要能按 locator 读回。
8. Add 的成功响应仍是现行回显字段，不在成功体里返回图片。对象存储和数据库没有同一个 ACID 事务：字节先落盘，数据库引用后发布。发布失败的 staged 对象按 request 租约回收。同一 request 的重试复用已核对的字节，不产生第二份逻辑来源。

官方 Add 等到本配置声明的必需产物发布后才返回成功。耗时的视觉计算可以在有界 worker 里并行，但不能先让“只有 Caption 的一代”被 Search 看见，再补原图。

## 4. 存储与表示

权威来源和图仍在现有 PostgreSQL 表族。多模态表是 schema v6：

- `ee_media_assets`：namespace、asset_id、内容哈希、MIME、字节长度、宽高、存储路径、generation、payload。
- `ee_media_occurrences`：occurrence、asset、source、part 序号、角色、generation。

原图和派生裁剪放在媒体目录或对象存储。数据库保存哈希、长度和 locator。locator 是内部读取位置，不作为官方响应里的 `image_url`。

索引按视图分开：

| 存储 | 放什么 |
|---|---|
| 来源与图 | 消息、part、occurrence、观察、区域、实体、治理依赖 |
| 精确 / 稀疏 | 用户原文、OCR、实体名、数字、日期、标签、空间属性。各用自己的 postings |
| 文本向量 | Caption、OCR、观察字段、对话片段、事件导航。沿用赛事允许的文本 embedding |
| 视觉向量 | 原图、区域、图文联合视图。研究配置单独登记模型、维度和预处理。图片字节不写入向量列 |
| 关系 | part → occurrence → region 或 observation → entity 或 event。跨会话关联带同一性证据和有效时间 |

ATM 的 dual-concat 是要保留的复现配置：图像向量和文本向量各自归一化后再拼接。EviEpoch 默认把各视图留成独立召回臂，再用 RRF 合并，以便分别报告 OCR、视觉和时间的贡献。不同维度或不同模型的原始分数不直接相加。

## 5. Search：图文义务、原图取证与证据组

Query 进入只存在于这一次请求的 QueryBundle：保留 part 顺序和查询图像的哈希。规划器读问题原文和查询图像，分开目标实体、所问的视觉属性、时间范围、比较关系，以及是否必须看原图。查询图像和临时说明不进 Add 表。

图像查询的编码必须吃到图像字节。把查询图像编成空字符串，或只编码旁边的 Caption，都不算图像检索。

| 通道 | 召回内容 |
|---|---|
| 文本 Dense / BM25 | 原问题及义务子查询，匹配对话、Caption、观察和 OCR |
| OCR / 元数据 | 编号、招牌、数字、地点、日期和属性约束 |
| Visual | 同一视觉模型空间里，用文本或查询图像检索原图和区域。该臂默认关闭，到视觉向量实验才打开 |
| Entity / Relation | 实体候选、跨会话关联、图文指代 |
| Temporal | 事件时间、图中时间、消息时间、属性更新和前后状态 |
| Episode | 命中图片附近的对话，包括指代、否定和后来的纠正 |

候选按 source 和 occurrence 聚合。同一张图的 Caption、OCR 和原图是互补视图，不去掉来源身份。重排只给已有候选打分，并指出支持区域。它不写出新的用户事实，也不把分数写回观察的真值。

颜色、位置、布局、图中文字或细小变化一旦成为问题的一部分，相关原图或区域就是视觉义务。Caption 的文本分再高，也不能把这个义务标成完成。

变化类问题的证据组至少是：

```text
H = {entity_hypothesis, time_hypothesis, unresolved_identity_conflicts}
S = {image_at_t1, time_support_t1, image_at_t2, time_support_t2,
     entity_correspondence, attribute_comparison}
admissible(H, S) = both_images_disclosable
                   AND each_image_has_its_own_time_support
                   AND entity_correspondence_is_supported
                   AND no_unresolved_identity_conflict_is_silently_collapsed
```

控制器顺序是 `search_index → hydrate_original → compare_visual_support → find_counterevidence → select`。缺原图、缺时间或身份仍有分歧时，返回已经支持的部分，并在 trace 里留下缺口。不把一张“之后”的图宣布成前后比较已经完成。

## 6. 输出与验证责任

多模态的 `data[].content` 是有序图文数组。一个证据包把来源文字和对应原图相邻放置。多个来源合成的包使用由 atom、顺序和表示版本派生的 bundle id，不冒充某一个 Source id。Coding 和文本响应仍是字符串。

```text
content = [
  source_label_and_exact_text,
  original_image_data_uri,
  next_source_label_and_exact_text,
  next_original_image_data_uri
]
```

官方 `image_url` 只携带实际 Base64 Data URI。内部 asset id、磁盘路径和“请再下载”的占位不能代替图片。返回的原图字节必须对得上 MediaAsset 的哈希。派生裁剪若出现，要写明父图和变换，并且不能被标成完整原图。

预算在最终要发出的内容上计算：

| 约束 | 处理 |
|---|---|
| `len(data) <= top_k` | 一个证据包算一条。不靠多拆条目绕过 top_k |
| 单图 ≤ 10 MiB，响应图像合计 ≤ 30 MiB | 按解码字节计。同一字节多次出现在响应里就多次计入 |
| HTTP 正文 | 计入 Base64 约 4/3 的膨胀和 JSON 开销 |
| Answer 输入 | 117760 是官网给出的输入 token 上限。图像 token 按实际回答模型估计，不把这个上限先用完文字再免费加图片 |
| 完整性 | 必要的原图、时间证据和例外放进同一个装得下的包。装不下就少返回，并记下缺口 |
| 装配 | 不在 Base64 中间截断。不返回无法解码的图片 |

EviEpoch 负责取出、核对和装配证据。最终回答由评测里的 Answer 模型完成。检索把原图放进上下文，不等于回答已经看对了颜色或时间。新的视觉结论只有经后续 Add 进入记忆。

删除从 source、part、asset、region 的授权求传递闭包，覆盖 OCR、Caption、观察、向量、postings、图边、摘要、裁剪、缓存和尚未发布的任务。先让在线读取不可见，再清理字节。同一字节被多个 namespace 引用时，只解除发起者的授权。一个用户的删除不扩散到其他用户。图里同时有可披露和不可披露区域时，未完成可追溯遮盖之前不返回这张图；其他有支持的证据仍可返回。

日志记录 part 数量、字节、尺寸、阶段和耗时。正式运行不把 Base64、OCR 正文或截图写入普通日志。

## 7. 评测

原型按一次只多一种机制比较。技术设计 §22.3 的 M0–M4 更细；报告里写明编号对应关系。

| 臂 | 相对前一臂只多出的机制 | 要回答的问题 |
|---|---|---|
| M0 | Caption 与观察文本的 Dense / BM25 | 只用文字会丢掉哪些视觉细节 |
| M1 | 证据包返回原图 | 原图是否改变可见细节的核对 |
| M2 | 视觉向量独立召回 | 跨模态候选是否比纯文本更完整 |
| M3 | 实体、时间和证据组 | 变化题和多证据题是否同时拿齐 |
| M4 | 多模态重排 | 同候选下排序是否提高证据组完整性 |

M2 及之后的视觉 embedding 属于研究配置，和 competition 文本模型的结果分表。M0 与 M1 是第一条必须能跑的对照。

正式计分按数据集各自的协议，不合成一个混合总分：

- ATM-Bench：原协议的 QA / judge，以及 source-level R@K。另报原图召回、视觉支持、证据组完整率、Query 图像使用率。
- Mem-Gallery：答案 F1、检索 Precision / Recall / HitRate。其 runner 先写完对话再提问；流式实验另记每题可见的前缀 generation。`point` 标签与 AML 的 7/4/3 分组分开保存。
- MemEye：原型基准。固定数据版本、题目划分和方法配置之后再接入。MobileMem 等 MemEye 的四组结果留档后再开。

工程验收按用户给出的 7/4/3 分组编号，不把它说成官网的六类能力名：

| 组 | 编号 |
|---|---|
| 记忆认知，7 项 | M-A1 文本找图；M-A2 图像找文字；M-A3 图像找图像；M-A4 OCR 与数字；M-A5 颜色、位置、布局；M-A6 跨会话多跳；M-A7 组合问题的证据组完整性 |
| 记忆演化，4 项 | M-B1 多时间与顺序；M-B2 属性变化与旧状态；M-B3 纠正、冲突和同一性；M-B4 增量 Add 可见且不用未来会话 |
| 记忆对齐，3 项 | M-C1 有情景依据的偏好；M-C2 证据缺失或矛盾时的不确定；M-C3 文图来源与披露 |

数据集的原始 category 或 point 如何映射到这些编号，写进数据 manifest。gold 答案、gold 证据 id 和未来会话不作为检索特征。

小型合成样例只验证故障模式：有序 text-image-text、多张图、只有图的 Query、无 Caption、JPEG/PNG/WebP、10/30 MiB 边界、错误 Base64、文字与图像矛盾、Caption 漏掉颜色或位置、同一张图的多个来源、相似物品被误并。它们不替代端到端评测。

## 8. 实施顺序

与 Coding 的八步一样，每一步只多证明一种机制。前一步的失败结果保留。文本适配器保持默认行为。

| 顺序 | 子任务 | 闭合的合同 | 做完时能运行的检查 |
|---|---|---|---|
| 1 | 锁定基线与图像故障夹具 | §1、§7 的数据边界，§22.4 的多模态故障 | ATM、Mem-Gallery、MemEye 的 commit 与数据版本可核对；夹具覆盖顺序、MIME、大小、坏数据和矛盾图文；不下载未钉住的数据集，不写入榜单分数 |
| 2 | 有序图文成为一条来源 | §1、§3 的前两步 | 图文数组保持 part 顺序；文本适配器拒绝该数组；Query 图像不入库；只有图的消息能成为来源 |
| 3 | 原图身份与出现位置 | §2 的 Asset / Occurrence，§3 的时间分离 | 相同字节共用 asset、分 occurrence；另一 namespace 不共用授权；图中日期、EXIF、消息时间和 stored_at 可分别读出 |
| 4 | 观察字段与来源标记 | §2 的 origin，§3 第 3 步 | 用户原文、OCR 和模型观察分字段保存；冲突不被收成一条 Caption；模型缺失时字段为空而不是编造 |
| 5 | 视觉义务检索 | §5 的通道与义务 | 问颜色、位置或图中文字时，只有 Caption 命中不算完成；M0 不返回原图，M1 返回且哈希一致 |
| 6 | 图文证据组与预算 | §5 的 admissible，§6 的预算 | 变化题缺一张图或缺少时间时留下缺口；响应按解码字节遵守 10/30 MiB；不在 Base64 中间截断 |
| 7 | 来源删除闭包 | §6 的删除段，技术设计 §10 与 §20.8 | 删除后 Caption、OCR、观察、裁剪和原图都不能被 Search 读回；其他 namespace 的同一字节仍在 |
| 8 | M0–M4 端到端 | §7 | 在已钉住并实际取得的数据上报告答案、原图召回、证据组完整率、成本和负向结果；合成夹具不替代该报告 |

第 1 步开始前不运行评测服务，也不把参考仓库 README 里的论文数字抄进本仓库的结果文件。
