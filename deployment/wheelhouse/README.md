# Offline wheelhouse

Place wheels built or downloaded on an approved connected preparation machine here. Wheels must match the target operating system, CPU architecture, and Python ABI. This directory intentionally contains no third-party binaries in source control.

Bootstrap uses this directory with `--no-index`; it never silently falls back to the Internet. Include the project build requirements, base dependencies, and the selected database extra dependencies.

Base runtime includes cryptography and argon2-cffi for the explicitly initialized encrypted Credential Store. Prepare matching wheels and transitive dependencies for every target OS, Python ABI, and architecture. Each per-database requirements lock includes base.lock.
