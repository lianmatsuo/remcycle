# Running the dream every night

`dream run` is safe to run at any time and as often as you like. It reads only sessions it has not read, keeps what the model made of each one, and changes nothing the gate refuses. A run that is interrupted loses nothing: the next one carries on.

Nothing here is set up for you. Pick one.

## Claude Code Desktop

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

## cron

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
