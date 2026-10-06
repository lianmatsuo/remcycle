# Setting up remcycle, for Claude

You are setting up remcycle on this person's machine. Work through the steps in order, finishing each before the next. `CLONE` below stands for the absolute path of the folder you cloned: the parent of this file's folder.

Three steps are marked **Ask first**: the first dream, the mod in every session, and the nightly run. Each spends the person's model usage or changes how their Claude Code runs. At each one, say in a sentence or two what it will do and what it costs, wait, and go on only with a yes. A no skips that step, and the steps after it still work.

Beyond those three, the steps write only to `CLONE`, to remcycle's own folders (`~/.config/remcycle` and `~/.local/share/remcycle`) and, in step 2, to uv's tool folders. For any other change you find you need, such as a line in a shell profile, say what it is and wait for a yes.

If a step fails, stop there. Report the error text and which steps are done, and let the person decide what happens next.

## 1. Check what is here

Run `uv --version` and `claude --version`.

- `uv` does not answer: remcycle is installed with it. Give the person the install line for their system from <https://docs.astral.sh/uv/getting-started/installation/>, and carry on once `uv --version` answers.
- `claude` does not answer: the dream reads sessions through that command, so steps 5 to 7 need it. Tell the person, do steps 2 to 4, then go to step 8.
- `claude` answers with a version older than 2.1.287: the mod in step 6 needs that version or later. Tell the person that `claude update` brings it up to date, and run it with their yes. Without it, skip step 6.

Done when both answer, or you have told the person which is missing and what that leaves out.

## 2. Install the command

From inside `CLONE`:

```bash
uv tool install --editable .
```

Done when `dream --help` lists its commands.

If `dream` is not found, the folder that `uv tool dir --bin` prints is missing from the path. For the rest of this setup use `"$(uv tool dir --bin)/dream"` in place of `dream`. Tell the person that `uv tool update-shell` adds that folder to their shell profile, and that until it is there the mod cannot find the command either. Run it with their yes.

## 3. Ask what to leave out

Before anything is read, ask the person: are there project folders remcycle should never read? An employer's or a client's work is the usual case.

remcycle files each session under the repository it ran in. So an entry is a repository's top folder, or a folder above several projects. For each folder the person names, the entry is what this prints:

```bash
git -C /the/folder/they/named rev-parse --show-toplevel
```

Where that fails because the folder is not in a repository, the entry is what `pwd -P` prints from inside it. A worktree belongs to the repository it was made from, so enter that repository.

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

## 5. The first dream: Ask first

The dream sends each unread session's prose to the model, through the person's own Claude Code. For a project with unfinished work it also sends the messages of that repository's recent commits and merged pull requests. What it learns goes into its own copy of memory, which no session sees until step 6.

The cost grows with the number and length of the sessions. On the author's machine one long session cost about $0.60 at API prices, and 28 sessions cost $13.

Give the person the unread count from step 4 and those figures, and offer:

- a trial on the three oldest sessions: `dream run --limit 3`
- everything: `dream run`

A session takes a minute or more to read, so a full run can outlast one shell call. Start it in the background and wait for it to end. A run that is cut short loses nothing: the same command carries on from where it stopped.

The run ends with a report. Done when the report's first line gives the number of sessions read and no line begins `- Could not read`. Each such line names a session that stays unread and why. `Not logged in` means the `claude` command is not signed in: the person runs `claude` in a terminal and signs in, then you run the same command again.

Relay what the report added, and what `dream queue` shows waiting for the person's ruling.

## 6. The mod in every session: Ask first

The mod hands each new session what the dream learned, gives Claude a `recall` tool, and adds the `/remcycle` panel. It installs as a Claude Code plugin from this clone, and Claude Code then loads it in every session. Checking it takes one short model call.

**Validate.** `claude plugin validate CLONE/mod` has to pass.

**Install.**

```bash
claude plugin marketplace add CLONE
claude plugin install remcycle@remcycle
```

The first line registers the clone as a place to install plugins from, and the second installs the mod from it. `claude plugin list` then shows `remcycle@remcycle` as enabled. Where it is listed already, leave it as it is.

**Confirm.** Done when this prints `WORKING`:

```bash
claude -p 'Call the tool mcp__remcycle__recall with the query "setup". Then reply with one word: WORKING if it answered with search results or said nothing matched, UNREACHABLE if it said remcycle could not be reached, MISSING if you have no tool of that name.' --model haiku --no-session-persistence --allowedTools mcp__remcycle__recall
```

- `UNREACHABLE`: the mod loaded and Claude Code could not run `dream`. Step 2 has the fix.
- `MISSING`: the mod did not load. `claude plugin list` says whether it is installed and enabled, and `claude --version` whether this Claude Code is new enough. Report both.

On an answer you cannot put right, take the mod out again with `claude plugin marketplace remove remcycle`, which also uninstalls it, and report.

The mod loads when a session starts, so the person sees it in the next session they open. In a session that is already open, `/reload-plugins` loads it. The desktop app carries its own copy of Claude Code, and mods work there from 2.1.286.

## 7. The nightly run: Ask first

The nightly run is `dream run` on a timer, and each night it reads every session that is still unread. Run `dream ingest` again and give the person its second line: that is what the first night will read, at the rates in step 5. If that is more than they want read in one night, schedule `dream run --limit N` with a number they choose, and the backlog is worked off over the nights that follow.

[schedule.md](schedule.md) has the two ways to schedule it. Ask which the person wants and at what time.

- **A scheduled task in the Claude desktop app.** Create it with your scheduled-task tool if you have one, using the prompt schedule.md gives. Otherwise write the file schedule.md shows and tell the person where in the app to set its time. Done when the person confirms the task is listed with the time they chose.
- **A cron line.** Build the line as schedule.md says, with this machine's paths in it. Add it after the person's existing cron lines, unless `crontab -l` already shows a `dream run` line. Done when `crontab -l` shows it once. Tell the person the first night's output lands in `~/.local/share/remcycle/dream.log`, and that `Not logged in` there means cron could not use Claude Code's sign-in, in which case the scheduled task is the way to go.

## 8. Hand over

Tell the person, in a few lines:

- which of the first dream, the mod and the nightly run are now on, and which they skipped;
- with the mod on: `/remcycle` in any new session opens the panel, showing what needs their ruling, what was learned and what is left open;
- with the nightly run on: when it first runs, and that each night's report is saved under `~/.local/share/remcycle/reports/`;
- `CLONE` has to stay where it is, because the command and the mod run from it, and `git pull` there updates both;
- three commands are theirs to ask for by name: `dream publish`, which writes into the memory Claude Code itself loads; `dream purge`, which deletes archived sessions; and `dream review`, which costs model usage on a large memory folder;
- [Removing it](reference.md#removing-it) says how to undo each part.

Done when you have said this.
