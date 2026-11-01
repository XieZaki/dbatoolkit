# Opt-in encrypted SQLite Provider (0.9.0 / V0.9.1 stabilization)

This implements the separately approved follow-up to the disposable transaction
experiment. It does not migrate any real user Store or approve a production release.
The legacy encrypted JSON Provider and Windows Credential Manager remain available.
SQLite is opt-in and currently Windows-only; other platforms return an explicit
unsupported-backend error. No automatic provider detection, switch or fallback exists.

## Transaction and authentication contract

The private database contains one encrypted credential map. AES-256-GCM and the
existing Argon2id safety policy are used. A fresh salt derives a data key, with a
key-confirmation HMAC. Store UUID, format/version, KDF parameters, migration state,
key confirmation and generation are authenticated as GCM additional data. Alias,
username and password appear only inside the encrypted map. Nonsecret metadata,
SQLite structure and ciphertext remain visible. Master passwords are not cached by
this Provider; a Session retains only its data key in process memory.

All writes read/authenticate/modify/encrypt under BEGIN IMMEDIATE, using SQLite
DELETE rollback journals and synchronous=EXTRA (verified as SQLite numeric mode 3
on each opened connection). In DELETE mode EXTRA adds a directory sync after the
rollback-journal unlink; this is the strongest synchronous mode for this journal
mode, subject to the filesystem and hardware honoring sync requests. See the
[SQLite synchronous pragma](https://www.sqlite.org/pragma.html#pragma_synchronous).
GET and metadata access authenticate
the encrypted payload; structural integrity_check is not used as authentication.
The row and header have bounded sizes. Contention times out after three seconds
with CredentialStoreBusy. Corrupt files, payload authentication, unsupported
formats, unsafe paths and Store-binding changes have separate error categories.
An incorrect password and malicious alteration of key-confirmation material cannot
be distinguished cryptographically; both report authentication/unlock failure.

Windows opens the main file before SQLite with READ/WRITE sharing and no DELETE
sharing; its file ID is compared with the unlocked binding. The guard stays open
through commit/rollback and connection close, preventing tested ordinary external
rename/replacement. A dedicated empty directory is required for initialization.
Its protected DACL grants only the current SID and SYSTEM; journal files inherit
that ACL. Main file, directory ownership/DACL and existing sidecars are checked.
Reparse/symbolic-link paths are rejected. Initialization never overwrites a file.

Session protocol 2 requires the target path, explicit load backend and unlock-time
binding (file ID plus UUID for SQLite). The server independently authenticates the
Store before accepting LOAD and each content operation. GET/SAVE/SET/DELETE and
metadata cannot use another Store's Session. SQLite transactions keep file identity
stable and re-read current data; legal mutations keep the Session usable until its
absolute TTL, lock or rekey. The legacy Provider retains mutation invalidation.
Old protocol agents/clients are rejected: stop the old agent with its matching
version or allow its TTL to expire, then unlock with the new version.

## Explicit CLI and migration

Use a dedicated private directory outside the repository. All password prompts
are hidden. These are operator instructions, not actions performed in this task:

```powershell
dba credentials init --backend sqlite
dba credentials set <alias> --backend sqlite
dba credentials unlock --backend sqlite
dba credentials list --backend sqlite
dba credentials show <alias> --backend sqlite
dba credentials test <alias> --backend sqlite
dba credentials delete <alias> --backend sqlite
dba credentials rekey --backend sqlite
dba credentials lock
```

The opt-in default is `%APPDATA%/DBAToolkit/sqlite/credentials.db`. To select a custom
Store, add `--store-path <path>` to credential commands. Database commands use
`--credential-backend sqlite --credential-store-path <path>` (the path is optional
for the SQLite default). Inventory credential_alias does not change. `auto` keeps
its old routing and never discovers SQLite. A missing/broken explicit SQLite Store
does not select the legacy Store.

```powershell
dba credentials migrate --source <old-encrypted-file> --destination <new-private-directory>/credentials.db
dba credentials unlock --backend sqlite --store-path <new-private-directory>/credentials.db
dba credentials list --backend sqlite --store-path <new-private-directory>/credentials.db
dba credentials test <alias> --backend sqlite --store-path <new-private-directory>/credentials.db
```

Migration authenticates and pins the old file read-only, creates the new file with
an authenticated `migration` state, copies records and freshly unlocks/readbacks
every alias, username and password. Readback and unchanged-source verification
must pass before a guarded transaction publishes `ready`. There is no path rename
between verification and publication. Interrupted/failed targets stay private and
are rejected as CredentialMigrationIncomplete (or corrupt if initialization itself
was interrupted). They are never automatically selected, deleted or retried over.
Preserve them for review; choose a fresh dedicated destination for a new attempt.
Migration never changes source bytes or the selected Provider.

### Synchronous-mode check (2026-10-30)

`ISOLATED_OFFLINE_TEST`: the process ran Python 3.12.14 and SQLite 3.53.1 while
reporting Windows 11, but its system temporary directory was sandbox-redirected;
this is not Windows-host acceptance. A disposable-database microbenchmark ran 120
single-row `BEGIN IMMEDIATE`/`UPDATE`/`COMMIT` cycles for
each mode on the same temporary directory. `FULL` returned pragma value 2, median
1.4550 ms and mean 1.4670 ms; `EXTRA` returned value 3, median 1.5019 ms and mean
1.4983 ms. Both databases passed `PRAGMA integrity_check` and each committed 120
updates. This is a tiny synthetic commit-path comparison, not a representative
credential workload or a performance guarantee. The credential-provider tests
also assert mode 3 and exercise process-exit/hot-journal recovery; those tests do
not simulate power loss or establish native Windows-host filesystem behavior. No
real user Store was opened.

The old encrypted JSON file remains a read-only source and is retained as the
pre-migration rollback copy. In this release, initialize, SAVE/SET, DELETE,
rekey, JIT writes, and Session mutation requests against that legacy Provider are
rejected; only identity-bound reads and migration-source reads are permitted.
There is no silent SQLite selection or fallback. Additional backups must stay in
a private ACL directory and contain ciphertext only. An active
SQLite database must be backed up with SQLite's consistent backup API, or copied
only after all readers/writers/agents have closed it. No backup command is supplied
in this change. Before new writes, manual rollback means locking the Session and
explicitly choosing `encrypted` with the old path. After any new writes, preserve
both Stores and explicitly reconcile changed credentials; do not silently revert
to the old copy or overwrite either Store.

## Limits and release gates

SQLite transactions and file sharing cover cooperating processes and tested
ordinary replacement/crash scenarios. They do not resist arbitrary same-user file
overwrites, process memory access, malicious journal edits or restoration of an
older authenticated database. Parent-directory replacement races, power loss,
hardware/fsync failures, network filesystems, low-disk behavior and other platforms
are not verified. AES authentication detects damage but cannot repair arbitrary
corruption. Journal recovery is crash consistency, not cryptographic rollback
protection. No database diagnostics E2E is implied by credential tests.

The current Provider rejects writes to the old JSON file, but older Toolkit
versions do not enforce this read-only policy; running an older writer or restoring
an older application version remains an operator-controlled downgrade risk. This
change does not resist arbitrary same-user edits, parent-directory replacement,
process-memory inspection, or valid rollback of the SQLite database. Process-exit
recovery tests do not simulate power loss. SQLite atomic-commit guarantees depend
on operating system, filesystem, and storage hardware behavior; see
[SQLite atomic commit](https://www.sqlite.org/atomiccommit.html). Memory Grant/WAL
deferred gates are unchanged.
