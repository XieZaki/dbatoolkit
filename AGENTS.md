Before changing this project:

1. Read `docs/PROJECT_RULES.md`.
2. Read the Roadmap, Backlog, Progress, and Acceptance files under `docs/roadmap/`.
3. Check Git status and confirm the current Task ID in Progress and Backlog.
4. Stay within the requested version scope and execute only the user-authorized task.
5. For the user-confirmed local Docker lab, follow the lab-only authorization, credential-separation, script-review, and TLS-exception rules in `docs/PROJECT_RULES.md`; never extend them to production or other assets.

## Real database E2E execution (mandatory)

- Run real SQL Server, PostgreSQL, MySQL, and Oracle E2E only from the authorized Windows host context, using Windows PowerShell and this project's `.venv`; a PowerShell-shaped shell or matching path alone does not prove host access.
- Before a real connection, verify the actual OS/process context, working directory and Python/CLI executable, the single selected Inventory asset's host/port and `environment=test`, and credential-session availability. Never scan Inventory or guess another target.
- Never run real database E2E in a Codex isolated environment or treat its `127.0.0.1` as the Windows host. Do not diagnose database, named-pipe, Docker, or credential failures from an isolated-context access denial.
- If the authorized Windows host context is unavailable, stop before database access and give the user complete PowerShell commands to run locally. Do not retry the same blocked connection, read or bypass credential files, substitute another database, or present Mock/Fake results as E2E.
- Label evidence as `WINDOWS_LOCAL_REAL_E2E`, `ISOLATED_OFFLINE_TEST`, or `NOT_EXECUTED`; these are report labels, not product enums.
- Redact `report.instance.credential_alias` in exported diagnosis JSON while retaining the alias internally. Never expose credential values.

After completing a task:

1. Update task status only in Backlog.
2. Update Progress and add Acceptance evidence only when actual evidence exists.
3. Report the next task, then stop for user approval.
