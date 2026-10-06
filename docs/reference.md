# Reference

Every command and setting, in the order you would meet them. For the idea behind it, read [how it works](how-it-works.md) first.

## Install by hand

The quicker way is to [let Claude set it up](setup-with-claude.md). By hand:

It needs Python 3.12 or later, [uv](https://docs.astral.sh/uv/), git, and Claude Code signed in on the same machine. It has no other dependencies. Clone this repository somewhere it can stay, because the mod runs from the clone, and from inside it:

```bash
uv tool install --editable .
```

That puts the `dream` command on your `PATH`, and keeps it in step with the clone when you `git pull`. The examples below use `uv run dream`, which works from the clone without installing.

## Archive

```bash
uv run dream ingest
```

Reads the session transcripts under `~/.claude/projects` into `~/.local/share/remcycle/archive.db`. It keeps your prompts and Claude's prose verbatim, keeps each subagent's final report, shrinks tool calls to their name and target, and drops tool output. It is safe to re-run: unchanged sessions are skipped, and a session whose transcript has been deleted or has shrunk keeps its archived copy. It ends by saying how many archived sessions the dream has not read yet.

```bash
uv run dream search retention sweep
```

Matching memories first, found by name, alias or words, then the best-matching turns from the current repository's sessions. Each turn is labelled `session#turn`. What you typed ranks above what Claude wrote. Last comes what each session it found was, in a few lines: the summary the dream wrote when it read that session.

Among memories that match alike, the one needed most often comes first. A memory is needed each time you say it in a session and each time a search or a session looks it up, so a search that brings a memory up counts towards that.

| Option | Effect |
|---|---|
| `--all-projects` | Search every project |
| `--project DIR` | Search another project |
| `--no-project` | Search sessions started without a folder |
| `--since`, `--until` | Limit to dates, as `YYYY-MM-DD` |
| `--also "PHRASING"` | Another way of asking the same thing; repeatable. Turns that more phrasings find rank higher |
| `--tools` | Include tool calls (files touched, commands run) |
| `--reports` | Include subagents' final reports |

```bash
uv run dream show 7f3a9c2e --first 9 --last 12
```

Prints those turns as archived. The session can be given by the start of its id.

```bash
uv run dream show 7f3a9c2e --summary
```

Prints what the session was in a few lines, in place of its turns: its day, title and length, and the summary the dream wrote. A session the dream has not read as it now stands has no summary yet.

## Dream

```bash
uv run dream run
```

Brings the archive up to date, then reads every session it has not read and reconciles what each established into memory. It prints a report and saves it under `~/.local/share/remcycle/reports/`.

The model is used for one step only: reading a session and proposing claims, each with a passage quoted from the turns it cites. Everything after that is code:

- A claim whose quote is not in the turns it cites is thrown out.
- A claim carries your authority only if you typed the quoted passage.
- A new entry needs your words or your agreement behind it, unless it is a lesson. A lesson nobody endorsed is kept on file but left out of the index every session loads.
- The same statement heard again adds evidence. A different one replaces the entry if it has at least the same authority and is newer. A weaker one is put to you instead.
- A memory written before remcycle is never replaced without your ruling. A claim that differs from it is put to you.
- A claim lands on the entry it is about even when the model names it differently: by another spelling, a known alias, or because it says what the entry already says.
- A fact about a file is withheld once that file is gone from the repository, and an entry you corrected Claude for following leaves the index. Both wait for your ruling.
- A thread a session left open is closed when a later session finishes it, or when a commit or merged pull request in the project's repository did. The model is asked which changes finished which threads, and a change counts only if its own words, quoted, show it. Commits are read with git and pull requests with `gh`, where it is installed and signed in.

The dream works on its own copy of each project's memory under `~/.local/share/remcycle/memory/`, staged and checked before it is accepted. One of the checks asks the model: given only the index, would you still open the right memory for each entry's probe question? A change that makes the index worse at that is refused.

Up to 60 entries the index is one line per entry. Past that it lists topics, and each topic has a page of its entries' lines. A topic's line names every memory on its page while the index has room. Lines are moved between the two, never rewritten. A review keeps the index one line per entry if, listed by topic, it leads the model to the right memory for fewer of the entries' probe questions. The memory Claude Code loads is not touched unless you pass `--publish`, and publishing is refused if that memory changed while the dream ran.

With the mod loaded you do not need to publish for sessions to get what the dream learned: the mod hands each new session those memories, up to about 4,000 characters. What you said in the last two weeks comes first, newest first; older memories follow, the most needed first, so what is left out when room runs short was said once, long ago. Publishing is for changing what Claude Code itself holds, which a handed-over list cannot do: taking a retired memory out of what it loads, or shortening an index that is nearly full.

```bash
uv run dream publish
```

Lists what publishing this project would add to, replace in and remove from the folder Claude Code loads, and writes nothing. `dream publish --yes` writes it. First it takes in whatever sessions wrote there since the dream last looked, so a session's work is never overwritten or removed. The files that were there before are copied to `~/.local/share/remcycle/backups/` before anything changes.

```bash
uv run dream queue
```

Lists what is waiting for you. Settle one with `dream resolve SLOT --accept` or `--keep`. Accepting takes the new claim, or retires the entry where that was the question. A retired entry leaves sessions but stays on file.

A thread a session left open is closed with `dream close SLOT`, and `dream reopen SLOT` puts it back. A closed thread is kept for two weeks with who closed it and why, and reopens by itself if a later session reports it unfinished.

```bash
uv run dream review
```

Reads the memories this project already has, which the dream otherwise touches only when a session says something about them. It gives each a topic and a probe question, and puts to you any that look dated, repeat each other or disagree, each backed by words quoted from the memories themselves. It changes and removes nothing. A large folder is reviewed 20 memories at a time; a pass the model fails on is left for the next run and the passes that worked are kept. On the development machine, reviewing 19 memories cost $2.28 at API prices with Sonnet, so try `effort = "low"` or `model = "haiku"` in the settings file before a large folder.

The model step runs through your own Claude Code, headless (`claude -p`), with no tools, no settings, no plugins and no transcript. It uses your plan's usage: on the development machine one long session cost about $0.58 at API prices with Sonnet. Choose the model with `model = "haiku"` in the settings file or `--model`.

## Mod

`mod/` is a Claude Code mod (function hooks, early access). Once loaded it:

- gives each new conversation what you have said applies to all your work, what the dream has learned about the project that Claude Code's own memory does not hold, and what earlier sessions in the project left open;
- gives Claude a `recall` tool that searches memory and the archive, takes several phrasings at once, gives a past session in brief, and reads turns back word for word;
- gives Claude a `close_thread` tool, and the names of the open threads, so a conversation closes a thread when it finishes one. It has to say what finished it, and the next dream checks the claim against that conversation's own transcript and reopens the thread if the work was not done;
- brings the archive up to date when a session ends;
- warns Claude when it reads a memory about a file that no longer exists;
- adds `/remcycle`, a pane showing at a glance what waits for your ruling, how many memories are in use and where they came from, how full the index is, what the dream learned lately, what sessions left open and what was closed lately. Its buttons take a claim, keep or retire an entry, and close or reopen a thread. "Add to chat" puts a question in your prompt box instead, to talk it through; Claude then applies what you decide with a `settle_memory` tool. It names the other projects with questions waiting and can switch to any of them.

It calls the `dream` command, so that has to be on your `PATH`:

```bash
uv tool install --editable .
```

```bash
claude --plugin-dir mod
```

That loads it for one session. To load it in every session, name the folder in the `env` block of `~/.claude/settings.json`:

```json
"env": {
  "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/remcycle/mod",
  "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
}
```

It was written against Claude Code 2.1.286. The mod API is early access and can change between releases.

## Running it every night

[schedule.md](schedule.md) has a ready-made task for Claude Code Desktop's scheduler and a cron line.

## What the archive holds, and what protects it

The archive stores what you typed into every session, word for word. Three things limit the exposure:

- **Redaction.** Before a turn is stored, text shaped like a credential is replaced with `[redacted]`: common API key and token formats, private key blocks, passwords inside connection URLs, and values assigned to names such as `API_KEY` or `PASSWORD`. This is pattern matching. A secret with no recognisable shape is stored as written.
- **Excluded projects.** Sessions from a listed project, or from anything inside it, are never archived. Sessions archived before the project was listed stay until you remove them: `dream purge` lists them and `dream purge --yes` deletes them and rewrites the file so their text is gone from it. Memory the dream already drew from them, under `~/.local/share/remcycle/memory/`, is not removed.
- **File permissions.** The archive file is readable only by your user.

The archive is one local SQLite file and ingest sends nothing anywhere. The dream is different: it sends the prose of each unread session, after redaction, to the model through your own Claude Code, as any session would.

## Settings

`~/.config/remcycle/config.toml`:

```toml
exclude = ["~/work/private-client"]
model = "sonnet"
effort = "low"
```

`model` and `effort` apply to every model step. Leave `effort` out to use Claude Code's default.

Locations follow the environment: `CLAUDE_CONFIG_DIR` for Claude Code's files, `XDG_DATA_HOME` for the archive and `XDG_CONFIG_HOME` for the settings file.

## Removing it

Each part comes out by itself.

- **The mod.** Take the two entries out of the `env` block of `~/.claude/settings.json`. If Claude did the setup, the file as it was before is at `~/.claude/settings.json.before-remcycle`.
- **The nightly run.** Delete the `remcycle-dream` task in the desktop app, or the `dream run` line in `crontab -e`.
- **The command.** `uv tool uninstall remcycle`.
- **What it kept.** `~/.local/share/remcycle` holds the archive, the dream's copy of memory, the reports and the backups. `~/.config/remcycle` holds the settings. Deleting both removes everything remcycle stored.

Claude Code's own memory is as you left it unless you published to it. `dream publish` keeps the files it replaces under `~/.local/share/remcycle/backups/`, so copy back what you want from there before you delete that folder.

## Compatibility

The archive reads Claude Code's session transcripts, an internal format that can change between releases. It was developed against Claude Code 2.1.280 to 2.1.286. Rows it does not recognise are skipped.
