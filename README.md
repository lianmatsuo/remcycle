# remcycle

Nightly memory for Claude Code, built from its own sessions: an archive of what was said, a dream that reconciles it into memory while you are away, and a mod that loads and recalls it. The why and the design are in [docs/intent.md](docs/intent.md).

Only the archive exists so far. It runs on macOS and Linux; Windows is untested.

remcycle is an independent project and is not affiliated with or endorsed by Anthropic.

## Archive

```bash
uv run dream ingest
```

Reads the session transcripts under `~/.claude/projects` into `~/.local/share/remcycle/archive.db`. It keeps your prompts and Claude's prose verbatim, keeps each subagent's final report, shrinks tool calls to their name and target, and drops tool output. It is safe to re-run: unchanged sessions are skipped, and a session whose transcript has been deleted or has shrunk keeps its archived copy.

```bash
uv run dream search retention sweep
```

Best-matching turns first, from the current repository's sessions only. Each hit is labelled `session#turn`.

| Option | Effect |
|---|---|
| `--all-projects` | Search every project |
| `--project DIR` | Search another project |
| `--no-project` | Search sessions started without a folder |
| `--since`, `--until` | Limit to dates, as `YYYY-MM-DD` |
| `--tools` | Include tool calls (files touched, commands run) |
| `--reports` | Include subagents' final reports |

```bash
uv run dream show 7f3a9c2e --first 9 --last 12
```

Prints those turns as archived. The session can be given by the start of its id.

## What the archive holds, and what protects it

The archive stores what you typed into every session, word for word. Three things limit the exposure:

- **Redaction.** Before a turn is stored, text shaped like a credential is replaced with `[redacted]`: common API key and token formats, private key blocks, passwords inside connection URLs, and values assigned to names such as `API_KEY` or `PASSWORD`. This is pattern matching. A secret with no recognisable shape is stored as written.
- **Excluded projects.** Sessions from a listed project, or from anything inside it, are never archived. Excluding a project does not remove sessions that were archived before it was listed.
- **File permissions.** The archive file is readable only by your user.

Nothing is sent anywhere. The archive is one local SQLite file.

## Settings

`~/.config/remcycle/config.toml`:

```toml
exclude = ["~/work/private-client"]
```

Locations follow the environment: `CLAUDE_CONFIG_DIR` for Claude Code's files, `XDG_DATA_HOME` for the archive and `XDG_CONFIG_HOME` for the settings file.

## Compatibility

The archive reads Claude Code's session transcripts, an internal format that can change between releases. It was developed against Claude Code 2.1.280 to 2.1.286. Rows it does not recognise are skipped.

## Development

```bash
uv run pytest
```

Tests sit at three seams: transcript parsing (`tests/test_transcript.py`), the archive's `ingest`, `search` and `show` against a real SQLite file (`tests/test_archive.py`), and settings (`tests/test_settings.py`). The CLI has one end-to-end test.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
