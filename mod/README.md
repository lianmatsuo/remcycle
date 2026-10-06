# The remcycle mod

The part of [remcycle](https://github.com/lianmatsuo/remcycle) that runs inside Claude Code. remcycle gives Claude Code a nightly memory, built from its own sessions.

## What it does

- When a conversation starts, it hands Claude what you have said applies to all your work, what was learned about the project, and what earlier sessions left unfinished.
- It gives Claude three tools: `recall` searches past sessions and reads them back, `close_thread` marks unfinished work done, and `settle_memory` applies a ruling you reached together.
- It adds `/remcycle`, a panel that shows what is remembered and lets you rule on what is in doubt.
- When a session ends, it files what was said into the archive.

## What it needs

The mod does its work by running remcycle's `dream` command, so that has to be installed too:

```bash
uv tool install git+https://github.com/lianmatsuo/remcycle
```

Without it the mod loads and does nothing: a conversation starts unchanged, and `recall` answers that remcycle could not be reached.

It needs Claude Code 2.1.287 or later in the terminal, or 2.1.286 in the desktop app.

## What it reaches

A mod runs with your permissions. This one starts one program, `dream`, and makes no network requests. `claude plugin validate` on this folder lists every event it handles and every call it makes.

The `dream` command keeps everything on your machine. What leaves is what the nightly dream sends to the model through your own Claude Code, once you have turned that on: the prose of your sessions and, for a project with unfinished work, the messages of its recent commits and the titles and descriptions of its merged pull requests.

## More

The [main README](https://github.com/lianmatsuo/remcycle#readme) explains the whole of remcycle, and [how it works](https://github.com/lianmatsuo/remcycle/blob/main/docs/how-it-works.md) has pictures.

Apache-2.0. remcycle is an independent project and is not affiliated with or endorsed by Anthropic.
