# The remcycle mod

The part of [remcycle](https://github.com/lianmatsuo/remcycle) that runs inside Claude Code. remcycle is garbage collection for Claude Code's memory: once a day it rereads your sessions, keeps what you decided, and questions the notes that have gone stale.

## What it does

- When a conversation starts, it hands Claude what you have said applies to all your work, what was learned about the project, and what earlier sessions left unfinished.
- It gives Claude three tools: `recall` searches past sessions and reads them back, `close_thread` marks unfinished work done, and `settle_memory` applies a ruling you reached together.
- It adds `/remcycle`, a panel that shows what is remembered and lets you rule on what is in doubt.
- When a session ends, it files what was said into the archive.

## What it needs

The mod does its work by running remcycle's `dream` command, so that has to be installed too:

```bash
uv tool install remcycle
```

Without it the mod loads and does nothing: a conversation starts unchanged, and `recall` answers that remcycle could not be reached.

It runs in Claude Code only, and needs 2.1.287 or later in the terminal or 2.1.286 in the desktop app. On claude.ai and in Cowork it does nothing.

## Exactly what it runs, reads and sends

**The one program it runs.** The mod starts one program, `dream`, remcycle's own command line, which you install separately as above, and nothing else. It always runs `dream` followed by one of these:

- `context`, when a conversation starts, for what to hand the session.
- `daily`, when a session starts, which starts the day's dream in the background if one is due; and `daily on`, `daily off` or `daily history ...` from the panel's switches.
- `status`, when the panel is drawn or refreshed, and before `settle_memory` applies a ruling.
- `search` and `show`, when Claude uses the `recall` tool.
- `close`, `reopen` and `resolve`, when Claude uses `close_thread` or `settle_memory`, or you press one of the panel's buttons.
- `note-read`, after Claude reads one of your memory files, to count the read.
- `ingest`, when a session ends.

**What it reads.** The mod does not read or keep your messages. It sees the session's id and folder, and the path of each file Claude reads, to notice memory files. The `dream` command reads Claude Code's session transcripts and memory folders on your disk: that is how it files sessions into its archive and how the daily dream learns from them.

**What leaves your machine, and where.** The mod makes no network requests. The `dream` command sends one thing out: when the dream runs, once a day unless you turn it off, it sends the prose of the sessions it reads to the model your Claude Code uses, through your own Claude Code (`claude -p`, signed in as you). For a project with unfinished work it also sends the messages of the repository's recent commits and the titles and descriptions of its merged pull requests, which it reads with `git` and, where it is installed and signed in, `gh`. Nothing is sent to remcycle's author or anyone else.

**What each hook does.**

- **When a session starts**, it registers the three tools and `/remcycle`, and runs `dream daily`.
- **When a conversation starts** (`prompt.context`), it adds up to three blocks to what Claude is given: what you have said applies to all your work, what was learned about this project, and what earlier sessions left unfinished. It removes and changes nothing else.
- **For its own three tools** (`tool.call` on `recall`, `close_thread` and `settle_memory`), it answers each one itself by running `dream`. It never answers any other tool in that tool's place.
- **When Claude reads a file** (`tool.call` on `Read`), it lets the read go ahead unchanged. If the file is one of your memory files, it counts the read, and adds a line when that memory names a file that no longer exists.
- **When a session ends**, it runs `dream ingest`.
- **For the panel** (`command.run` on `/remcycle`, `ui.render` and `ui.focus`), it opens and draws remcycle's own panel, and in the desktop app counts a click on one of the panel's buttons as a press. These touch nothing outside the panel.

`claude plugin validate` on this folder lists every event the mod handles and every call it makes.

## Privacy and support

remcycle collects nothing about you and has no server. [PRIVACY.md](https://github.com/lianmatsuo/remcycle/blob/main/PRIVACY.md) says what it keeps on your machine and what leaves it.

Questions go to [Discussions](https://github.com/lianmatsuo/remcycle/discussions) and problems to [an issue](https://github.com/lianmatsuo/remcycle/issues). Report a security problem privately through the repository's Security tab.

## More

The [main README](https://github.com/lianmatsuo/remcycle#readme) explains the whole of remcycle, and [how it works](https://github.com/lianmatsuo/remcycle/blob/main/docs/how-it-works.md) has pictures.

Apache-2.0. remcycle is an independent project and is not affiliated with or endorsed by Anthropic.
