# Third-party notices

EviEpoch includes license and notice texts under `third_party/licenses/` for upstream and bundled components. These notices are part of the source distribution; consult the corresponding terms before redistribution.

- ActiveMemoryIndex (`academic-v4-20261016-rc6`): `ACTIVEMEMORYINDEX-MIT.txt` (MIT; copyright © 2026 Xuhao Lin). The upstream tag and commit are recorded in `SUBMISSION_DRAFT.md`. AMI work is credited to Xuhao Lin.
- Cognee: `COGNEE-APACHE-2.0.txt` and `COGNEE-NOTICE.txt` (Apache-2.0; notice identifies Topoteretes UG and cognee.ai).
- MemOS: `MEMOS-APACHE-2.0.txt` (Apache-2.0).
- Hindsight: `HINDSIGHT-MIT.txt` (MIT; copyright notice identifies Vectorize AI, Inc.).
- claude-mem v15.8.3 (`9603d9bd7ba2bf1ed53595cf7eaa7e7b6a8013e4`, Alex Newman): `CLAUDE-MEM-APACHE-2.0.txt` (Apache-2.0). Vendored behavior files live under `eviepoch/adapters/coding/reference/claude_mem/` and are pinned in `experiments/coding/baselines/coding_baseline_lock.json`. The older v12.1.1 tag used AGPL-3.0; that license does not apply to these v15.8.3 copies.

These bundled notice files do not by themselves enumerate every transitive Python package. The Python dependency list is maintained separately in `requirements.txt`.
