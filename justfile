set shell := ["bash", "-cu"]

uv := env_var_or_default("UV", "uv")
openspec := env_var_or_default("OPENSPEC", "openspec")
openspec_min := "1.13.1"
hermes_agent_repo := env_var_or_default("HERMES_AGENT_REPO", "https://github.com/NousResearch/hermes-agent.git")
hermes_pinned_dir := env_var_or_default("HERMES_PINNED_DIR", ".hermes-agent-pinned")
hermes_upstream_dir := env_var_or_default("HERMES_UPSTREAM_DIR", ".hermes-agent-upstream")

# Show available recipes.
default:
    @just --list

# Create or sync the uv-managed environment.
install:
    {{ uv }} sync

# Run the full unittest suite.
test:
    {{ uv }} run python -m unittest discover -s tests

# Run one unittest by dotted test name.
test-one test_name:
    {{ uv }} run python -m unittest {{ test_name }}

# Run deterministic release intent, identity, and workflow contracts.
test-release:
    {{ uv }} run python -m unittest tests.test_release_contract -v

# Attach an immutable CI trigger to the branch semantic-release matches.
[private]
release-attach-trigger:
    #!/usr/bin/env bash
    set -euo pipefail
    test "$GITHUB_REF" = "refs/heads/main"
    test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
    git switch --force-create main "$EXPECTED_SHA"
    test "$(git branch --show-current)" = "main"
    test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"

# Check out hermes-agent at a given ref, in a directory of its own.
#
# Each ref gets its own directory. Sharing one meant a pinned checkout left a
# detached HEAD behind, and the next upstream run's `pull --ff-only || true`
# swallowed the resulting error -- so it silently re-tested the pin while
# reporting upstream.
[private]
prepare-hermes-ref ref dir:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -d "{{ dir }}/.git" ]]; then
      git -C "{{ dir }}" fetch -q --depth 1 origin "{{ ref }}"
    else
      echo "Cloning hermes-agent into {{ dir }}"
      git init -q "{{ dir }}"
      git -C "{{ dir }}" remote add origin "{{ hermes_agent_repo }}"
      git -C "{{ dir }}" fetch -q --depth 1 origin "{{ ref }}"
    fi
    git -C "{{ dir }}" checkout -q FETCH_HEAD
    echo "hermes-agent at $(git -C "{{ dir }}" rev-parse HEAD)"

# Run the full contract suite against the deployed pin.
#
# HERMES_EXPECTED_REVISION makes the lane refuse a checkout at any other
# revision, so a stale HERMES_AGENT_PATH cannot receipt the wrong host.
test-contract-pinned:
    #!/usr/bin/env bash
    set -euo pipefail
    pin="$({{ uv }} run python -c 'from hermes_plugin_kit.testing import DEPLOYED_HERMES_REVISION as r; print(r)')"
    dir="${HERMES_AGENT_PATH:-{{ hermes_pinned_dir }}}"
    if [[ -z "${HERMES_AGENT_PATH:-}" ]]; then
      just prepare-hermes-ref "$pin" "{{ hermes_pinned_dir }}"
    fi
    HERMES_CONTRACT_REQUIRED=1 HERMES_EXPECTED_REVISION="$pin" \
      HERMES_AGENT_PATH="$(cd "$dir" && pwd)" \
      just _run-contract-suite "pinned $pin"

# Run the full contract suite against upstream main -- the drift lane.
test-contract-upstream:
    #!/usr/bin/env bash
    set -euo pipefail
    dir="${HERMES_AGENT_PATH:-{{ hermes_upstream_dir }}}"
    if [[ -z "${HERMES_AGENT_PATH:-}" ]]; then
      just prepare-hermes-ref main "{{ hermes_upstream_dir }}"
    fi
    rev="$(git -C "$dir" rev-parse HEAD)"
    HERMES_CONTRACT_REQUIRED=1 HERMES_AGENT_PATH="$(cd "$dir" && pwd)" \
      just _run-contract-suite "upstream $rev"

# Backwards-compatible alias for the upstream lane.
test-contract: test-contract-upstream

# Run both contract modules and prove the run actually executed checks.
#
# The exit status alone cannot say whether anything ran: on Python 3.11 --
# the version CI pins -- unittest exits 0 both when every test skips and when
# zero tests are collected. So the executed count is the evidence.
[private]
_run-contract-suite label:
    #!/usr/bin/env bash
    set -euo pipefail
    log="$(mktemp)"
    trap 'rm -f "$log"' EXIT
    set +e
    {{ uv }} run python -m unittest tests.test_hermes_contract tests.test_context_engine_contract -v \
      > "$log" 2>&1
    echo "EXIT=$?" >> "$log"
    set -e
    tail -25 "$log"
    grep -qE '^EXIT=0' "$log" || { echo "contract suite failed ({{ label }})" >&2; exit 1; }
    ran="$(grep -oE '^Ran [0-9]+' "$log" | grep -oE '[0-9]+' | head -n1)"
    test -n "$ran" && [ "$ran" -gt 0 ] \
      || { echo "no contract tests ran ({{ label }}); a lane that checked nothing is not a lane that passed" >&2; exit 1; }
    if grep -qE 'OK \(skipped=' "$log"; then
      echo "contract suite skipped tests ({{ label }}); expected a host for every case" >&2
      exit 1
    fi
    echo "contract receipt: {{ label }}; executed=$ran"

# Run the context-engine contract. Requires an explicit checkout.
test-context-engine-contract:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${HERMES_AGENT_PATH:?set it to a hermes-agent checkout; this lane does not guess}"
    HERMES_CONTRACT_REQUIRED=1 {{ uv }} run python -m unittest tests.test_context_engine_contract -v

# Refuse to run when the installed OpenSpec predates the archive-correctness fixes.
[private]
openspec-floor:
    #!/usr/bin/env bash
    set -euo pipefail
    have="$({{ openspec }} --version 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$have" ]]; then
      echo "openspec not found on PATH; need >= {{ openspec_min }}" >&2
      exit 1
    fi
    lowest="$(printf '%s\n%s\n' "$have" "{{ openspec_min }}" | sort -V | head -n1)"
    if [[ "$have" != "{{ openspec_min }}" && "$lowest" != "{{ openspec_min }}" ]]; then
      echo "openspec $have is below the required {{ openspec_min }}" >&2
      echo "Releases before {{ openspec_min }} could exit 0 from archive having applied" >&2
      echo "less than the delta specified. Upgrade before validating or archiving." >&2
      exit 1
    fi

# Validate every OpenSpec change, and confirm each authored one will archive.
spec-validate: openspec-floor
    #!/usr/bin/env bash
    set -euo pipefail
    changes="$({{ openspec }} list --json | {{ uv }} run python -c 'import json,sys; print("\n".join(c["name"] for c in json.load(sys.stdin)["changes"]))')"
    test -n "$changes" || { echo "no OpenSpec changes found" >&2; exit 1; }
    for change in $changes; do
      echo "== $change"
      {{ openspec }} validate "$change" --strict
      # `validate --strict` can pass while `archive` refuses — a MODIFIED header
      # matching nothing in the main spec is INFO at validate and fatal at archive.
      # Resolving the deltas catches that early, but only once a proposal exists.
      if [[ -f "openspec/changes/$change/proposal.md" ]]; then
        {{ openspec }} show "$change" --diff >/dev/null
      else
        echo "   (not yet authored — skipping delta resolution)"
      fi
    done

# Build the wheel and source distribution.
build:
    rm -rf dist
    {{ uv }} build

# Validate wheel and source-distribution metadata.
check-dist:
    {{ uv }} run twine check dist/*

# Prove every public name imports from an installed wheel, not just the source tree.
#
# `twine check` validates metadata and never opens the archive, so it cannot see
# a module missing from the build. Only installing and importing can.
check-install: build
    #!/usr/bin/env bash
    set -euo pipefail
    wheel="$(ls dist/*.whl | head -n1)"
    test -n "$wheel" || { echo "no wheel in dist/" >&2; exit 1; }
    workdir="$(mktemp -d)"
    trap 'rm -rf "$workdir"' EXIT
    # --no-project keeps the source tree off sys.path, so a name that only
    # resolves in-tree fails here instead of passing by accident.
    repo="$PWD"
    cat > "$workdir/probe.py" <<'PROBE'
    import hermes_plugin_kit as hpk
    missing = sorted(n for n in hpk.__all__ if not hasattr(hpk, n))
    if missing:
        raise SystemExit("not importable from the installed wheel: " + ", ".join(missing))
    import hermes_plugin_kit.testing as t
    t.RecordingPluginContext(name="install-probe")
    print("installed wheel exports", len(hpk.__all__), "names; harness constructs")
    PROBE
    cd "$workdir"
    {{ uv }} venv --quiet .venv
    VIRTUAL_ENV="$workdir/.venv" {{ uv }} pip install --quiet "$repo/$wheel"
    VIRTUAL_ENV="$workdir/.venv" {{ uv }} run --no-project python probe.py

# Remove Python caches and build artifacts.
clean:
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
    rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info
