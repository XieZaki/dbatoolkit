# Credential SAVE transaction experiment — 2026-10-29

Product version remains 0.9.0. This is a test-only prototype, not a provider,
migration command or approval to change a user's Store. V0.9.1 RC remains blocked.

## Executable scope

`tests/credential_sqlite_prototype.py` uses Python sqlite3 transactions with one
AES-GCM encrypted JSON payload, an independently generated Store UUID and an
authenticated generation. The payload contains all aliases and values; SQLite
does not receive plaintext values. UUID, generation and SQLite structure are
visible. The supplied data key is retained in memory only. Prototype subprocesses
receive disposable test keys through stdin, never argv or environment variables.
There is no password/KDF interface in this experiment.

Each Windows operation opens a guard handle allowing READ/WRITE sharing but
denying DELETE sharing. File device/inode identity must match the session binding.
The guard stays open from before SQLite opens the existing file through transaction
close. SQLite uses DELETE journal mode, FULL synchronization and BEGIN IMMEDIATE
for a read/decrypt/modify/encrypt/commit transaction. It serializes cooperating
writers without stale whole-payload overwrites. Successful commits change the
generation, not the main-file identity, so the same binding can read again.
No default credential path or product provider imports this prototype.

## Threat boundary

Covered experiments: ordinary concurrent writers, file replacement attempts during
an operation, replacement between operations, process exit before/after commit,
authentication failure and damaged-file refusal. File identity is checked through
the pinned handle; the path cannot be renamed/replaced while that handle is open.

Not covered: arbitrary writes by malicious processes with the same user rights,
process-memory/key access, hostile journal manipulation, restoring an older valid
database, hard-link/parent-directory manipulation, power loss or faulty storage.
The guard intentionally permits writes so SQLite can open the database. SQLite
locking is not a defense against arbitrary same-user byte modification. AES-GCM
detects authentication failure; it cannot repair arbitrary corruption or prevent
denial of service. A trusted OS-backed monotonic authority would be needed to
claim rollback protection. Such claims are outside this candidate.

## Tests and evidence

Evidence label: ISOLATED_OFFLINE_TEST, Windows temporary files and isolated IPC,
not database E2E and not access to the real user's credential session.

Final focused command: pytest tests/test_credential_sqlite_prototype.py
tests/test_encrypted_credentials.py tests/test_credential_session.py -q -rs.
Result: 59 passed, 1 skipped (Windows has no POSIX permission bits), 0 failed.
This includes nine prototype cases and two real isolated-IPC cases. Targeted Ruff,
strict MyPy for the prototype and git diff --check passed. Full pytest and a
production release/security gate were not executed or approved by this experiment.

- SAVE then GET; two legal commits reuse the same binding, original values retained.
- Store A binding presented to Store B rejects without changing B.
- Four independently launched processes save distinct aliases; all survive.
- A subprocess attempts replacement after UPDATE and before COMMIT; replacement
  fails and the transaction succeeds. After the guard is released, replacement
  succeeds; the old binding then rejects and leaves the replacement unchanged.
- Abrupt os._exit before commit: cache spill forces an actual rollback journal;
  recovery preserves old credentials and excludes the uncommitted alias.
- Abrupt os._exit after commit: the new alias survives. A subsequent SAVE works.
- Wrong key, modified ciphertext and an externally truncated main file all reject
  without treating corruption as an empty Store or saving new credentials.

The isolated IPC fixture gives the agent, parent and lookup subprocess the same
random pipe name. It preserves the product's SID/peer checks. Unit tests in the
two credential modules use a unique unused endpoint; implicit default-agent launch
fails the test. Integration tests explicitly own and terminate only their child
agent. No production endpoint is probed or locked.

## Compatibility proposal requiring separate approval

1. A SQLite provider needs a new, explicitly identified disk format and filename.
   The current JSON/AES-GCM format stays readable by the existing provider.
2. Explicit one-time migration unlocks/authenticates the old Store in memory,
   preserves its KDF policy/parameters in the new provider design, creates a fresh
   protected database, encrypts the aliases and values and verifies a full readback.
   The prototype does not yet implement the KDF metadata, ACL hardening or migration.
3. Keep the old encrypted file unchanged. Switch the provider selection only after
   successful verification; if migration fails, discard only the new temporary
   candidate and continue using the old provider. Do not overwrite the old file.
4. Backups contain encrypted data only and inherit private ACLs. Use SQLite's
   backup API under verified Store binding, then authenticate/readback the backup;
   do not copy an active main database without its transaction state. Rollback to
   a pre-migration backup loses later changes and must be explicitly acknowledged.
5. Inventory credential_alias and Windows Credential Manager remain unchanged.
   Future CLI init/unlock/list/show/set/test/delete/rekey must route by explicit
   format/provider; there is no automatic migration or fallback on corruption.
6. Existing project policy invalidates a credential session after mutation. This
   experiment proves stable file binding, not approval to change that policy.
   Production integration must choose and test invalidation/reload explicitly.

Before integration: independent security review, parent-path/reparse policy,
cross-process startup/unlock races, backup/restore and rekey, bounded input/storage,
secure permissions, journal authentication limits, hardware durability and the full
CLI regression require validation. No production SAVE fix is claimed here.
