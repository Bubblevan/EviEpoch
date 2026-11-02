# GPT-6 Luna reverse-tunnel runtime

The EviEpoch API remains on the `.com` ECS. Only its internal structured LLM
calls travel through an SSH reverse tunnel to the logged-in Codex CLI on the
Windows laptop. The model gateway binds to laptop loopback; ECS's listener is
bound to its Docker bridge, and the SSH reverse listener is bound to ECS
loopback. No model-proxy port is published to the Internet.

```text
AML evaluator
  -> HTTPS /v1/memories/add or /search on ECS
  -> EviEpoch container (172.19.0.2)
  -> Docker bridge relay (172.19.0.1:18013)
  -> ECS loopback reverse-SSH port (127.0.0.1:18012)
  -> laptop loopback gateway (127.0.0.1:18123)
  -> Codex CLI, gpt-6-luna
```

## Bounds and logs

- The laptop gateway accepts a separate Bearer token and binds only to
  `127.0.0.1:18123`.
- One Codex CLI inference runs at a time. Up to 32 total requests may be queued;
  excess work is rejected with `429` and `Retry-After: 5`.
- The process allows at most 256 actual Codex CLI launches. Exact cache hits do
  not consume that launch budget. Restarting the gateway starts a new
  per-process budget; the private exact cache remains in `logs/codex-cli-cache`.
- A CLI launch has a 180-second timeout by default. EviEpoch's HTTP timeout is
  300 seconds and its extraction deadline is 900 seconds to allow for the
  serialized queue. These values may make a crowded Smoke slower.
- The CLI cache retains prompts, model events, outputs, and usage metadata under
  Git-ignored `logs/codex-cli-cache`. Gateway logs contain only operational
  metadata and rotate at 2 MB with three backups. The ECS TCP relay logs no
  request content.
- Embeddings remain on the configured `text-embedding-v4` provider. This tunnel
  carries only the LLM's structured Chat Completions used by Add/Search.
- The laptop must remain powered on, awake, online, and signed in to Codex CLI
  during an evaluation. The watcher retries SSH every five seconds after a
  disconnect; it does not keep Windows awake.

On the ECS, the TCP bridge runs as `nobody` with a 32 MiB memory cap and a
10% CPU quota. UFW permits its port only from the EviEpoch Docker bridge
subnet on the bridge interface. The bridge binds to the Docker host-side
private address; the SSH reverse listener itself remains on ECS loopback.

## Start and stop on Windows

From the project root:

```powershell
.\deploy\start-codex-relay.ps1
.\deploy\stop-codex-relay.ps1
```

The dedicated tunnel key and gateway token are stored in Git-ignored `logs/`.
The SSH host identity uses the previously pinned `.com` host key in the
temporary `eviepoch-com-pinned-known-hosts` file. The existing `ami-tunnel`
ECS account is restricted to one loopback reverse-forward port and cannot open
an interactive shell.

## ECS Compose override and rollback

`deploy/compose.codex-tunnel.yaml` selects `gpt-6-luna` and points the EviEpoch
container at the private Docker-bridge relay. It leaves the DeepSeek release
configuration intact underneath so rollback is a Compose re-create without
this override. To restore the prior DeepSeek configuration, stop the laptop
relay, disable `eviepoch-codex-bridge.service`, and recreate the service using
the original three Compose files, omitting `compose.codex-tunnel.yaml`.

The API endpoint remains live while the laptop is offline, but LLM-dependent
operations will fail; health reports process liveness and does not guarantee
the laptop tunnel is ready.
