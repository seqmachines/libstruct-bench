#!/usr/bin/env bash
set -euo pipefail

manifest=/workspace/workspace_manifest.json
auth_source=/run/secrets/libgen-codex-auth.json

if [[ ! -f "$auth_source" || -L "$auth_source" ]]; then
  echo "isolated Codex auth.json is missing or is not a regular file" >&2
  exit 2
fi

if [[ "${CODEX_FORCE_AUTH_JSON:-}" != "1" ]]; then
  echo "isolated Codex requires CODEX_FORCE_AUTH_JSON=1" >&2
  exit 2
fi

if [[ "$(jq -er '.host_paths_exposed' "$manifest")" != "false" ]] \
  || [[ "$(jq -er '.network_policy' "$manifest")" != "provider_api_only_no_web" ]]; then
  echo "workspace does not declare the isolated worker contract" >&2
  exit 2
fi

mode=$(jq -er '.mode' "$manifest")
if [[ "$mode" == "human_review_console" ]]; then
  echo "the interactive human console is not an agent worker" >&2
  exit 2
fi

prompt_relative=$(jq -er '.agent_contract.prompt_path' "$manifest")
schema_relative=$(jq -er '.agent_contract.output_schema_path' "$manifest")
draft_relative=$(jq -er '.agent_contract.draft_output_path' "$manifest")
events_relative=$(jq -er '.agent_contract.event_log_path' "$manifest")

mapfile -t resolved_contract < <(python3 - \
  "$prompt_relative" "$schema_relative" "$draft_relative" "$events_relative" <<'PY'
import sys
from pathlib import Path

root = Path("/workspace")
for value in sys.argv[1:]:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"unsafe workspace contract path: {value}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise SystemExit(f"workspace contract path escapes /workspace: {value}")
    print(resolved)
PY
)

if [[ ${#resolved_contract[@]} -ne 4 ]]; then
  echo "workspace agent contract is incomplete" >&2
  exit 2
fi

prompt=${resolved_contract[0]}
schema=${resolved_contract[1]}
draft=${resolved_contract[2]}
events=${resolved_contract[3]}

if [[ ! -f "$prompt" || -L "$prompt" || ! -f "$schema" || -L "$schema" ]]; then
  echo "workspace prompt or output schema is missing" >&2
  exit 2
fi

if [[ -e "$draft" && ( ! -f "$draft" || -L "$draft" ) ]]; then
  echo "workspace draft target is not a regular file" >&2
  exit 2
fi

mkdir -p "$(dirname "$draft")" "$(dirname "$events")"
mkdir -p "$HOME"
ln -s "$auth_source" "$CODEX_HOME/auth.json"

model=${LIBGEN_CODEX_MODEL:?missing experiment-pinned model}
reasoning_effort=${LIBGEN_CODEX_REASONING_EFFORT:?missing experiment-pinned reasoning effort}
expected_version=${LIBGEN_CODEX_VERSION:?missing experiment-pinned Codex version}

case "$reasoning_effort" in
  low|medium|high|xhigh|max|ultra) ;;
  *) echo "invalid experiment-pinned reasoning effort: $reasoning_effort" >&2; exit 2 ;;
esac

observed_version=$(codex --version | awk '{print $2}')
if [[ "$observed_version" != "$expected_version" ]]; then
  echo "experiment requires Codex $expected_version; worker has $observed_version" >&2
  exit 2
fi

exec codex exec \
  --model "$model" \
  --config "model_reasoning_effort=\"$reasoning_effort\"" \
  --dangerously-bypass-approvals-and-sandbox \
  --ephemeral \
  --ignore-user-config \
  --skip-git-repo-check \
  --output-schema "$schema" \
  --json \
  --output-last-message "$draft" \
  --cd /workspace \
  - < "$prompt"
