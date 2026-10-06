# Changelog

## 0.1.0

The first public release.

- **The archive.** `dream ingest`, `search`, `show` and `purge`. Every session's prose is kept word for word in one local file, with secrets redacted and the projects you exclude left out.
- **The dream.** `dream run` reads new sessions through your own Claude Code, keeps what you said or agreed to together with the words it came from, and checks every change before accepting it. `dream review` goes over the memories you already had. `dream publish` writes to the memory Claude Code itself loads, and only when you ask.
- **The mod.** It hands each new session what was learned and what was left unfinished, gives Claude the `recall`, `close_thread` and `settle_memory` tools, and adds the `/remcycle` panel.
- **Setup by Claude.** One prompt to paste. Claude does the install and stops for a yes before anything that costs model usage or changes how Claude Code runs.
