# AGENTS.md

CSCI 599 Assignment 1 — Tool-Using Agent with MCP Integration

This repository contains an individual course project to build and deploy an LLM-powered, tool-using agent with MCP integration and conversational memory. The completed project must satisfy the implementation, deployment, testing, documentation, and submission requirements in `Assignment_1_Description.md`.

This file is the canonical coordination guide for coding agents in this repository. It intentionally does not repeat the assignment specification.

## 1. Assignment Ground Truth

- Read `Assignment_1_Description.md` completely before making project decisions or changing code, configuration, documentation, tests, deployment, or submission artifacts.
- Treat `Assignment_1_Description.md` as the ground truth for all project requirements, grading criteria, academic-integrity rules, technical contracts, and deliverables.
- Do not rely on summaries in chat or agent memory when the assignment document can answer the question directly.
- If repository code, documentation, or a user request appears inconsistent with the assignment, identify the conflict before proceeding.
- Follow higher-priority user and platform instructions when they conflict with this file.

## 2. Required Agent Workflow

At the start of every agent session:

1. Read this file completely.
2. Read `Assignment_1_Description.md` completely.
3. Append a `SESSION START` entry to `AI_TRANSCRIPT.md` using §4.1.
4. Complete the user's request while preserving the assignment contract.
5. If the turn qualifies under §3, append the user prompt and final response to `AI_TRANSCRIPT.md` using §4.2 before sending the response.

Do not skip logging for qualifying turns. Sub-agents and worktrees use the same transcript.

## 3. Transcript Location and Safety

The shared transcript is `AI_TRANSCRIPT.md` beside this file in the repository root.

- Resolve its path relative to this file; do not hardcode an absolute path.
- Create it if missing.
- Append only. Never rewrite, reorder, truncate, or delete earlier entries.
- Log a turn only when at least one of these conditions is true:
  - Code, documentation, configuration, tests, deployment files, or other repository artifacts are created, edited, or deleted.
  - The user gives feedback on prior agent work, repository work, or a proposed change, including requests for revisions.
- Do not log information-only questions or answers when no repository artifact changes and the user is not giving feedback.
- Do not log routine planning or status-only exchanges unless they also meet one of the conditions above.
- Record the user prompt verbatim unless it contains secrets or sensitive personal information.
- Record the final user-facing response verbatim. Draft it, append it, verify it, then send the same response.
- Replace API keys, tokens, passwords, cookies, OAuth codes, private keys, `.env` values, and sensitive PII with `[REDACTED]`.
- Do not copy binary data, large file contents, dependency output, or full command output into the transcript. Summarize actions and results.
- Keep `AI_TRANSCRIPT.md` separate from `PROCESS_LOG.md`. The transcript is automatic evidence; the submitted process log must remain Rylan Wade's accurate, personal reflection.

## 4. Transcript Format

### 4.1 Session Start

```markdown
## YYYY-MM-DDTHH:MM:SS±HH:MM — SESSION START

- Tool: <exact coding agent or harness name>
- Repository root: <absolute path>
- Branch: <branch name or `not a Git repository`>
- Worktree: <path or `main`>
- Parent agent: <name or `none`>
```

### 4.2 Per-Turn Entry

```markdown
## YYYY-MM-DDTHH:MM:SS±HH:MM — <short descriptive title>

### User Prompt

<verbatim prompt with secrets and sensitive PII replaced by `[REDACTED]`>

### Agent Response

<verbatim final user-facing response>

### Work Performed

- <files created or edited>
- <commands, tests, or tools used>
- <important decisions or unresolved issues>

### Context

- Tool: <exact coding agent or harness name>
- Branch: <branch name or `not a Git repository`>
- Repository root: <absolute path>
- Parent agent: <name or `none`>
```

If interrupted before a final response, log the work completed and write `Interrupted before final response`. Append a new entry when work resumes.

### 4.3 Verification

Before sending each final response for a qualifying turn:

- Re-read the new transcript entry.
- Confirm the prompt and final response match the conversation.
- Confirm `Tool` is present and accurate.
- Confirm no secrets or sensitive PII were logged.
- Confirm earlier transcript entries were not changed.

## 5. Minimal Repository Guardrails

- Never commit or expose credentials. Use environment variables and ignored local secret files as directed by the assignment.
- Do not claim that code, tests, tools, deployment, or verification worked unless the relevant action was actually performed and inspected.
- Do not fabricate personal experiences, prompts, lessons, or reflections for `PROCESS_LOG.md`; ask Rylan Wade when personal input is required.
- Preserve user-authored and unrelated changes.
- Keep `CLAUDE.md` as the single-line import `@AGENTS.md`.
- Keep project behavior and documentation aligned with `Assignment_1_Description.md`.
