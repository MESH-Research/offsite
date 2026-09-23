# FAQ

Design questions that have come up while building this system, with the
reasoning (and upstream documentation) behind the answers.

## Does `RESTIC_HOST` identify the storage server?

No — it is the **FROM** side. The hostname stored in a restic snapshot names
the machine the data came *from* (the backup client), never the repository it
was written *to*. From `restic backup --help`:

> `-H, --host hostname    set the hostname for the snapshot manually
> (default: $RESTIC_HOST). To prevent an expensive rescan use the "parent"
> flag`

Each repository (the primary and every mirror) is a fully independent store
with its own identity; which storage box you are talking to is determined
solely by `RESTIC_REPOSITORY`. The host label plays no part in that, so using
one label across all repositories is correct: the *source* — this backup
system — is the same in every case.

We pin the label (default `mesh-offsite`) because Fargate containers get
random hostnames, and two documented restic behaviours depend on a stable one:

> "When `forget` is run with a policy, restic first loads the list of all
> snapshots and groups them by their host name and paths."
> — [restic docs: Removing backup snapshots](https://restic.readthedocs.io/en/stable/060_forget.html)

> "By default restic groups snapshots by hostname and backup paths, and then
> selects the latest snapshot in the group that matches the current backup."
> — [restic docs: Backing up](https://restic.readthedocs.io/en/stable/040_backup.html)

With a random hostname per run, every snapshot would sit in its own retention
group (so `forget` could never thin correctly) and no backup would ever find
a parent snapshot (so every run would re-read and re-hash the entire tree).

Why not rename the variable to something clearer? Because `RESTIC_HOST` is
restic's *own* environment variable (see the help text above) — people who
know restic expect it, and inventing our own name would just add a synonym.

## What does the retention policy actually keep?

The policy is `restic forget --keep-weekly 4 --keep-monthly 6` (configurable
via `KEEP_WEEKLY` / `KEEP_MONTHLY`; `KEEP_DAILY` exists but defaults to 0 =
disabled). Restic counts calendar periods **including the current one**, and
the current period's slot always lands on the newest snapshot. With daily
backup runs, the retained set at any moment is therefore:

- the contemporary snapshot (newest),
- the last **3** ISO week-end snapshots (the current week's slot is the
  newest snapshot itself),
- the last **5** month-end snapshots (likewise).

Each week-end checkpoint expires at ~4 weeks old; each month-end checkpoint
expires when it leaves the 6-month window. Backups still *run* daily — each
green run's `forget` thins them to this set. Skipping `forget` (which we do
whenever any component fails) only defers deletions; it never loses data.

## Won't daily SQL dumps of a huge database bloat the repository?

Mostly not. Restic splits every input stream into content-defined chunks
(~0.5–8 MiB) using a rolling hash; identical byte runs dedup across snapshots
even inside one enormous file, and chunk boundaries resynchronise after
insertions and deletions. A mostly-unchanged daily dump uploads roughly its
changed regions, and repository-format-v2 compresses the remainder (SQL text
compresses ~3–5×). Three caveats:

1. **Time is not incremental** — restic still reads and hashes the whole dump
   every run; only storage and transfer shrink.
2. **Byte-stability matters** — InnoDB dumps rows in primary-key order, we
   pass `--skip-dump-date`, and we never pre-compress: gzip before restic
   scrambles the bytes and destroys deduplication entirely.
3. **Postgres dedups somewhat worse** — `pg_dump`'s COPY output follows heap
   order, which drifts with updates and vacuum, but savings remain
   substantial. (`pg_dump` is MVCC-snapshot-consistent by default — the
   Postgres equivalent of `mariadb-dump --single-transaction`.)

If real-world dedup disappoints (check `restic stats --mode raw-data` growth
after the first week), the escalation path is per-table dump files (mydumper
for MariaDB, `pg_dump -Fd -Z0` for Postgres — both still open formats) and
excluding high-churn ephemeral tables (WordPress transients, cache/log
tables) from the main dump.
