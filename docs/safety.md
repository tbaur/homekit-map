# Why backup does not write the live database

`homekit-map backup` copies `~/Library/HomeKit/core.sqlite` to `data/core.sqlite`. HomeKit can be running. The copy is the only file the command is willing to replace.

## When a `-wal` file is already there

HomeKit's store uses SQLite WAL mode. Recent commits live in `core.sqlite-wal`, next to the database. The command opens the live file with SQLite's read-only URI and `PRAGMA query_only=ON`, then uses the backup API. That API reads a consistent snapshot, including commits that are still in the wal.

A read-only connection can update `core.sqlite-shm`. That file is the lock index SQLite uses so HomeKit can keep writing. It is not the database. The command says so when the lock file's bytes change. It does not write `core.sqlite` or `core.sqlite-wal`.

The read-only setting is checked before the copy and again after it. If SQLite did not keep the connection read-only, the command stops and deletes only its own partial file.

## When there is no `-wal` file

Opening a WAL database that is missing its `-wal` file makes SQLite create one beside the live database. This command does not open the live file with SQLite in that case. It reads the bytes and writes them to the snapshot. If the live file's hash changes during that read, or a `-wal` file appears, the partial snapshot is discarded and nothing in the live folder is deleted. Run the command again.

## What the command will not do

- It will not follow a symlink for the source, the destination, or the destination directory.
- It will not write a file inside the folder that contains the source, including `core.sqlite`, `core.sqlite-wal`, and `core.sqlite-shm`. Folders are compared by file identity as well as by name, so a path that spells the folder in another case is refused too.
- It will not delete a file in that folder. The only files it deletes are its own unfinished `*.partial` files next to the snapshot, and an unfinished version file from a copy that failed.
- It will not replace the destination when that path is the source.

A later backup writes a new partial file outside the live folder. If those bytes differ from `data/core.sqlite`, the command first copies the current snapshot to `data/versions/`, then renames the partial into place. The version name is the snapshot file's modification time, `YYYYMMDD-HHMMSS`. If that name is already taken, the next file ends in `-2`. The rename happens only after the destination has been checked again.

If the new bytes match `data/core.sqlite` and `data/versions/` has no snapshot yet, the command copies the current snapshot there and leaves `data/core.sqlite` in place. A later backup with the same bytes does not add another version. Version files are full copies of the store. They stay under `data/`, which git ignores. A failed copy deletes the unfinished version file it just created. It does not delete anything in the live folder.

`search`, `diff`, and `extract` never open the live directory. They open a snapshot with the same read-only URI and `query_only`. `diff versions` only lists file names under the snapshot folder. `extract` also refuses to write its JSON into the live directory, and it refuses an output path that is a symlink. It writes the JSON to a private `0600` file next to the output, then renames it into place, so no other user can read a partial export.

The snapshot is a full copy of the store. Treat `data/` like a password file. The gitignore excludes it. Do not force-add it.
