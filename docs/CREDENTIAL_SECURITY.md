# Credential Security

DBA Toolkit resolves an Inventory `credential_alias` through the Credential Provider abstraction. Adapters receive only a `Credential` object and do not know its storage backend.

## Provider selection

The automatic resolution order is:

1. Windows Credential Manager or an approved Linux secure keyring backend.
2. An active encrypted Credential Session.
3. An existing encrypted local store.
4. Just-in-time enrollment when the alias is genuinely missing.

When a database command cannot find an alias after all available stores were checked successfully, it asks for the database username and hidden password, then asks whether to save securely (default Yes). Choosing No uses the credential in that process only. Choosing Yes writes through the selected secure OS store or an already initialized encrypted store. An encrypted store is never created implicitly. If no secure persistent backend is available, the command continues ephemerally and explains how to initialize encrypted storage. Existing encrypted stores that fail to unlock, authenticate, or pass integrity/permission checks fail closed and never fall through to JIT.

`dba credentials init --backend encrypted` creates the encrypted store; explicit `dba credentials set <alias> --backend encrypted` remains available for advanced provisioning and rotation. `dba credentials backend` reports backend availability without unlocking or showing stored values. `credentials list/show/test/delete` support `--backend auto|os|encrypted`; `rekey` changes the encrypted store master password. Existing aliases are never overwritten by JIT enrollment. Credential migration between stores is not automatic.

## Credential Session (V0.8.3.5)

Run `dba credentials unlock` to decrypt the encrypted store once and place its credential map in a separate, short-lived Agent process. The master passphrase is used only by the unlocking CLI to derive/decrypt; after transfer the encrypted-store object is locked and local references are cleared. The Agent retains resolved credentials and the derived data key in process memory; it never retains the master passphrase. The key lets the Agent atomically add an explicitly approved JIT credential to the encrypted store without prompting for the master passphrase again. A successful JIT save rewrites the authenticated encrypted container and immediately invalidates the session. Python cannot guarantee zeroization of immutable strings or byte objects.

The default session uses an absolute 30-minute TTL. `--ttl` accepts bounded values from `5m` through `8h`; activity never extends expiry. `--renew` asks for the master passphrase again and replaces the payload. `dba credentials status` exposes only session metadata, while `dba credentials lock` clears references and stops the Agent. Agent crash, OS restart, expiry, lock, or any encrypted-store file change invalidates the session. Successful encrypted `set`, `delete`, and `rekey` also request immediate invalidation. An encrypted file fingerprint is checked against the store on each session request to detect external changes.

Windows uses a named pipe with a protected DACL granting access only to the current user's SID and SYSTEM; the server also checks the connecting process token SID. Linux uses a Unix domain socket in a mode-0700 per-user runtime directory, a mode-0600 socket, and Linux `SO_PEERCRED` UID verification. Unsupported Unix platforms fail closed when peer identity cannot be verified. The JSON protocol is versioned and bounded to 4 MiB, rejects unknown fields and duplicate JSON keys, and supports status, one-alias lookup, metadata listing, load, save-and-invalidate, lock, and shutdown. It does not use TCP, pickle, or eval.

The session reduces repeated passphrase entry and prevents ordinary other-user access through the IPC boundary. It does not protect against Administrator/root with full control, malware running as the same user, keyloggers, a debugger or memory inspector attached to the Agent, or kernel compromise. Same-user processes are within the session trust boundary. No master password, database password, key, session token, credential payload, or session metadata is persisted by the session feature; the Linux socket pathname is the only IPC filesystem artifact and is removed when the Agent exits.

If an existing encrypted store cannot be unlocked, Toolkit fails closed and does not prompt for a replacement database credential. Wrong master password and failed authentication produce the same unlock message. Unsupported formats, unsafe permissions, and malformed containers also fail closed without modifying the source store.

## Encrypted format

The local store uses AES-256-GCM with a 256-bit key and 96-bit random nonce, with Argon2id key derivation. The current centralized KDF policy is 64 MiB (`memory_cost_kib=65536`), three iterations, and one lane. A cryptographically random 16-byte salt is generated on every store rewrite, including update and rekey. Store format v1 records the cipher, KDF name/parameters, salt, nonce, and ciphertext. Canonical authenticated associated data binds format identifier, format version, KDF parameters, cipher name, and salt; GCM authenticates the ciphertext and nonce. Alias, username, and password are all inside the encrypted payload. No plaintext alias index is created for this provider.

The master password is entered with hidden terminal input, kept only in the current process, and never written to a file, environment variable, command argument, or log. A CLI process unlocks once and reuses its in-memory payload for that operation. `dba credentials rekey --backend encrypted` decrypts with the current password, derives a new key using a new salt, generates a new nonce, and atomically replaces the encrypted container. Master password minimum length is 12 characters; a longer passphrase is recommended.

Writes use a same-directory temporary file, restrictive creation mode, flush/fsync, and atomic replace. An advisory cross-process lock prevents cooperating Toolkit commands from overwriting one another; lock contention fails with an explicit error. Corrupt or unsupported files are never reset automatically. There is no automatic backup artifact.

## File locations and permissions

- Windows: `%APPDATA%\DBAToolkit\credentials.enc` (falling back to the current user's roaming AppData path). The file is outside the project and is checked against public/repository locations. Complete ACL evaluation is reported as `BEST_EFFORT`, not as a proven secure ACL.
- Linux: `$XDG_CONFIG_HOME/dba-toolkit/credentials.enc`, or `~/.config/dba-toolkit/credentials.enc`. The directory is created with mode `0700`, the store and lock with mode `0600`. Existing group/world access is rejected with a remediation message; Toolkit does not silently repair existing permissions.

Do not place a custom copy in a project, shared directory, or repository. `.gitignore` and the tracked-file scanner reject known credential-store/key artifacts. Encryption does not make a file safe to commit: a copied ciphertext permits offline password guessing.

## Threat model and limits

The encrypted provider is designed to reduce Git leakage, plaintext-file exposure, ordinary report/log leakage, and direct disclosure from a copied store. AES-GCM detects ciphertext/header tampering; Argon2id increases offline guessing cost. Protection depends on a strong master passphrase and restrictive file access.

It does not protect against a fully compromised current OS account, an administrator/root with complete control, malicious memory inspection, keyloggers, or unlimited offline guessing of a weak passphrase. A master-password-protected store can technically be moved to another machine and decrypted with the same password; that is not an approved synchronization mechanism. Use organization-approved protected media and procedures if a controlled transfer is required.

Python cannot guarantee zeroization of immutable string objects. Toolkit limits secret lifetime and avoids logging/repr, but does not claim secure memory erasure. The encryption salt is not a secret, but is not printed in normal output. Keys, plaintext payload, ciphertext, and authentication details are not printed.

## Offline deployment

The runtime dependencies `cryptography` and `argon2-cffi` are declared in `pyproject.toml`, `deployment/runtime-manifest.yaml`, and `requirements/base.lock`. A target-specific offline wheelhouse must include these distributions and their dependencies. Bootstrap always uses `--no-index`; missing wheels fail locally and never trigger an internet fallback. Run `dba doctor` to inspect package/backend readiness.

OS secure stores remain preferred when usable. `Interactive Provider` remains available without a desktop session's keyring, and the encrypted local provider supports offline/headless operations after explicit initialization.
