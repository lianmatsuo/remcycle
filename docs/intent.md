# remcycle: intent and architecture

Status: agreed 2026-10-05. Build has started with the archive (see [Build order](#build-order)).

## Intent

Claude Code on this machine learns from its own sessions once a day. Each day it reviews the latest work, reconciles it against what earlier sessions established, and keeps what was done and what was decided, each with its reason and a pointer back to the session it came from. Finished sessions leave the sidebar but stay recallable through a compact record. You can open a view of what Claude believes, see where each belief came from, and correct it.

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
| Working | Threads a session left open, per project | By the mod, at the start of each conversation | Two weeks, unless a session touches the thread again |
| Long-term | One claim per entry, scoped global or per project | One index line per entry, kept within what Claude Code loads; bodies on recall | Until superseded |
| Archive | Verbatim turns per session, plus what the model made of each | Never; recall only | Permanent; the evidence long-term entries cite |

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

`dream run` is one command, safe to run at any time and to interrupt. Each run works from the sessions it has not yet read.

1. **Ingest** new and changed transcripts into the archive.
2. **Extract.** For each unread session the model proposes claims and open threads, each claim with a passage quoted from the turns it cites. This is the only model step, and what the model made of a session is kept, so no session is paid for twice.
3. **Check.** Code throws out any claim whose quote is not in the turns it cites, and lowers a claim's provenance to what those turns bear out: it carries the person's authority only if the person typed the quoted passage. It also throws out a claim whose name could not be a memory file's: `memory`, which a disk that ignores case takes for the index, and the names Windows keeps for devices.
4. **Reconcile** in code, per slot. The same statement adds evidence. A different one supersedes the entry if it has at least the same authority and is newer. A memory from before remcycle is the exception: a different statement there is always put to the person. A weaker one is put to the person; for a fact it also withholds the entry, since the world may have changed. A new entry needs the person's words or agreement behind it, unless it is a lesson.
5. **Stage and gate.** The changes are made in a throwaway copy of remcycle's own copy of the project's memory. The gate refuses the copy if an entry vanished without a record, if a memory file changed outside any recorded operation, or if the index would be cut off by Claude Code or points at a missing file. A refused copy changes nothing and its sessions stay unread. The same goes for a copy that cannot be moved into place because another program has it open, which Windows does not allow: that project waits for the next dream and the others carry on. What a session said for every project counts as read only once the copy kept for all projects has taken it.
6. **Report** what changed and what waits for a ruling.
7. **Publish**, only when asked: write the accepted memory to the folder Claude Code loads, unless that folder changed while the dream ran.

The index is kept within what Claude Code loads by dropping lines, never by rewriting them: memories from before remcycle that no session has read go first, oldest first. A lesson the person neither stated nor agreed to never gets a line. Dropped entries stay on file, where recall and search find them.

## Plugging into Claude Code

| Part | Mechanism |
|---|---|
| Long-term store | remcycle's own copy of each project's memory folder, a git repository under its data folder. Claude Code's folder is written only on publish |
| Capture during the day | Sessions keep writing memories as they do now. The dream takes them in at the start of each run, and a session's version always wins |
| Dream | `dream daily`, which the mod runs at session start: `dream run` in the background, at most once in 20 hours. `dream run` on any scheduler as well |
| What a conversation is given at its start | Mod, `prompt.context`: what applies everywhere and what the project left open, asked once per conversation so the prompt cache holds |
| Recall | Mod-registered tool backed by `dream search` and `dream show` |
| Verify at use, usage counts | Mod `tool.call` hook on reads of memory files |
| View and rulings | Mod pane, `/remcycle` |
| Ingest at session end | Mod `session.end` hook |
| Later: MCP sources as extra evidence | Mod `mcp.call`. Not built |

## Decisions

Each is dated 2026-10-05 unless noted.

**Build our own dream; keep Claude Code's built-in one off.** Claude Code 2.1.283 contains an undocumented auto-dream behind a server-side flag. It edits live memory in place, hard-deletes what it judges stale with no record, resolves contradictions silently, and sees one project's memory folder at a time. Setting `autoDreamEnabled: false` in user settings keeps a server rollout from starting a second consolidator that writes to the same files.

**The dream never edits live memory.** Rejected: rewriting memory files in place, which the first draft did. Every shipped consolidator isolates its writes (Anthropic's hosted Dreams writes a new store; Letta uses git worktrees), and ACE documents an unguarded rewrite collapsing a context from 18,282 tokens to 122.

**The model extracts; code decides.** Rejected: letting the model make the same/supersedes/contradicts call. Model-run reconciliation is the least reliable step in the systems measured, and Mem0 removed its own.

**The archive keeps verbatim text; the digest is an index over it.** Rejected: a digest-only archive. Extracted summaries lose what is searched for later. Verbatim means human turns and assistant prose; tool calls are reduced to name and target; tool output is dropped. Measured on the development machine's 28 sessions: human turns 0.1 MB and assistant prose 0.7 MB, against 76 MB of tool output, so keeping the prose costs under 1% of the raw size.

**Budget by entry count and relevance, not kilobytes.** Rejected: the first draft's 5 KB index and 2 KB working caps. No size effect has been measured at this scale; count and irrelevance effects have.

**Ranked lexical search first.** Rejected for now: embeddings and graphs. Plain BM25 beat the graph and vector memory systems on the academic benchmark. Embeddings are added only if logs show lexical misses.

**Named remcycle.** Rejected: claude-dream-mod. Claude Code's legal page says its names cannot be used as part of another product's name, and "mod" named one of four parts.

**Apache-2.0.** Copyright Lian Matsuo, stated in `NOTICE`. The licence text itself carries no holder line.

**Hand sessions what was learned; publish only to clean up.** Rejected as the default: writing the dream's memory into the folders Claude Code loads. The mod gives each new session the project's learned memories, so nothing of Claude Code's is touched and turning the mod off undoes it. A handed-over list can only add. Removing a stale memory from what Claude Code loads, or shortening its index, still takes publishing, which stays off.

**Mod first.** Rejected: a stable layer of skills and settings hooks with the mod as an add-on. The consequence is that a release works only with the Claude Code builds whose mod API it was written against, and has to say which.

**The dream's model call runs in the user's own Claude Code, headless.** Rejected: calling the API with a key, or with the user's subscription. Claude Code's terms do not let a third-party tool route requests through someone's subscription, but anyone may run their own Claude Code, so remcycle never handles credentials.

**The model reads before the dream takes its turn.** Reading the sessions is the slow part of a dream, and it only reads memory, so it runs without the turn at changing memory and keeps what the model made of each session. The dream after it works from those and holds the turn only to apply them, so closing a thread or ruling on a question during a run is not refused for minutes. Each session is then read against memory as it stood before the run, not with what an earlier session in the same run added, and reconciling settles the difference.

**Ingest when a session ends; the dream once a day, started by a session.** Ingest needs no model and takes seconds. The dream costs model calls, so a session starts it at most once in 20 hours, in a process of its own that outlives the session. It catches up on whatever it has not read.

**A running daily dream is known by a lock it holds.** The background run holds the lock for as long as it lives, and the system lets go of it when the process ends, however it ends. Rejected: knowing the run by its process number, which the first version did. Once a process is gone the system gives its number out again, so after a restart some other process could hold the number of a run that died, and no daily dream would start until that process ended. The number is still believed for the first minute, before the run has taken its lock.

**The daily dream is on by default.** People install remcycle for the dream, so it runs with nothing to set up and is turned off with `dream daily off` or the panel. Rejected: asking before the first dream and leaving the nightly run as a setup step, which the first release did. Because it now spends the person's plan without a yes, how much from before it began it reads is the person's choice, asked once in the panel and in the guided setup; until then, only new sessions, and a daily dream that starts says how many earlier ones are waiting. A run has no cap unless the person sets one.

**Secrets are redacted at ingest, projects can be excluded, and the archive file is owner-only.** Rejected: storing prompts as written and documenting the risk. Redacting at ingest means a secret never reaches the archive, at the cost of not being recoverable from it. On Windows the folder made for the archive is what is owner-only, and the file takes its permissions from the folder.

**Subagent final reports are kept.** They are `report` turns by `subagent`, treated as `observed`, and searched only on request. Rejected: dropping them, which lost any finding the main session did not repeat, and ingesting full subagent transcripts, which are three times the size of the sessions themselves.

**Sessions started without a folder share one project.** Rejected: each as its own project, which put them out of reach of project-scoped search.

**macOS, Linux and Windows.** The command keeps to what the three share. The files it writes, which are memory, its own records and its settings, are UTF-8 with Unix line ends whatever the system would choose, so a memory file is the same bytes on each. The log of the background run holds whatever that run prints, as the system prints it. A project is known by its folder's path as its own system writes it. Where the systems differ, each has its own few lines behind a check of which system it is: among them taking a lock, starting the background run, and moving or deleting a file that another process has open. remcycle's own folders are the same on all three, `.config/remcycle` and `.local/share/remcycle` under the home folder, so one set of instructions covers them. Rejected: `%APPDATA%` and `%LOCALAPPDATA%`, where Windows programs usually keep such files, which would put a second set of paths in every guide. Rejected: leaving Windows untested, which the first releases did. CI runs the command's tests on all three and the mod's on Linux. remcycle as a whole has not yet been run on a Windows machine.

**The dream works on its own copy of memory.** Rejected: making the folder Claude Code loads a git repository and working on a branch of it. A copy needs no answer to whether Claude Code tolerates a repository there, cannot leave live memory half-changed, and lets the dream commit as itself without waiting on the person's signing key. The accepted copy takes the mirror's place by renaming folders, so a reader finds the old content or the new, and an accept that is cut short is finished by the next command. Rejected: clearing the mirror and copying the files in, which the first version did. A kill partway through left half a copy, and the next run recorded the rest as deleted.

**Every claim must quote the session.** Rejected: trusting the model's own account of who said what. Code checks the quote against the cited turns and sets provenance from who wrote them. On the first real session this threw out one claim of four and kept two of the assistant's own conclusions from being stored as decisions.

**A memory from before remcycle is never replaced without the person's ruling.** Rejected: letting a claim of equal authority supersede it, which the first version did. Such a memory can hold several statements, and a claim replaces it with one. On the first full run a broad note on product naming was replaced by a narrow point about one sign-in screen. A differing claim on such a slot is now always put to the person.

**The index is drawn from one list of lines.** Rejected: editing `MEMORY.md` in place, which the first version did. The store keeps every entry's line in its own file and writes the index sessions load from it: flat within budget, or by topic past a threshold. A line left out for budget comes back when there is room, and a session's own edits to the index are merged in at the next sync.

**Findings about existing memories go to the person, never straight into memory.** The review may be wrong about what is dated, and a memory can hold more than the review saw in it. gbrain's contradiction probe takes the same stance: it reports and the operator decides.

**A lesson nobody endorsed stays out of the index.** It is the model's conclusion, the kind of context that costs more than it helps when always loaded. It stays on file for recall.

**One archive database, scoped at query time.** Rejected for now: a database per project. Search defaults to the current project and crossing projects takes an explicit flag. The file lives at `~/.local/share/remcycle/archive.db`, outside `~/.claude`, which Claude Code manages and sweeps.

**Search covers prose by default.** Tool calls are three quarters of archived turns and outranked the prose that explained them, so they are searched only on request. So are subagent reports.

## The first full run

On the development machine on 2026-10-05 the dream read 28 sessions through Sonnet, for $13.43 of use at API prices. It wrote 80 entries: 48 from the person's own words, 5 the person agreed to, and 27 lessons nobody endorsed, which stay out of the index. It rejected 19 claims, some for quotes that were not in the turns cited and the rest for having nothing the person said behind them. It noted 66 open threads. Every project's changes passed the gate.

Two faults showed up and were fixed: the supersession described above, and five statements that guessed a pronoun for the person, which the extraction prompt now forbids.

## Facts this design depends on

- Auto memory lives at `~/.claude/projects/<project>/memory/`, keyed by git repository and shared across worktrees. `MEMORY.md` is cut at 200 lines or 25 KB at load. The engine stamps `modified` on each write.
- Memory is read once at session start; mid-session edits do not apply until `/clear`, `/compact` or restart.
- Transcripts are swept after `cleanupPeriodDays` (default 30). Transcripts created by the desktop app are exempt unless `desktopSessionCleanupPeriodDays` is set.
- A desktop local scheduled task fires only while the app is open and the Mac is awake.
- Session transcripts sit at `~/.claude/projects/<project>/<session>.jsonl`, with subagent transcripts nested under `<session>/subagents/`. On the development machine on 2026-10-05 that was 28 sessions (245 MB) and 269 subagent transcripts (759 MB).

## Build order

1. **Archive and search.** Built: `dream ingest`, `dream search`, `dream show`, `dream purge`, with redaction, excluded projects and settings.
2. **The dream.** Built: `dream run`, `dream queue`, `dream resolve`, `dream close`, `dream reopen`, `dream publish`, with the per-session digest kept in the archive.
3. **Mod.** Built: start-of-conversation context, the recall tool, the close_thread tool, ingest at session end, the warning on stale memories, read counts.
4. **Pane and rulings.** Built: `/remcycle`.
5. **Outside sources.** Built for git and GitHub: commits and merged pull requests close threads finished outside a session. Slack, Jira and Notion are not built: they can be read only from inside a session, through its connectors.

Built after the first full run, from the unbuilt list and from a comparison with gbrain:

- **Aliases.** A claim finds its entry by slot, alias, another spelling of either, or the same statement. This is gbrain's alias lookup applied to the dream's weakest step, the model naming slots.
- **Recall.** `dream search` matches memory entries by name, alias and words, ranks what the person typed first, and fuses several phrasings of one question by reciprocal rank.
- **The gate's fourth check.** Each entry has a probe question, written by the model with the claim or in the review. The staged index is refused if it leads a model to the right memory less often than the current one.
- **A two-level index.** Past 60 entries the index lists topics, each with a page of entry lines. The store keeps every line in one list and draws whichever index fits, so lines are moved and never rewritten.
- **Pruning.** Facts about files that have gone are withheld. An entry the person corrected the assistant for following leaves the index. Both wait for a ruling.
- **The review.** `dream review` reads existing memories, gives each a topic and a probe, and questions the dated, the repeated and the contradictory, on quoted evidence only.

Built from a second comparison with gbrain's dream cycle:

- **Sessions in brief.** The dream already wrote a summary of each session it read and nothing used it. Search now ends with what each session it found was, and `dream show --summary` gives one session's. The recall tool, given a session and no turns, returns that summary where it used to return every turn. Rejected: summaries in place of the turns, for the reason the archive keeps verbatim text.
- **Need.** A memory is needed once for every session it came up in, and once more each time a search brings it up or a session reads its file. That one count orders equal search matches, decides which index lines are dropped last, and orders what a session is handed, where something said in the last two weeks counts once more. Rejected: handing over the last two weeks' memories ahead of everything else, which the first version did. A busy fortnight then crowded out a memory that had come up in session after session. Rejected: counting every cited span of turns, which counted one session several times. Rejected: keeping the count inside the dream's copy, where the first version put it. Sessions add to the count at any moment and the dream replaces that folder whole, so a search could make a dream's step fail. Rejected: questioning memories that are never looked up. No look-up had been recorded on the development machine when this was built, so it would have questioned every memory.
- Not taken: reading mail, meetings and notes, and pages about people and companies. The first is outside what remcycle reads, and the second grows every night, which is the accumulation the design exists to prevent.

Still not built:

- Vector search and a reranker. Both need an embedding model or a service, so they would be an optional backend.
- Measuring extraction against sessions labelled by hand.
- A command to remove sessions archived before their project was excluded.
- MCP sources as extra evidence.

## Open

- A foreground subagent's report is read from the result of its Agent call. Only two such rows existed on the development machine to check that shape against; background reports, which arrive as task notifications, are well covered.
- Excluding a project stops new sessions being archived. There is no command yet to remove sessions archived before the project was listed.
- Redaction is pattern matching and misses secrets with no recognisable shape.
- A change to the extraction rules re-derives archived sessions, but only those whose transcripts still exist.
- A session's project is its git repository, and a worktree is traced to its repository only while the worktree still exists. Sessions from worktrees deleted before their first ingest keep the worktree path as their project.
- Orchestrated sessions: a prompt sent by another agent or a script is not the person's word, so a decision that reached the session that way is treated as the assistant's own and is not stored. This is strict by design and loses some real decisions.
- A session edit to a memory the dream wrote makes it a memory without provenance again.

## Sources

- [Claude Code: legal and compliance](https://code.claude.com/docs/en/legal-and-compliance), on names and on whose credentials a tool may use
- [Claude Code: memory](https://code.claude.com/docs/en/memory), [prompt caching](https://code.claude.com/docs/en/prompt-caching), [desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)
- [Anthropic: Dreams for Managed Agents](https://platform.claude.com/docs/en/managed-agents/dreams), the copy, review, swap pattern
- [GitHub: building an agentic memory system for Copilot](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/), citations verified at use and expiry on non-use
- [Gloaguen et al., Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988), the cost of model-written context files

The undocumented behaviour described here (the built-in auto-dream, the transcript layout) was read from Claude Code 2.1.283 and can change without notice.
