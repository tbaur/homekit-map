# Security Policy

## Supported versions

Only the latest release gets fixes.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting on the Security tab. Include what you found, how to reproduce it, and the impact you expect.

You should get an acknowledgment within a week.

## What the tool protects

homekit-map reads a copy of the HomeKit database on your Mac. That database holds pairing material and the names of your home, rooms, and accessories. The tool is built around two rules:

- **It never writes the live database.** `backup` opens `~/Library/HomeKit/core.sqlite` read-only and writes only into `data/`. The other commands refuse to open the live folder. [docs/safety.md](docs/safety.md) explains how.
- **The snapshot stays on this machine.** `data/` is gitignored. The tool makes no network calls and has no runtime dependencies.

## Keeping your data private

- Do not commit `data/`, `*.sqlite`, or `export.json`, and do not attach them to issues.
- Redact room, accessory, and person names from any output that you paste into an issue.
- `backup` needs Full Disk Access for the terminal that runs it. Grant it to that terminal only, and remove it when you no longer need it.
