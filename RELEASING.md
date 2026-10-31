# Releasing

[release-please](https://github.com/googleapis/release-please) automates releases. It reads commit messages to set the version, write `CHANGELOG.md`, and create the git tag and GitHub Release. Nobody edits those by hand.

Nothing is published to PyPI. A release is the git tag and the GitHub Release notes.

## Flow

1. Open a PR with a Conventional Commit title. The table in [CONTRIBUTING.md](CONTRIBUTING.md) shows which prefix makes which release.
2. When **Tests**, **Dependency review**, and **CodeQL** pass, squash-merge the PR into `main`.
3. release-please opens or updates a Release PR titled `chore(main): release X.Y.Z`. It bumps `.release-please-manifest.json`, `pyproject.toml`, and `src/homekit_map/__init__.py`, and it adds to `CHANGELOG.md`. Several merged PRs go into one Release PR.
4. Approve the Release PR's checks, then merge it. That creates the `vX.Y.Z` tag and the GitHub Release.

To force a version, put `Release-As: X.Y.Z` in the footer of the squash-merged commit.

## Approving the Release PR checks

`github-actions[bot]` opens the Release PR, so GitHub holds its workflow runs until someone with write access approves them. Click **Approve and run** on the PR's Checks tab, or approve each held run:

```bash
gh api "repos/tbaur/homekit-map/actions/runs?status=action_required" --jq '.workflow_runs[].id'
gh api -X POST repos/tbaur/homekit-map/actions/runs/RUN_ID/approve
```

Approve again whenever release-please updates the Release PR. If you merge without approving, the runs end as `failure` with no jobs. That means nobody approved them. It does not mean something broke.

## Repository settings

- Branch protection on `main`: require a pull request, require the `Tests passed` and `Dependency review` checks, allow squash merges only, and block force-pushes.
- Settings, Actions, General, Workflow permissions: choose **Read and write permissions** and turn on **Allow GitHub Actions to create and approve pull requests**. Without that, release-please cannot open the Release PR.
