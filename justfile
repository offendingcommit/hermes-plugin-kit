set shell := ["bash", "-cu"]

uv := env_var_or_default("UV", "uv")
hermes_agent_repo := env_var_or_default("HERMES_AGENT_REPO", "https://github.com/NousResearch/hermes-agent.git")
hermes_agent_dir := env_var_or_default("HERMES_AGENT_DIR", ".hermes-agent")

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

# Prepare a local Hermes checkout unless the caller supplied one.
[private]
prepare-hermes-agent:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "${HERMES_AGENT_PATH:-}" ]]; then
      exit 0
    elif [[ -d "{{ hermes_agent_dir }}/.git" ]]; then
      echo "Updating {{ hermes_agent_dir }}"
      git -C "{{ hermes_agent_dir }}" pull --ff-only -q || true
    else
      echo "Cloning hermes-agent into {{ hermes_agent_dir }}"
      git clone --depth 1 "{{ hermes_agent_repo }}" "{{ hermes_agent_dir }}"
    fi

# Run contract tests against an existing or locally managed Hermes checkout.
test-contract: prepare-hermes-agent
    #!/usr/bin/env bash
    set -euo pipefail
    hermes_path="${HERMES_AGENT_PATH:-{{ hermes_agent_dir }}}"
    HERMES_AGENT_PATH="$(cd "$hermes_path" && pwd)" \
      {{ uv }} run python -m unittest tests.test_hermes_contract -v

# Run the context-engine contract against HERMES_AGENT_PATH.
test-context-engine-contract:
    {{ uv }} run python -m unittest tests.test_context_engine_contract -v

# Build the wheel and source distribution.
build:
    rm -rf dist
    {{ uv }} build

# Validate wheel and source-distribution metadata.
check-dist:
    {{ uv }} run twine check dist/*

# Remove Python caches and build artifacts.
clean:
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
    rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info
