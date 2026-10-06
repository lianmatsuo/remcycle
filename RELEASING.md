# Releasing

A release is a signed tag, `vX.Y.Z`, on a commit of `main`. Pushing the tag starts [the release workflow](.github/workflows/release.yml), which publishes the `dream` command to PyPI and creates the GitHub release.

Nothing reaches anyone between releases. The command is installed from PyPI, and the mod from the tag that `.claude-plugin/marketplace.json` names, so `main` can move freely.

## The version

One version covers the command and the mod. It is written in `pyproject.toml`, `uv.lock` and `mod/.claude-plugin/plugin.json`, and as the tag in `.claude-plugin/marketplace.json`. A test fails when they differ, and they change only in a release.

Before 1.0, a release that adds something raises the middle number, and one that only fixes raises the last. This lists what is going out:

```bash
git log vPREVIOUS..origin/main --format=%s
```

## The steps

1. **Branch and write the version.** On a branch `release/X.Y.Z` from `main`:

   ```bash
   scripts/release bump X.Y.Z
   ```

2. **Write the changelog.** Add a `## X.Y.Z` section at the top of `CHANGELOG.md`, in plain words for someone who uses remcycle. It becomes the release's notes word for word, so anything a person has to do when they upgrade goes in it. `scripts/release notes X.Y.Z` prints what the release will say.

3. **Open the pull request.** Commit as `chore(release): X.Y.Z`, open it, and wait for `gate`.

4. **Tag and push.** The tag goes on the commit that passed. `main` and the tag arrive together, because from that commit on the marketplace file on `main` names the tag:

   ```bash
   git tag -s vX.Y.Z -m "remcycle X.Y.Z" release/X.Y.Z
   git push --atomic origin release/X.Y.Z:main vX.Y.Z
   ```

5. **Watch the workflow.** It refuses a tag that does not name the version the files hold, a commit that did not pass `gate`, and a version with no changelog section. Then it builds, publishes to PyPI and creates the GitHub release with the built files attached.

6. **Check what came out.**

   ```bash
   gh release view vX.Y.Z
   uvx --refresh --from remcycle==X.Y.Z dream --version
   claude plugin update remcycle@remcycle
   ```

## Publishing needs no token

PyPI accepts the upload from the workflow itself: the project there names this repository, `release.yml` and the `pypi` environment as its trusted publisher. Renaming the workflow file or the environment breaks publishing until PyPI is told the new name.

## When the workflow fails

- **Before the PyPI step.** Nothing is out. Fix it on `main` through a pull request, remove the tag here and on GitHub, and tag the fixed commit with the same version.
- **After the PyPI step.** The version is on PyPI for good, and PyPI never takes the same version twice. If only the GitHub release is missing, run that job again from the workflow's page. If the release itself is wrong, release the next version with the fix.
