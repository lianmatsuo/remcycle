# Privacy

remcycle collects nothing about you. It has no server, no account and no telemetry, and its author receives no data from it.

## What it keeps, on your machine

- **The archive**, `~/.local/share/remcycle/archive.db`: what you typed into each Claude Code session and what Claude wrote back, word for word. Tool output is left out, and text shaped like a password or an API key is blanked before it is stored. The file is readable only by your user.
- **The dream's copy of memory**, its reports and the backups it takes before publishing, beside the archive.
- **Your settings**, `~/.config/remcycle/config.toml`.

## What leaves your machine

Only what the dream sends to the model, and only through your own Claude Code, under your own Claude account and the terms you already have with Anthropic. That is the prose of the sessions it reads and, for a project with unfinished work, the messages of its recent commits and the titles and descriptions of its merged pull requests. The mod itself makes no network requests.

The daily dream sends this once a day. `dream daily off` stops it.

## Keeping things out, and removing them

- List a project folder under `exclude` in the settings file and its sessions are never archived. `dream purge` removes sessions archived before you excluded it.
- Deleting `~/.local/share/remcycle` and `~/.config/remcycle` removes everything remcycle stored. [Removing it](docs/reference.md#removing-it) covers each part.

## Contact

Questions go to [Discussions](https://github.com/lianmatsuo/remcycle/discussions) and problems to [an issue](https://github.com/lianmatsuo/remcycle/issues). Report anything that could expose someone's data privately, as [SECURITY.md](SECURITY.md) says.
