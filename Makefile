.DEFAULT_GOAL := help
UV ?= uv
HERMES_AGENT_REPO ?= https://github.com/NousResearch/hermes-agent.git
HERMES_AGENT_DIR ?= .hermes-agent

.PHONY: help install test test-one test-contract build clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create/sync the uv-managed environment
	$(UV) sync

test: ## Run the full unittest suite
	$(UV) run python -m unittest discover -s tests

test-one: ## Run a single test: make test-one T=tests.test_kit.Class.method
	$(UV) run python -m unittest $(T)

test-contract: ## Clone hermes-agent into a staging dir and run the contract tests against it
	@if [ -d "$(HERMES_AGENT_DIR)/.git" ]; then \
		echo "Updating $(HERMES_AGENT_DIR)"; git -C "$(HERMES_AGENT_DIR)" pull --ff-only -q || true; \
	else \
		echo "Cloning hermes-agent into $(HERMES_AGENT_DIR)"; git clone --depth 1 "$(HERMES_AGENT_REPO)" "$(HERMES_AGENT_DIR)"; \
	fi
	HERMES_AGENT_PATH="$(abspath $(HERMES_AGENT_DIR))" $(UV) run python -m unittest tests.test_hermes_contract -v

build: ## Build the wheel/sdist distribution
	$(UV) build

clean: ## Remove Python caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info
