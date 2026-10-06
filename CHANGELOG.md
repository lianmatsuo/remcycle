# Changelog

## 0.4.0

- **Windows.** The `dream` command and the mod now run on Windows as well as macOS and Linux. This is new: the command's tests pass on Windows, but remcycle has not yet been run there with Claude Code, so the mod and the daily dream are untried on it. It keeps its files in the same folders as elsewhere, `.config\remcycle` and `.local\share\remcycle` under your user folder.
- **A daily dream that died no longer holds up the next one.** A dream cut short, by a restart for one, could read as still running for as long as some other program had its process number, and no new one started until that program ended.
- **One project no longer stops a dream.** A project whose memory the system would not let the dream move into place is left for the next dream, with the reason in the report, and the other projects are still read.
- **What you said for all your work is not lost when it cannot be saved.** A session that said something for every project now stays unread until that has been accepted.
- **Excluded folders.** An entry in `exclude` written with a closing slash, such as `"/work/client/"`, now leaves out the sessions run in that folder itself, and not only those in the folders inside it.
- **Memory names.** The dream could name a memory `memory`, and on a Mac's disk that was written over the index, `MEMORY.md`. Such a name is now refused.

### Upgrading

```bash
uv tool upgrade remcycle
claude plugin update remcycle@remcycle
```

If a daily dream is running while you upgrade, the panel says it stopped until it has finished.

## 0.3.0

- **remcycle is on PyPI.** `uv tool install remcycle` installs the `dream` command, and `uv tool upgrade remcycle` brings the latest release.
- **The mod follows releases.** It is installed from the tag of the latest release, so `claude plugin update remcycle@remcycle` brings a release and nothing in between.
- **`dream --version`** says which version of the command is installed.

### Upgrading from 0.2.0 or earlier

The command you have follows the repository, not releases. Replace it once, and update the mod:

```bash
uv tool install --reinstall remcycle
claude plugin update remcycle@remcycle
```

If Claude set remcycle up for you from a folder such as `~/remcycle`, the mod was installed from that folder as well. Move it to the repository on GitHub, after which the folder can be deleted:

```bash
claude plugin marketplace remove remcycle
claude plugin marketplace add lianmatsuo/remcycle
claude plugin install remcycle@remcycle
```

## 0.2.0

- **The daily dream is on by default.** Once a day, the first session after 20 hours starts the dream in the background, with no scheduler to set up. The panel asks once how much history it should read, shows when it last ran and why it went wrong if it did, and turns it off. `dream daily` makes the same changes from the command line.
- **Memory stays free while a dream reads.** The model reads the sessions before the dream takes its turn at changing memory, so closing a thread or ruling on a question during a run works, and a close that does collide says to try again.
- **Lighter model calls.** The dream's calls to Claude Code no longer carry the descriptions of your MCP servers and skills. One measured call went from about 183,000 tokens of context to about 500.

## 0.1.0

The first public release.

- **The archive.** `dream ingest`, `search`, `show` and `purge`. Every session's prose is kept word for word in one local file, with secrets redacted and the projects you exclude left out.
- **The dream.** `dream run` reads new sessions through your own Claude Code, keeps what you said or agreed to together with the words it came from, and checks every change before accepting it. `dream review` goes over the memories you already had. `dream publish` writes to the memory Claude Code itself loads, and only when you ask.
- **The mod.** It hands each new session what was learned and what was left unfinished, gives Claude the `recall`, `close_thread` and `settle_memory` tools, and adds the `/remcycle` panel.
- **Setup by Claude.** One prompt to paste. Claude does the install and stops for a yes before anything that costs model usage or changes how Claude Code runs.
