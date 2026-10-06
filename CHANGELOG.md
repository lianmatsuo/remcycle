# Changelog

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
