# remcycle: intent and architecture

Status: agreed 2026-10-05. Build has started with the archive (see [Build order](#build-order)).

## Intent

Claude Code on this machine learns from its own sessions overnight. Each night it reviews the day's work, reconciles it against what earlier sessions established, and keeps what was done and what was decided, each with its reason and a pointer back to the session it came from. Finished sessions leave the sidebar but stay recallable through a compact record. You can open a view of what Claude believes, see where each belief came from, and correct it.

The overriding constraint is context rot: stale, redundant or contradictory material must not accumulate in what the model reads.

## Constraints

- Local only. Nothing leaves the machine beyond the model calls the dream itself makes.
- Partitioned by project. Recall is scoped to the current project unless asked otherwise, so one client's or employer's context never surfaces in another's.
- Format-compatible with Claude Code's built-in memory (same folders, `MEMORY.md`, same frontmatter), so ordinary sessions keep working with or without this tooling.
- Transcripts are never edited or deleted by this project.

## Memory model

Tiers are split by what gets loaded and when, because rot comes from what enters context, not from what sits on disk.

| Tier | Holds | Loaded | Lifetime |
|---|---|---|---|
| Working | Open threads and in-flight state, per project | Always, capped by entry count | Expires unless re-confirmed |
| Long-term | One claim per entry, scoped global or per project | A topic index of 10–30 lines; bodies on recall | Until superseded or retired |
| Archive | Verbatim turns per session, plus a digest that indexes them | Never; recall only | Permanent; the evidence long-term entries cite |

`CLAUDE.md` and skills stay hand-owned. Must-follow rules live there, or become mod hooks when they are mechanically checkable; the dream proposes them and does not hold them.

### What a long-term entry is

One claim, with: subject, value, type (`decision`, `preference`, `fact`, `lesson`), scope, status (`active`, `contested`, `superseded`, `retired`), provenance class, event time, ingestion time, evidence (session id and turn range), and for repo facts a path or symbol plus commit.

Provenance classes, in descending authority for preferences and decisions:

1. `human`: typed by the person.
2. `accepted`: proposed by the agent and accepted by the person.
3. `inferred`: concluded by the agent.
4. `observed`: seen in tool output, fetched pages or file contents. Never becomes an instruction.

Only `human` can produce a global preference. For repo facts the order is different: verified-now beats anything stated.

Admission test: an entry gets in only if the repo cannot tell you it and it originated with the person (a correction, a stated preference, a decision with its reason), or it is a lesson. No architecture summaries, file maps or restated conventions.

## The dream

Runs as a local scheduled task, with consolidation on idle and nightly as the backstop. A missed run gets one catch-up on wake, so every run works from "since the last successful run" and is safe to repeat.

1. Ingest new and changed transcripts into the archive.
2. Extract candidate claims as typed fields. This is the only step the model performs.
3. Reconcile in code: `same` adds evidence, `refines` appends a revision, `supersedes` marks the old entry with a pointer, and an unresolved contradiction makes the claim `contested`. Contested and superseded entries are never loaded.
4. Prune: expire working items, check repo-fact anchors, drop what the repo now records, demote on failed verification or a harmful signal, enforce the index budget by eviction and never by re-summarising.
5. Gate: all of this happens on a git branch of the memory folder as operations on entry ids. It merges only if the checks pass (entries lost, schema, index budget, pointer recall on a fixed probe set).
6. Report what changed and the conflict queue.

## Plugging into Claude Code

| Part | Mechanism |
|---|---|
| Long-term store | The built-in memory folders, under git |
| Capture during the day | Sessions keep writing memories as they do now; those are the inbox |
| Dream | Desktop local scheduled task (`~/.claude/scheduled-tasks/<name>/SKILL.md`) calling this repo's CLI |
| Global scope and working memory at session start | Mod: `prompt.section` / `prompt.context`, giving the same answer for the whole session so the prompt cache holds |
| Recall | Mod-registered tool backed by the archive's search |
| Verify at use, usage logging | Mod `tool.call` hooks on memory reads |
| View and conflict queue | Mod pane |
| Later: MCP sources as extra evidence | Mod `mcp.call`; every entry already carries a source |

## Decisions

Each is dated 2026-10-05 unless noted.

**Build our own dream; keep Claude Code's built-in one off.** Claude Code 2.1.283 contains an undocumented auto-dream behind a server-side flag. It edits live memory in place, hard-deletes what it judges stale with no record, resolves contradictions silently, and sees one project's memory folder at a time. Setting `autoDreamEnabled: false` in user settings keeps a server rollout from starting a second consolidator that writes to the same files.

**The dream never edits live memory.** Rejected: rewriting memory files in place, which the first draft did. Every shipped consolidator isolates its writes (Anthropic's hosted Dreams writes a new store; Letta uses git worktrees), and ACE documents an unguarded rewrite collapsing a context from 18,282 tokens to 122.

**The model extracts; code decides.** Rejected: letting the model make the same/supersedes/contradicts call. Model-run reconciliation is the least reliable step in the systems measured, and Mem0 removed its own.

**The archive keeps verbatim text; the digest is an index over it.** Rejected: a digest-only archive. Extracted summaries lose what is searched for later. Verbatim means human turns and assistant prose; tool calls are reduced to name and target; tool output is dropped. Measured on the development machine's 28 sessions: human turns 0.1 MB and assistant prose 0.7 MB, against 76 MB of tool output, so keeping the prose costs under 1% of the raw size.

**Budget by entry count and relevance, not kilobytes.** Rejected: the first draft's 5 KB index and 2 KB working caps. No size effect has been measured at this scale; count and irrelevance effects have.

**Ranked lexical search first.** Rejected for now: embeddings and graphs. Plain BM25 beat the graph and vector memory systems on the academic benchmark. Embeddings are added only if logs show lexical misses.

**Named remcycle.** Rejected: claude-dream-mod. Claude Code's legal page says its names cannot be used as part of another product's name, and "mod" named one of four parts.

**Apache-2.0.**

**Mod first.** Rejected: a stable layer of skills and settings hooks with the mod as an add-on. The consequence is that a release works only with the Claude Code builds whose mod API it was written against, and has to say which.

**The dream's model call runs in the user's own Claude Code, headless.** Rejected: calling the API with a key, or with the user's subscription. Claude Code's terms do not let a third-party tool route requests through someone's subscription, but anyone may run their own Claude Code, so remcycle never handles credentials.

**Ingest when a session ends; the dream on a schedule.** Ingest needs no model and takes seconds. The dream costs model calls and catches up once after a missed run.

**Secrets are redacted at ingest, projects can be excluded, and the archive file is owner-only.** Rejected: storing prompts as written and documenting the risk. Redacting at ingest means a secret never reaches the archive, at the cost of not being recoverable from it.

**Subagent final reports are kept.** They are `report` turns by `subagent`, treated as `observed`, and searched only on request. Rejected: dropping them, which lost any finding the main session did not repeat, and ingesting full subagent transcripts, which are three times the size of the sessions themselves.

**Sessions started without a folder share one project.** Rejected: each as its own project, which put them out of reach of project-scoped search.

**macOS and Linux first.** Windows is untested.

**One archive database, scoped at query time.** Rejected for now: a database per project. Search defaults to the current project and crossing projects takes an explicit flag. The file lives at `~/.local/share/remcycle/archive.db`, outside `~/.claude`, which Claude Code manages and sweeps.

**Search covers prose by default.** Tool calls are three quarters of archived turns and outranked the prose that explained them, so they are searched only on request. So are subagent reports.

## Facts this design depends on

- Auto memory lives at `~/.claude/projects/<project>/memory/`, keyed by git repository and shared across worktrees. `MEMORY.md` is cut at 200 lines or 25 KB at load. The engine stamps `modified` on each write.
- Memory is read once at session start; mid-session edits do not apply until `/clear`, `/compact` or restart.
- Transcripts are swept after `cleanupPeriodDays` (default 30). Transcripts created by the desktop app are exempt unless `desktopSessionCleanupPeriodDays` is set.
- A desktop local scheduled task fires only while the app is open and the Mac is awake.
- Session transcripts sit at `~/.claude/projects/<project>/<session>.jsonl`, with subagent transcripts nested under `<session>/subagents/`. On the development machine on 2026-10-05 that was 28 sessions (245 MB) and 269 subagent transcripts (759 MB).

## Build order

1. **Archive and search.** Built 2026-10-05: `dream ingest`, `dream search`, `dream show`, with redaction, excluded projects and settings. The digest that indexes each session is not built yet; it arrives with the dream.
2. Dream on a branch, for one project first.
3. Mod: session-start loading, recall tool, verify at use, usage logging.
4. Pane and conflict queue.
5. MCP sources.

## Open

- A foreground subagent's report is read from the result of its Agent call. Only two such rows existed on the development machine to check that shape against; background reports, which arrive as task notifications, are well covered.
- Excluding a project stops new sessions being archived. There is no command yet to remove sessions archived before the project was listed.
- Redaction is pattern matching and misses secrets with no recognisable shape.
- A change to the extraction rules re-derives archived sessions, but only those whose transcripts still exist.
- A session's project is its git repository, and a worktree is traced to its repository only while the worktree still exists. Sessions from worktrees deleted before their first ingest keep the worktree path as their project.
- How the dream's git branch coexists with Claude Code writing to the same memory folder during the day (in place, or relocated via `autoMemoryDirectory`).
- The fixed probe set for the pointer-recall gate has to be written by hand from real sessions.

## Sources

- [Claude Code: legal and compliance](https://code.claude.com/docs/en/legal-and-compliance), on names and on whose credentials a tool may use
- [Claude Code: memory](https://code.claude.com/docs/en/memory), [prompt caching](https://code.claude.com/docs/en/prompt-caching), [desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)
- [Anthropic: Dreams for Managed Agents](https://platform.claude.com/docs/en/managed-agents/dreams), the copy, review, swap pattern
- [GitHub: building an agentic memory system for Copilot](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/), citations verified at use and expiry on non-use
- [Gloaguen et al., Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988), the cost of model-written context files

The undocumented behaviour described here (the built-in auto-dream, the transcript layout) was read from Claude Code 2.1.283 and can change without notice.
