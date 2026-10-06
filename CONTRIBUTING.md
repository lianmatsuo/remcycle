# Contributing

People and coding agents follow the same guide: [AGENTS.md](AGENTS.md). It covers the one command that checks a change, how to try things without touching real data, and how changes are made.

To start:

```bash
git clone https://github.com/lianmatsuo/remcycle
cd remcycle
uv sync
scripts/check
```

`scripts/check` needs [uv](https://docs.astral.sh/uv/) and, for the mod's half, Claude Code and [pnpm](https://pnpm.io). `scripts/check python` needs only uv.

Then branch, make the change, run `scripts/check`, and open a pull request.

Questions and ideas go to [Discussions](https://github.com/lianmatsuo/remcycle/discussions), and something that is wrong goes to an issue. Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).
