# Setting up remcycle, for Claude

You are setting up remcycle on this person's machine. Work through the steps in order, finishing each before the next.

Two steps are marked **Ask first**: the mod in every session, which also starts the daily dream, and how much history that dream reads. Between them they change how the person's Claude Code runs and spend their model usage. At each one, say in a sentence or two what it will do and what it costs, wait, and go on only with an answer. A no skips that step, and the steps after it still work.

Beyond those two, the steps write only to remcycle's own folders (`~/.config/remcycle` and `~/.local/share/remcycle`) and, in step 2, to uv's tool folders. For any other change you find you need, such as a line in a shell profile, say what it is and wait for a yes.

If a step fails, stop there. Report the error text and which steps are done, and let the person decide what happens next.

## 1. Check what is here

Run `uv --version` and `claude --version`.

- `uv` does not answer: remcycle is installed with it. Give the person the install line for their system from <https://docs.astral.sh/uv/getting-started/installation/>, and carry on once `uv --version` answers.
- `claude` does not answer: the dream reads sessions through that command, so steps 5 to 7 need it. Tell the person, do steps 2 to 4, then go to step 8.
- `claude` answers with a version older than 2.1.287: the mod in step 5 needs that version or later. Tell the person that `claude update` brings it up to date, and run it with their yes. Without it, skip step 5.

Done when both answer, or you have told the person which is missing and what that leaves out.

## 2. Install the command

```bash
uv tool install --reinstall remcycle
```

This installs the latest release from PyPI. `--reinstall` replaces a copy installed earlier from GitHub or from a clone, which would not follow releases.

Done when `dream --version` prints a version.

If `dream` is not found, the folder that `uv tool dir --bin` prints is missing from the path. For the rest of this setup use `"$(uv tool dir --bin)/dream"` in place of `dream`. Tell the person that `uv tool update-shell` adds that folder to their shell profile, and that until it is there the mod cannot find the command either. Run it with their yes.

## 3. Ask what to leave out

Before anything is read, ask the person: are there project folders remcycle should never read? An employer's or a client's work is the usual case.

remcycle files each session under the repository it ran in. So an entry is a repository's top folder, or a folder above several projects. For each folder the person names, the entry is what this prints:

```bash
git -C /the/folder/they/named rev-parse --show-toplevel
```

Where that fails because the folder is not in a repository, the entry is what `pwd -P` prints from inside it. A worktree belongs to the repository it was made from, so enter that repository.

On Windows an entry starts with the drive, as git prints it: `C:/Users/you/work`. A path that the shell prints as `/c/Users/you/work` matches nothing until it is written that way.

Add the entries to `exclude` in `~/.config/remcycle/config.toml`. Create the file if it is missing, and keep everything else it holds:

```toml
exclude = ["/absolute/path/to/a/repository"]
```

Done when the person has answered, including with "none", and every folder they named has its entry.

## 4. Build the archive

```bash
dream ingest
```

This copies what was said in past sessions into one local file. It sends nothing anywhere and is safe to repeat. It prints two lines, like these:

```text
212 added, 0 updated, 0 unchanged, 9 excluded
212 not yet read by the dream
```

Give the person both lines. `excluded` counts the sessions step 3 left out. If they named folders and it says 0, either no session ever ran in those folders or an entry matches nothing. Settle which with the person before step 5, because step 5 is where sessions are sent to the model. After you correct an entry, `dream purge` lists the archived sessions it now covers, and with the person's yes `dream purge --yes` removes them.

Done when the person has seen both lines and, where they named folders, agrees the `excluded` count is right.

## 5. The mod, and with it the daily dream: Ask first

The mod hands each new session what the dream learned, gives Claude a `recall` tool, and adds the `/remcycle` panel. It installs as a Claude Code plugin from remcycle's repository on GitHub, at the tag of the latest release, and Claude Code then loads it in every session.

It also starts the daily dream. Once a day, the first session after 20 hours starts `dream run` in the background, which sends each new session's prose to the model through the person's own Claude Code. For a project with unfinished work it also sends the messages of that repository's recent commits and merged pull requests. The cost grows with the number and length of the sessions: on the author's machine one long session cost about $0.60 at API prices, and 28 sessions cost $13. The person can keep the mod and turn only the dream off, now or later, with `dream daily off`. Checking the mod takes one short model call.

**Install.**

```bash
claude plugin marketplace add lianmatsuo/remcycle
claude plugin install remcycle@remcycle
```

The first line registers the repository as a place to install plugins from, and the second installs the mod from it. `claude plugin list` then shows `remcycle@remcycle` as enabled. Where it is listed already, leave it as it is.

**Confirm.** Done when this prints `WORKING`:

```bash
claude -p 'Call the tool mcp__remcycle__recall with the query "setup". Then reply with one word: WORKING if it answered with search results or said nothing matched, UNREACHABLE if it said remcycle could not be reached, MISSING if you have no tool of that name.' --model haiku --no-session-persistence --allowedTools mcp__remcycle__recall
```

- `UNREACHABLE`: the mod loaded and Claude Code could not run `dream`. Step 2 has the fix.
- `MISSING`: the mod did not load. `claude plugin list` says whether it is installed and enabled, and `claude --version` whether this Claude Code is new enough. Report both.

On an answer you cannot put right, take the mod out again with `claude plugin marketplace remove remcycle`, which also uninstalls it, and report.

The mod loads when a session starts, so the person sees it in the next session they open. In a session that is already open, `/reload-plugins` loads it. The desktop app carries its own copy of Claude Code, and mods work there from 2.1.286.

## 6. How much history the daily dream reads: Ask first

Until the person chooses, the daily dream reads only sessions that end after it first starts. `dream status` prints JSON whose `daily.waiting` says how many unread sessions each choice would read now: `new`, `week` and `all`. Give the person those counts, at the rates in step 5, and offer:

- only new sessions: `dream daily history new`
- the week before as well: `dream daily history week`
- everything: `dream daily history all`

A cap on how many sessions one run reads, for a person with a long history, is `dream daily limit N`. Without one, a run reads every session that is due. Done when `dream status` shows `daily.history` as their choice, or `daily.on` as false after `dream daily off`. If the person would rather not choose now, the panel asks the same question the first time they open it.

## 7. A fixed time instead: only if asked

If the person would rather the dream ran at a set time, such as overnight, [schedule.md](schedule.md) has a task for the Claude desktop app and, for macOS and Linux, a cron line. Set up the one they choose, with this machine's paths in it, and run `dream daily off` so the two do not both run.

- **A scheduled task in the Claude desktop app.** Create it with your scheduled-task tool if you have one, using the prompt schedule.md gives. Otherwise write the file schedule.md shows and tell the person where in the app to set its time. Done when the person confirms the task is listed with the time they chose.
- **A cron line.** Add it after the person's existing cron lines, unless `crontab -l` already shows a `dream run` line. Done when `crontab -l` shows it once. Its output lands in `~/.local/share/remcycle/dream.log`, and `Not logged in` there means cron could not use Claude Code's sign-in, in which case the daily dream or the scheduled task is the way to go.

## 8. Hand over

Tell the person, in a few lines:

- whether the mod and the daily dream are on, and which history the dream reads;
- with the mod on: `/remcycle` in any new session opens the panel, showing what needs their ruling, what was learned and what is left open;
- with the daily dream on: the first session after 20 hours starts it in the background, at most once a day; its output goes to `~/.local/share/remcycle/daily.log` and its reports under `~/.local/share/remcycle/reports/`; `dream daily off`, or the switch in the panel, turns it off;
- `uv tool upgrade remcycle` and `claude plugin update remcycle@remcycle` bring the next release, and the folder this guide was read from is no longer needed;
- three commands are theirs to ask for by name: `dream publish`, which writes into the memory Claude Code itself loads; `dream purge`, which deletes archived sessions; and `dream review`, which costs model usage on a large memory folder;
- [Removing it](reference.md#removing-it) says how to undo each part.

Done when you have said this.
