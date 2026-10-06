# The daily dream

remcycle runs the dream by itself. The mod runs `dream daily` whenever a session starts. When no daily dream has started in the last 20 hours and there is something new to read, that starts `dream run` in the background, in a process of its own that carries on after the session ends. Nothing needs setting up, and it runs only on days you use Claude Code.

`dream run` is safe to run at any time and as often as you like. It reads only sessions it has not read, keeps what the model made of each one, and changes nothing the gate refuses. A run that is interrupted loses nothing: the next one carries on.

## Its settings

- `dream daily off` and `dream daily on`.
- `dream daily history new`, `week` or `all`: how much from before it began it reads. Until you choose, only new sessions. The `/remcycle` panel asks this once.
- `dream daily limit N`, or `none`: how many sessions one run reads at most. There is no limit until you set one.

Each changes one line in `~/.config/remcycle/config.toml`, so it can be changed there by hand too. The background run writes to `~/.local/share/remcycle/daily.log`, and the panel shows when it last started and why it went wrong if it did. A session the model could not be asked about stays unread and is listed in the log with the reason. If the reason is that `claude` is not signed in, run `claude` in a terminal and sign in.

## A fixed time instead

To have the dream run at a set time, such as overnight, use one of these, and turn the daily dream off with `dream daily off` so the two do not both run.

### Claude Code Desktop

Desktop's scheduler starts a session at a time you choose. It runs only while the app is open and the computer is awake, and a missed run is caught up once on wake.

Create `~/.claude/scheduled-tasks/remcycle-dream/SKILL.md`:

```markdown
---
name: remcycle-dream
description: Run the nightly remcycle dream and report what changed
---

Run `dream run` with the Bash tool and wait for it to finish. It can take a while.
Then tell me, in a few lines: how many sessions were read, what was added or
superseded in each project, and anything listed as a question or withheld.
Do not change any memory yourself.
```

Then set its schedule, folder and permission mode in the app: Code tab, Routines, the task's Edit form. Run it once by hand and allow the Bash call, so later runs do not stop to ask.

### cron

On macOS and Linux. No way of running it at a fixed time has been tried on Windows.

```
30 3 * * * USER=you PATH=/home/you/.local/bin:/usr/bin:/bin /home/you/.local/bin/dream run >> /home/you/.local/share/remcycle/dream.log 2>&1
```

cron starts a command with almost nothing in its environment, so the line carries what the run needs:

- the full path to `dream`, which `command -v dream` prints;
- a `PATH` that holds the folders of `claude` and `git`, and of `gh` if the dream should read merged pull requests;
- `USER`. On macOS, `claude` started without it reports `Not logged in`.

Read the log after the first night. A session the model could not be asked about stays unread and is listed there with the reason.

## Publishing

By default the dream stops at its own copy of memory, and the mod hands each new session what the dream learned. Add `--publish` only when you want the dream to change the memory Claude Code itself holds:

```
dream run --publish
```
