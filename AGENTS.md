# CLAUDE.md
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.
**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 0. Communication
- Answer concisely. NO apologies, NO long-winded explanations, NO robotic greetings.
- All variable names, functions, classes, and inline comments MUST be in English. Use Vietnamese only when chatting and explaining concepts.
- When suggesting improvements beyond the original request, flag explicitly as an optional suggestion — never mix into the main deliverable.

## 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If a task is ambiguous or involves >3 steps, outline a brief plan and wait for confirmation before writing code.

## 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- No magic numbers or hardcoded strings — extract into well-named constants, enums, or config files.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Only output snippets that need to be added or modified. DO NOT output the entire file unless >50% has changed.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Code Quality
**Clean, defensive, self-documenting.**

- Follow DRY. Prioritize performance and security.
- Use meaningful names to explain WHAT. Use comments only to explain WHY (business logic or non-obvious decisions).
- Apply Defensive Programming: validate inputs early, Fail Fast, never trust external data.
- Never silently swallow exceptions. Log errors with sufficient context.
- When fixing bugs, resolve the Root Cause. No temporary patches or hacky workarounds.

## 5. Goal-Driven Execution
**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 6. Finish The Plan First
**Complete the agreed plan before proposing upgrades.**

- If a PLAN already exists, prioritize finishing it before suggesting enhancements.
- Verify the PLAN is complete and stable before proposing optimizations, extensions, or refactors outside its scope.
- Don't mix upgrade work into unfinished PLAN items unless the user explicitly asks to change the plan.

---

## 7. MCP: Memory
- Before starting any task involving creating/modifying >2 files or implementing a new feature, use the `memory` MCP to read saved project context first.
- After completing a significant task (new feature, major refactor, key architectural decision), proactively save relevant context to the `memory` MCP for future sessions.

## 8. MCP: Puppeteer
Use Puppeteer to search Official Docs or StackOverflow ONLY when:
1. Encountering a cryptic runtime error
2. Working with a library released or updated after your knowledge cutoff
3. Explicitly asked to research something

---
**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## 9. Auto-YT Project Policy
- **ALWAYS** use Playwright to interact with ChatGPT for this project. Do not rely on direct API calls or other methods for fetching data or prompting ChatGPT, as the project's core functionality relies on utilizing the authenticated browser session via Playwright.
- **ALWAYS** implement features and fixes generically for every video. Never hardcode behavior for a specific video ID, YouTube URL, title, ChatGPT conversation, prompt version, or stored record. Video-specific data may only be used to reproduce and verify a general solution.

## 10. Auto_YT Multi-Service Lifecycle Policy
- **Complete Pipeline Invariant**: Auto_YT relies on interdependent services to produce videos end-to-end:
  1. **Backend API** (FastAPI on port `8080`): Orchestration, SQLite DB, queue workers, video muxing.
  2. **Frontend UI** (Vite + React on port `5173`): User management, progress tracking, preview & download.
  3. **OmniVoice TTS Worker** (FastAPI on port `8011`): Local Vietnamese voice cloning & TTS audio generation.
  4. **ChatGPT Browser Service** (Playwright Chromium CDP with profile `PROFILE_GPT_1`): Outlines, scripts, scene plans, metadata.
  5. **Google Flow Browser Service** (Playwright Chromium CDP with profile `PROFILE_GOOGLE_FLOW_1`): Scene visual image generation.
- **Future-Proofing & Service Extensibility (MANDATORY)**:
  Whenever ANY new service, background worker, external tool, or automation daemon is added to the Auto_YT pipeline in the future:
  1. **Start Script (`start_autoyt.ps1` / `run_autoyt.bat`)**: MUST be updated to initialize the new service, check its health/readiness probe, and include its status in the terminal summary.
  2. **Stop Script (`stop_autoyt.ps1` / `run_autoyt_stop.bat`)**: MUST be updated to safely terminate the new service, release its listening ports, kill associated browser/helper processes, and remove its lock/state files.
  3. **Restart Script (`run_autoyt_restart.bat`)**: Automatically inherits clean stop and startup so the new service restarts seamlessly without leaks or port collisions.
  Never allow a new service to run as an unmonitored orphan outside this unified lifecycle trio.
- **Startup Invariant**: Any startup routine must verify the readiness of all registered services before declaring the system ready.
- **Shutdown & Cleanup Invariant**: Any shutdown routine must:
  - Free all registered service ports (`8080`, `5173`, `8011`, and any future ports).
  - Terminate project-specific Chromium processes (`PROFILE_GPT_*`, `PROFILE_GOOGLE_FLOW_*`) without touching the user's personal browser.
  - Remove stale `SingletonLock` files and state markers (`*.stop.json`, `worker.pid`) to guarantee conflict-free restarts.
- **Dual Directory Parity**: Always maintain identical execution wrappers (`run_autoyt.bat`, `run_autoyt_restart.bat`, `run_autoyt_stop.bat`) at both the project root (`Auto_YT\`) and subfolder (`Tool-auto-login-GPT\`).
- **Post-Fix Safe Restart & Idle Verification Invariant (MANDATORY)**:
  Whenever code fixes or modifications are completed:
  1. **Idle Verification First**: MUST check whether any generation, TTS, rendering, or browser pipeline processes/jobs are currently running or immediately claimable. This includes `system_jobs` in `processing`/`running`, `queued`, and due `retry_wait` states, background worker loops, active Playwright sessions, and any job that can launch a channel GPM profile.
  2. **Active Process Handling**: If any background job or process is active, DO NOT restart immediately. Wait and monitor until all active jobs have completed and the system returns to an idle state.
  3. **Clean Restart**: Only once the system is verified idle, trigger `run_autoyt_restart.bat` to safely apply the code updates across all services without interrupting active work.
  4. **No GPM Launch During Startup**: Starting or restarting Auto_YT MUST NOT directly activate comment jobs, scanners, or other channel work that can launch a GPM profile. GPM profiles may open only for work scheduled after the application is fully ready or for an explicit user action.

## 11. Anti-Detect Browser (GPM-Login) & Zero-Footprint Channel Isolation Policy
- **GPM-Login API Versioning**:
  - Always target GPM-Login v3 endpoints (`/api/v3/profiles`, `/api/v3/profiles/start/{id}`, `/api/v3/profiles/stop/{id}`) for local API communication (`http://127.0.0.1:19995`).
  - Gracefully handle GPM v3 plain-text response (`b"GPM-Login"`) for unmapped routes and prioritize `/api/v3` before falling back to `/api/v1`.
- **Complete Zero-Footprint Network Isolation (MANDATORY)**:
  - Every YouTube channel MUST be assigned a dedicated GPM Profile with its own proxy and browser fingerprint.
  - **Dual Isolation Invariant**:
    1. **Browser Operations**: YouTube Studio uploads, UI verification, and manual management MUST execute inside the channel's GPM profile via Playwright CDP.
    2. **Background REST API Operations**: ALL background HTTP requests for a managed channel (OAuth token refresh, comment sync, video sync, resumable video chunk uploads, caption/thumbnail uploads) MUST be routed through the channel's assigned GPM proxy via `proxy_utils.py`.
  - NEVER allow background REST API calls for a proxy-assigned channel to leak through the host machine's raw WAN IP.

## 12. Secret & Access Token Security Policy (MANDATORY)
- This policy applies to every credential handled by the project, including API keys, OAuth access/refresh tokens, Page Access Tokens, app secrets, session cookies, passwords, and proxy credentials.
- **Encrypted at Rest**: Never persist credentials as plaintext in SQLite, JSON, backups, cache files, browser storage, or generated artifacts. On Windows, store them through the centralized DPAPI-backed secret store. Migrate legacy plaintext values and clear the original fields.
- **Backend-Only / Write-Only**: Credentials may enter through an authenticated backend endpoint but MUST NOT be returned by any API response. Frontend state may receive only non-sensitive metadata such as `token_configured`, expiry, scopes, or account ID. Never store credentials in `localStorage`, `sessionStorage`, URLs, or DOM attributes.
- **Safe Transport**: Send bearer tokens through the `Authorization` header and secrets through request bodies when an upstream API requires them. Never place credentials in query strings, request URLs, redirects, command-line arguments, or exception messages.
- **Safe Logging and Errors**: Pass all external errors, tracebacks, job errors, and diagnostic output through centralized secret redaction before logging, persisting, or returning them. Logs may record credential type or configured state, never prefixes, suffixes, hashes, or recoverable fragments.
- **Identity and Lifetime Validation**: Verify that a returned token belongs to the explicitly requested account/Page and validate its actual expiry/scopes before labeling it long-lived. Never silently select the first account or claim a token is permanent without authoritative validation.
- **Proxy Fail-Closed**: For any channel assigned to a GPM proxy, credential refreshes and authenticated REST requests MUST fail if that proxy is missing or unusable. They must never fall back to the host WAN IP.
- **Required Regression Checks**: Every credential-related change must include tests proving that API responses contain no secret fields or token patterns, persisted plaintext count is zero, logs/errors are redacted, sensitive API responses use `Cache-Control: no-store`, and proxy-assigned requests fail closed.
- **Exposure Response**: If plaintext credentials are found, remove them from active data, backups, logs, and browser storage; add a regression test; and report that the affected credential must be rotated. Do not revoke or rotate an external credential without explicit user authorization.
- Review debug scripts, tests, migration code, backups, and manual tooling under the same rules as production code. No security exception is allowed merely because code is local or temporary.
