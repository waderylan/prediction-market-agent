# AGENTS.md

CSCI 599 Assignment 1 — Tool-Using Agent with MCP Integration

This repository contains an individual course project to build and deploy an LLM-powered, tool-using agent with MCP integration and conversational memory. The completed project must satisfy the implementation, deployment, testing, documentation, and submission requirements in `docs/assignment/Assignment_1_Description.md`.

This file is the canonical coordination guide for coding agents in this repository. It intentionally does not repeat the assignment specification.

## 1. Assignment Ground Truth

- Read `docs/assignment/Assignment_1_Description.md` completely before making project decisions or changing code, configuration, documentation, tests, deployment, or submission artifacts.
- Treat `docs/assignment/Assignment_1_Description.md` as the ground truth for all project requirements, grading criteria, academic-integrity rules, technical contracts, and deliverables.
- Do not rely on summaries in chat or agent memory when the assignment document can answer the question directly.
- If repository code, documentation, or a user request appears inconsistent with the assignment, identify the conflict before proceeding.
- Follow higher-priority user and platform instructions when they conflict with this file.

## 2. Required Agent Workflow

At the start of every agent session:

1. Read this file completely.
2. Read `docs/assignment/Assignment_1_Description.md` completely.
3. If you are the top-level agent, generate one session UUID for working context only. Do not write a session-start entry.
4. Complete the user's request while preserving the assignment contract.
5. Only if the completed work qualifies under §3, the top-level agent appends exactly one event entry using §4.1 and §4.2 immediately before sending the response.

The top-level agent is the only transcript writer for its conversation. Sub-agents must never write to the transcript; they report qualifying work to the parent, which includes it in one consolidated entry. Separate top-level sessions and worktrees share the transcript and must use the concurrency-safe append protocol in §4.2.

## 3. Transcript Location and Safety

The shared transcript is `AI_TRANSCRIPT.md` beside this file in the repository root.

- Resolve its path relative to this file; do not hardcode an absolute path.
- Create it if missing with the single final line `<!-- AI_TRANSCRIPT_EOF -->`.
- Append only. Never rewrite, reorder, truncate, or delete earlier entries.
- The file must contain exactly one `<!-- AI_TRANSCRIPT_EOF -->` marker, and it must remain the final line. Treat this marker as the only legal insertion point.
- Never anchor a transcript edit on a previous heading, `Context` block, timestamp, or other repeated content. That can insert new entries in the middle of the file.
- Never rewrite the whole transcript with a formatter, generated file, search-and-replace, or shell redirection.
- Write an event entry only when at least one of these conditions is satisfied:
  - **Major feature implemented:** the turn completes and verifies a substantial new user-visible capability, assignment milestone, external integration, service, endpoint, persistent data model, deployment component, or comparably significant workflow.
  - **Major bug discovered or fixed:** the turn produces concrete evidence of, or verifies a fix for, a high-impact correctness, security, data-loss, crash, deployment, MCP-contract, or core-workflow failure.
- A feature plan, partial scaffold, or unverified implementation does not qualify until the major capability works.
- Minor bugs, cosmetic defects, small refactors, dependency maintenance, test-only changes, documentation-only changes, configuration tweaks, and routine cleanup do not qualify unless they are inseparable from a qualifying major feature or major bug.
- User feedback, revision requests, information-only answers, planning, status updates, code review, and ordinary repository edits do not qualify by themselves.
- Do not write `SESSION START`, interruption, or administrative entries. If no qualifying event occurred, do not touch `AI_TRANSCRIPT.md`.
- If a major bug is discovered and fixed in the same turn, write one combined entry rather than separate discovery and fix entries.
- When uncertain whether the threshold is met, do not log the turn.
- Record the user prompt verbatim unless it contains secrets or sensitive personal information.
- Record the final user-facing response verbatim. Draft it, append it, verify it, then send the same response.
- Replace API keys, tokens, passwords, cookies, OAuth codes, private keys, `.env` values, and sensitive PII with `[REDACTED]`.
- Do not copy binary data, large file contents, dependency output, or full command output into the transcript. Summarize actions and results.
- Keep `AI_TRANSCRIPT.md` separate from `PROCESS_LOG.md`. The transcript is automatic evidence; the submitted process log must remain Rylan Wade's accurate, personal reflection.

## 4. Transcript Format

### 4.1 Major Event Entry

```markdown
<!-- transcript-entry-id: event:<event-uuid> -->
## YYYY-MM-DDTHH:MM:SS±HH:MM — [MAJOR FEATURE|MAJOR BUG] <short descriptive title>

- Event: <major feature implemented|major bug discovered|major bug fixed|major bug discovered and fixed>

### User Prompt

<verbatim prompt with secrets and sensitive PII replaced by `[REDACTED]`>

### Agent Response

<verbatim final user-facing response>

### Work Performed

- <files created or edited>
- <implementation or bug evidence>
- <verification commands and results>
- <important decisions or unresolved issues>

### Context

- Session ID: <session UUID held in working context>
- Tool: <exact coding agent or harness name>
- Branch: <branch name or `not a Git repository`>
- Repository root: <absolute path>
- Parent agent: <name or `none`>
```

Generate one event UUID when qualifying work is confirmed and reuse it if the finalization step is retried. Before appending, search for its exact `transcript-entry-id`; if present, update nothing and do not create a duplicate.

### 4.2 Concurrency-Safe Append and Verification

For every qualifying event entry:

- Re-read the transcript tail immediately before editing and confirm the EOF marker is the final line.
- Use one `apply_patch` operation that matches only the unique EOF marker and replaces it with: the new entry, one blank line, and the same EOF marker. Do not use prior transcript content as patch context.
- If the patch fails, another session may have appended concurrently. Re-read the tail and retry against the EOF marker; never replace the file.
- Search for the new `transcript-entry-id` and confirm it occurs exactly once.
- Confirm the EOF marker occurs exactly once and is still the final line.
- Re-read the new transcript entry.
- Confirm the prompt and final response match the conversation.
- Confirm `Tool` is present and accurate.
- Confirm no secrets or sensitive PII were logged.
- Confirm the content that preceded the old EOF marker was not changed.

## 5. Minimal Repository Guardrails

- Never commit or expose credentials. Use environment variables and ignored local secret files as directed by the assignment.
- Do not claim that code, tests, tools, deployment, or verification worked unless the relevant action was actually performed and inspected.
- Do not fabricate personal experiences, prompts, lessons, or reflections for `PROCESS_LOG.md`; ask Rylan Wade when personal input is required.
- Preserve user-authored and unrelated changes.
- Keep `CLAUDE.md` as the single-line import `@AGENTS.md`.
- Keep project behavior and documentation aligned with `docs/assignment/Assignment_1_Description.md`.
