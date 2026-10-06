# Working on remcycle

The working guide for anyone changing this repository, person or coding agent. `CLAUDE.md` loads this file.

remcycle is two programs in one clone: the `dream` command (`src/dream`, Python, standard library only) and a Claude Code mod (`mod/`, TypeScript) that calls it.

## The gate

```bash
scripts/check
```

It runs everything CI runs: the Python tests, then the mod's manifest, types and tests. A change is done when it passes. `scripts/check python` and `scripts/check mod` run one half.

In the pull request, say what you ran and what it printed. If part of the gate could not run, say which part.

## Real data stays out of development

The machine you are on may hold its owner's real archive and memory, under `~/.local/share/remcycle` and `~/.claude`. Development works on copies and throwaway folders.

- Try a change through the tests. To run the command by hand, point it at temporary folders by setting `XDG_DATA_HOME`, `XDG_CONFIG_HOME` and `CLAUDE_CONFIG_DIR`.
- `dream run` and `dream review` spend the owner's model usage. `dream publish --yes` and `dream purge --yes` change or delete what the owner keeps. Run these against the real folders only when the owner asks for that command by name.
- The tests stand in for the model, so none of them spends usage. Keep it that way.

## How changes are made

- Write the failing test first, at one of the seams the README lists under Development, through the public interface. Watch it fail for the reason you expect, then make it pass.
- Before changing how memory is written, how a claim is accepted, or what is sent to the model, read `docs/intent.md`. It records each decision beside the alternative that was rejected. A change that reverses one updates that file in the same pull request.
- Python: dataclasses for structured data, `pathlib` for paths, `raise ... from e`, never a bare `except`.
- Comments and docs say what the code does now, and why where a decision was made. How the change came about goes in the pull request.
- Docs are in plain words, for someone who has never seen the code, and state only what was checked.
- `README.md`, `docs/reference.md` and `docs/setup-with-claude.md` describe the commands. A change to a command's flags or output changes them in the same pull request.

## The mod

- It runs inside Claude Code through the function-hooks API, which is early access. The gate writes that API, as the installed Claude Code declares it, to `mod/.claude/types/claude-code.d.ts`. Look names up there.
- Its tests use Claude Code's own kit, `claude-code/testing`, and stand in for the `dream` command.
- The mod reaches remcycle only by running `dream` and reading what it prints. Put logic in the command, with its test, and let the mod call it.

## Pull requests

Branch from `main` and open a pull request. CI runs the gate on Linux and macOS, on the oldest and newest Python remcycle supports, and against the Claude Code version the mod was written against. Merge when the `gate` result is green.

A commit subject says what the change does, in plain words: "Say how many sessions are unread", not "Update cli.py".
