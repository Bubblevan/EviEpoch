# EviEpoch — AML submission draft

Prepared: 2026-10-28 (Asia/Shanghai)

## Form fields

- **System name:** EviEpoch
- **Version name:** v1.0 (deployed source tree commit `55d14b147408638c3b61265c72a0a309f53115b8`; API metadata version `0.2.0`)
- **Add API URL:** https://bubblevan.de5.net/v1/memories/add
- **Search API URL:** https://bubblevan.de5.net/v1/memories/search
- **Health URL:** https://bubblevan.de5.net/v1/memories/health
- **Authentication:** Authorization: Bearer
- **Memory System Key:** use the existing `EPOCH_AUTH_TOKEN` value from `D:\MyLab\EviEpoch\.env`; never put its value in this draft or Git.
- **Public repository URL:** local Git origin is `https://github.com/Bubblevan/EviEpoch`; public visibility and the deployed commit are not verified. Confirm public access and push the current source before submitting this required field.
- **Applicant:** Bao Bowen (包博文), individual participant

## Submission notes / run instructions

EviEpoch provides HTTPS Add and evidence-only Search endpoints. Add stores the original conversation turns and timestamps in persistent SQLite, then completes its configured synchronous memory processing before returning success. Repeated `request_id` writes are idempotent within a user namespace. Search ranks and returns source evidence scoped to `user_id`; it does not generate the final answer.

The current deployment uses DeepSeek `deepseek-flash` for language-model operations and Alibaba Cloud `text-embedding-v4` with 1024 dimensions. Authenticated requests use a bearer key. The configured service limits are 16 concurrent Add requests, 16 concurrent Search requests, and 36 total in-flight HTTP requests. The app process is limited to 150% CPU and 700 MiB RAM. SQLite persists at `/var/lib/eviepoch/eviepoch-v1.sqlite3`.

Authenticated Add/Search request and response bodies are retained as private JSONL audit records for analysis, capped at 1 MiB per body and 8 GiB total with 30-day retention. Authorization headers are not recorded. The operator exports the records over SSH using `deploy/export-audit.ps1`.

## Method and provenance disclosure

EviEpoch preserves source messages and returns source-backed evidence. The deployed revision is identified above. Upstream provenance: ActiveMemoryIndex (AMI), `academic-v4-20261016-rc6`, upstream commit `2754a7880a169a7dd60cfc7f8d279454885f52a9`; original author Xuhao Lin. The upstream MIT license and attribution are retained in the repository. Upstream functionality is credited to its original authors.

The current public interface has passed a provider-backed synthetic Add, restart, and Search verification. The public GitHub repository field must be verified before submission. No official Smoke or Full run has been started by the operator in this deployment session.
