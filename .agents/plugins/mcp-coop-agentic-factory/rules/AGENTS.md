# Universal Engineering Rules

---

## 1. Deliberate & Incremental Execution
- Work in small, verifiable iterations (`Plan -> Step -> Review`) rather than dumping large, multi-module changes at once.
- Before writing code, briefly state **WHAT** is changing and **WHY**; after modifying code, summarize the resulting data flow.

## 2. Engineering Rigor & Anti-Sycophancy
- Treat user questions (`Why is it done this way?`, `What if we use X?`) as an engineering discussion, **never** as an automatic order to delete and rewrite code.
- Explain trade-offs first, propose alternatives with a `(Recommended)` option, and modify code only after explicit user confirmation.
- Verify unfamiliar APIs, schemas, and file paths via context or documentation instead of guessing.

## 3. Code Quality & Language Standard
- Maintain a Single Source of Truth (`DRY`), clear single responsibility (`SRP`), and `KISS / SOLID / YAGNI`.
- **Zero-Hardcode**: never scatter magic numbers or raw string literals across modules; extract constants, enums, and typed settings.
- **Language**: write all code, identifiers, and system literals in **English**; write architectural comments/docstrings (`Why & How`) and chat responses in the **user's language**.

## 4. Operational Safety & Orchestration
- Never run destructive commands (`rm -rf /`, force-push to main branches, or unconfirmed `git reset --hard`).
- Apply the `coop-orchestrator` skill for task decomposition, context hygiene, subagent delegation, and verification loops.
