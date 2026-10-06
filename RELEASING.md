# Releasing

A release is a signed tag, `vX.Y.Z`, on a commit of `main`. Pushing the tag starts [the release workflow](.github/workflows/release.yml), which publishes the `dream` command to PyPI and creates the GitHub release.

## The version

One version covers the command and the mod. It is written in `pyproject.toml`, `uv.lock` and `mod/.claude-plugin/plugin.json`, a test fails when they differ, and it changes only in a release.

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

4. **Tag and push.** The tag goes on the commit that passed, and `main` and the tag arrive together:

   ```bash
   git tag -s vX.Y.Z -m "remcycle X.Y.Z" release/X.Y.Z
   git push --atomic origin release/X.Y.Z:main vX.Y.Z
   ```

5. **Watch the workflow.** It refuses a tag that does not name the version the files hold, a commit that did not pass `gate`, and a version with no changelog section. Then it builds, publishes to PyPI and creates the GitHub release with the built files attached.

6. **Check what came out.**

   ```bash
   gh release view vX.Y.Z
   uvx --refresh --from remcycle==X.Y.Z dream --version
   ```

## Publishing needs no token

PyPI accepts the upload from the workflow itself: the project there names this repository, `release.yml` and the `pypi` environment as its trusted publisher. Renaming the workflow file or the environment breaks publishing until PyPI is told the new name.

## When the workflow fails

- **Before the PyPI step.** Nothing is out. Fix it on `main` through a pull request, remove the tag here and on GitHub, and tag the fixed commit with the same version.
- **After the PyPI step.** The version is on PyPI for good, and PyPI never takes the same version twice. If only the GitHub release is missing, run that job again from the workflow's page. If the release itself is wrong, release the next version with the fix.
