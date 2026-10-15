# Repository Guidelines

## Project Structure & Module Organization

This Python 3.11+ project implements a document import and retrieval service. Application code lives in `app/`: `import_process/` handles upload and document-ingestion graphs, while `query_process/` serves retrieval and answer-generation graphs. Keep HTTP endpoints in each area's `api/`, LangGraph orchestration in `agent/`, and graph steps in `agent/nodes/`. Shared integrations belong in `clients/`, model helpers in `lm/`, settings in `conf/`, and reusable helpers in `core/` or `utils/`.

Prompt templates are stored in `prompts/`. Source PDF fixtures are in `doc/`; generated artifacts, logs, and temporary processing results belong in `output/` and `logs/`, not in application modules. Place runnable checks in `test/` using the existing numeric naming pattern, such as `test/06-test-query-flow.py`.

## Build, Test, and Development Commands

- `uv sync` installs the locked dependencies from `pyproject.toml` and `uv.lock`.
- `uv run python app/import_process/api/file_import_service.py` starts the import API on port 8000.
- `uv run python app/query_process/api/query_service.py` starts the query API on port 8001.
- `uv run python test/05-test-main-graph.py` runs the end-to-end graph smoke test. Run targeted scripts matching the area changed before opening a PR.

External services (such as MongoDB, Milvus, MinIO, and model providers) must be configured before integration flows can run.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for modules, functions, variables, and JSON keys, and `PascalCase` for classes. Keep FastAPI route handlers thin; put workflow logic in agent nodes and shared behavior in utilities. Follow nearby type-hint and docstring conventions, use explicit exceptions for client/API failures, and keep prompt text in `.prompt` files rather than embedding large templates in Python.

## Testing Guidelines

The repository currently uses executable Python smoke-test scripts rather than a declared test runner. Name new scripts `test/NN-test-<feature>.py`, make their setup requirements explicit, and avoid relying on local output from previous runs. For changes to a graph node, add or extend the smallest corresponding script and run it with `uv run python`.

## Commit & Pull Request Guidelines

Git history is not available in this checkout. Use short imperative commit subjects, for example `Add import retry handling` or `Fix sparse-vector normalization`. Keep commits focused. PRs should explain the affected pipeline, configuration or service prerequisites, test commands and results, and include request/response examples or screenshots for API/UI changes. Never commit credentials, provider keys, generated PDFs, or log output.
