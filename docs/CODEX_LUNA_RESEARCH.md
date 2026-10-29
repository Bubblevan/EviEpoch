# 使用 Codex CLI 的 GPT-6 Luna 做研究验证

本机已验证 `gpt-6-luna` 可以通过现有 Codex CLI 的 ChatGPT 登录调用。研究侧的抽取、规划、取证、披露、代理 Answer 和 judge 可以使用这个后端；正式服务的 HTTP provider 与参赛模型校验保持原样。

## 运行方式

从项目根目录运行两个针对性合成控制，设置明确的 CLI 调用上限：

```powershell
.\.venv\Scripts\python.exe experiments/evaluate.py --codex-luna --answer --require-pass --arms full --dataset experiments/datasets/answer_replay_controls.json --cases experience-data privacy-minimal --codex-max-calls 24 --capture-io --output logs/codex-luna-focused.json
```

启动仅监听本机的公开研究工作台：

```powershell
.\.venv\Scripts\python.exe experiments/start_research.py --codex-luna --codex-max-calls 32 --port 8013
```

工作台是原有的无认证公开样例入口。私有回放继续使用本机忽略目录中的专用 Bearer 服务，不把私有内容放进公开工作台。私有 replay/judge 辅助脚本已改为默认 `codex-luna`、一个 worker、每个进程最多 32 次实际 CLI 启动；完整批次必须明确配置足够的调用上限。

`--deepseek` 和 `--codex-luna` 互斥。未显式选择研究后端时仍保留原配置语义，不会偷偷替换正式模型。

## 成本控制与边界

- 使用 `gpt-6-luna`、`low` reasoning。每次调用是独立的 ephemeral CLI 会话。
- 精确缓存按模型、reasoning、适配器版本、system/user 输入、阶段和输出预算指纹区分。同目录的缓存未命中在进程间串行化。
- 默认每个运行进程最多启动 32 次 CLI；缓存命中不消耗新的模型调用。失败、超时也消耗该启动预算，不自动重试或切回付费 HTTP 模型。
- CLI 的近似输出长度由提示约束；这里没有伪装成 API `max_tokens` 的硬上限。调用次数、串行执行和超时也不等于金额上限。
- 子进程不继承 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 等密钥，避免 dotenv 中的 API key 改变用户要求的 CLI 登录路径。不复制或读取 CLI 的登录密钥文件。
- 忽略用户配置、禁用工具/插件/浏览/委派/钩子，设置只读沙箱。意外工具事件或非 JSON 结果会拒绝，数据仍需通过 EviEpoch 原有的来源、治理与预算校验。
- 提示、模型事件和响应缓存只写到 Git 忽略的 `logs/codex-cli-cache`。缓存重放用于工程复核，不算新的独立模型质量样本。

Codex CLI 会占用账户自己的额度，不能承诺免费或无限。Embedding 仍使用独立配置的 `text-embedding-v4` 服务；私有回放优先复用已有 Add 抽取与向量，减少重复成本。新旧 provider 的实验必须分别记录，不能把 Luna 的代理评审结果当作原模型或官方得分。

## 验证口径

小型 CLI JSON 探针、两个真实模型合成控制、15 份请求的零新增推理缓存复核，以及适配器的预算/鉴权/失败边界测试均有记录。离线 SQLite/PostgreSQL 共 280 项通过。一个私有数值问题的定点顺序回放复用了 7 次 Add，Search 真实调用 Luna 5 次并返回 200；10 条输出证据均通过原文字节、用户与记忆代次核验。Add 复用的是此前 DeepSeek 抽取与已存向量，不能称为一次新的 Luna 全链路 Add 可靠性实验。

脱敏摘要见 [验证报告](../experiments/reports/codex-luna-validation.json)。两个合成问题的答案与隐私检查均通过，但证据充分性仍记为未知；缓存复核也不是第二次独立质量测量。完整回放与官方 100% 尚未由这些小样本证明。

实现依据是 [OpenAI 的 Codex 非交互调用文档](https://learn.chatgpt.com/docs/non-interactive-mode)与[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)，并以本机 CLI 帮助、模型缓存和实际调用验证可用性。该适配器使用 CLI 已有登录，不要求提供新的 OpenAI API key。
