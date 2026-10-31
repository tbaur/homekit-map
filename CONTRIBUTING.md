# Contributing to homekit-map

Thanks for helping. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Issues

Check [existing issues](https://github.com/tbaur/homekit-map/issues) first, then use the bug or feature template. Include your macOS and Python versions and the command you ran.

**Never paste a raw snapshot or export.** The HomeKit database holds pairing material and the names of your home. Replace room, accessory, and person names with placeholders such as `Zone One` or `Outlet A` before you paste any output.

## Development

You need macOS, Python 3.11 or newer, and Git. Tests use a generated fixture database, so they also run on Linux and do not need Full Disk Access.

```bash
git clone git@github.com:tbaur/homekit-map.git
cd homekit-map
make check
```

| Target | What it does |
|---|---|
| `make install` | Creates `.venv` and installs the tool with dev extras |
| `make lint` | Ruff |
| `make test` | pytest with the coverage gate (`fail_under` in `pyproject.toml`) |
| `make check` | `lint` and `test` |
| `make clean` | Removes `.venv`, build output, and caches |

CI runs `make lint`, `make test`, and `pip-audit` on Python 3.11, 3.13, 3.14, and 3.15. A final `Tests passed` job is the required check.

Test data must be placeholders. Use the names already in `tests/fixture.py`, and never copy values from your own snapshot.

## Pull requests

1. Branch from `main`, for example `fix/short-description`.
2. Add or update tests when behavior changes.
3. Update [README.md](README.md), [docs/query.md](docs/query.md), or [docs/safety.md](docs/safety.md) when what users see changes.
4. Give the PR a [Conventional Commit](https://conventionalcommits.org) title. The title sets the next version (see [RELEASING.md](RELEASING.md)).

| Prefix | Release |
|---|---|
| `fix:` | patch |
| `feat:` | minor |
| `feat!:` or a `BREAKING CHANGE:` footer | major |
| `docs:`, `test:`, `refactor:`, `chore:`, `ci:` | none |

Do not edit `CHANGELOG.md` or version strings by hand. release-please manages them.

## Project layout

```
homekit-map/
├── bin/homekit-map        # runs this checkout, installing .venv on first use
├── src/homekit_map/       # the package
│   ├── cli.py             # argument parsing and command dispatch
│   ├── backup.py          # read-only copy of the live database
│   ├── query.py, day.py, gaps.py, diff.py, extract.py
│   └── decode.py, predicate.py, schema.py, about.py
├── tests/                 # unittest-style tests run by pytest
└── docs/                  # query fields and backup safety
```
