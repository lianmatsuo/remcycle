# remcycle

Nightly memory for Claude Code, built from its own sessions.

Claude Code starts every session fresh, and the notes it keeps about your projects pile up until the old ones are wrong. remcycle keeps everything you and Claude said, and while you are away it reads the new sessions, keeps what you actually decided, and questions the rest. The next session starts knowing it.

```mermaid
flowchart LR
    work(["You and<br/>Claude Code"])
    archive[("Archive<br/>word for word")]
    dream{{"The dream<br/>while you are away"}}
    memory["Memory<br/>short, sourced notes"]
    work -- "saved" --> archive
    archive -- "what is new" --> dream
    dream -- "what you decided" --> memory
    memory -- "next session" --> work
    dream -. "asks if unsure" .-> work
```

[How it works](docs/how-it-works.md) explains each part in plain words, with pictures.

## What you get

- **Sessions that start informed.** Each new session is handed what you have said applies to all your work, what was learned about the project, and what earlier sessions left unfinished.
- **Memory you can trust.** Every memory points at the words it came from. Something only Claude concluded is never loaded as if you had said it, and a note you already had is never replaced without asking you.
- **A list that stays short.** Stale, repeated and contradictory notes are found and put to you, so what loads at the start of a session does not keep growing.
- **Recall.** Claude can search everything that was said in past sessions and read it back word for word.
- **A panel.** `/remcycle` shows what needs your ruling, what was learned lately and what is left open.
- **Everything on your machine.** The archive is one local file. The only thing that leaves is what the dream sends to the model, through your own Claude Code.

## Set it up by asking Claude

Paste this into Claude Code:

```text
Set up remcycle for me. Clone https://github.com/lianmatsuo/remcycle into ~/remcycle, or run git pull there if it is already cloned. Then read docs/setup-with-claude.md in that folder and follow it step by step.
```

Claude installs the command, asks which projects to leave out, and builds the archive. It stops and asks you before each of the three steps that cost model usage or change how Claude Code runs: the first dream, loading the mod in every session, and the nightly run. You can say no to any of them and turn it on later.

The folder has to stay where it is afterwards, because remcycle runs from it. [Removing it](docs/reference.md#removing-it) says how to undo each part.

To do it by hand, see [Install by hand](docs/reference.md#install-by-hand).

## What it does without asking

Out of the box it only saves what was said and answers when you ask it something. The nightly dream and the mod run by themselves once you turn them on.

Four things never happen without you: writing into the memory Claude Code itself loads, retiring or replacing a note you already had, deleting anything from the archive, and reading a project you have excluded.

## Find out more

- [How it works](docs/how-it-works.md): the idea, in plain words and pictures.
- [Reference](docs/reference.md): every command and setting.
- [Running it every night](docs/schedule.md): the scheduled task and the cron line.
- [Design notes](docs/intent.md): why it is built this way, and what was rejected.

## Status

This is early software, written for one person's machine and released as it stands. It reads Claude Code's transcripts and, when you ask it to, writes to the memory Claude Code loads, so read what a command will do before you run it.

It runs on macOS and Linux; Windows is untested. The mod was written against Claude Code 2.1.286, and its API is early access, so it can break between releases.

remcycle is an independent project and is not affiliated with or endorsed by Anthropic.

## Development

```bash
uv run pytest
```

The Python tests sit at these seams: transcript parsing, the archive against a real SQLite file, settings, reconciliation as pure functions, the memory store against folders in Claude Code's format, extraction through a stand-in for the model, the gate, and whole dream runs against temporary folders. They run on macOS and Linux for every push.

```bash
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin test mod
```

The mod's tests run in Claude Code's own plugin test kit, so they need Claude Code installed and are not part of the automatic run.

## Licence

Apache-2.0. See [LICENSE](LICENSE). Copyright 2026 Lian Matsuo, as [NOTICE](NOTICE) says.
