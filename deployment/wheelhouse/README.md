# Offline wheelhouse

Place wheels built or downloaded on an approved connected preparation machine here. Wheels must match the target operating system, CPU architecture, and Python ABI. This directory intentionally contains no third-party binaries in source control.

Bootstrap uses this directory with `--no-index`; it never silently falls back to the Internet. Include the project build requirements, base dependencies, and the selected database extra dependencies.

The portable Windows x64 package is pinned to CPython 3.13 (`cp313`) by the lock set in `deployment/locks/windows-cp313-x64/`. Its base and per-database locks enumerate exact versions and SHA256 hashes, including transitive runtime dependencies. Prepare the matching wheels with `pip download --no-deps --only-binary=:all: --require-hashes --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 --dest deployment/wheelhouse -r <lock-file>` for every lock file. The Windows manual build consumes only those local wheels with `--no-index` and `--require-hashes`.

The base runtime includes cryptography and argon2-cffi for the explicitly initialized encrypted Credential Store. Driver locks include the base lock so each replaceable driver package carries its declared dependency set and can be checked independently. Do not use this lock set for another Python ABI, operating system, or architecture.
