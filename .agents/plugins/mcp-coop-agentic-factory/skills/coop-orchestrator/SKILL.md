---
name: coop-orchestrator
description: >-
  Universal task orchestration, context window hygiene, subagent delegation, and verification loop workflow. Apply when decomposing multi-step tasks, coordinating research or subagents, and verifying code changes.
---

# Cooperative Task Orchestrator (`coop-orchestrator`)

## 1. Task Decomposition & Single-Loop First
- Break ambiguous or multi-step goals into small, atomic, independently verifiable steps before editing code.
- Default to a direct single-agent loop for localized tasks; avoid over-engineering workflows when a direct edit and check is sufficient.

## 2. Context Hygiene & Subagent Delegation
- Protect the primary context window from noise and large raw outputs.
- Spawn subagents only for broad codebase exploration, heavy documentation research, or independent parallel workstreams.
- Because a subagent has an isolated context, always provide a self-contained brief:
  1. **Objective**: exact question to answer or artifact to produce.
  2. **Scope & Constraints**: target directories, files, or boundaries.
  3. **Expected Output**: concise structured summary with exact file/line references, never raw file dumps.

## 3. Execution-Verification Loop (Evaluator-Optimizer)
- Never ship code blind: validate every atomic change with targeted typechecks, linters, or unit tests.
- **Root-Cause First**: when a test or command fails, read the exact error and identify the root cause before making another edit.
- **Bounded Retries**: never repeat a failing command blindly. If an issue is not resolved after 2–3 reasoned attempts, stop, summarize the findings, and align with the user.
