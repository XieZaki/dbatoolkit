# Diagnostic policy configuration

CONFIG-003 adds nested diagnostics to settings YAML and Inventory. Connection
options retain their existing connection section and engine-specific semantics.
The example files are examples/diagnostics.settings.yaml and
examples/inventory/sqlserver.example.yaml. Example assets are disabled.

Resolution order is explicit CLI arguments, Inventory instance diagnostics,
Inventory defaults diagnostics, global settings diagnostics, legacy engine
settings, then program defaults. Nested Inventory sections merge field by field.
An omitted field inherits its lower-priority value. Null, booleans in numeric
fields, unknown fields, duplicate YAML keys and out-of-range values are rejected.
An empty global diagnostics mapping remains invalid for compatibility.

Existing engine settings continue to load. The new global names map onto the
same runtime settings consumed by the collectors and analyzers.
Legacy slow_top_n values above 100 now fail validation, matching the bounded
Top SQL and Active SQL count contract. Reduce such values before migration.

- diagnostic_sql_timeout_seconds maps to the existing same-named field, default
  30 seconds, range 1..60. The effective SQL timeout remains the minimum of the
  diagnostic policy, Registry script timeout and any stricter connection cap.
  Execute and Fetch share the original absolute deadline.
  The CONFIG-003 authorization extends the earlier settings-only placement rule
  to Inventory diagnostics overrides. Inventory connection timeout caps continue
  to constrain the final execution budget independently.
- slow.threshold_seconds maps to long_running_seconds, default 10 seconds,
  range 1..86400, all four engines. Each engine retains its existing active SQL
  and blocking classification. This field does not set SQL execution timeouts.
- limits.top_n maps to slow_top_n, default 10, range 1..100. Top SQL and SQL
  Server Active SQL use this default. Explicit --top takes precedence for those
  commands. Slow retains its existing CLI argument contract.
- concurrency.active_requests_threshold maps to high_concurrency_threshold,
  default 50, range 1..86400. Native active-request counts remain engine-specific.
  Connection count and CPU saturation are separate evidence.
- transactions.idle_threshold_seconds maps to idle_transaction_seconds, default
  60, range 1..86400, MySQL/PostgreSQL/Oracle only.
- transactions.sqlserver_long_transaction_seconds defaults to 3600, range
  1..86400. SQL Server Slow triage and disk-growth use this independent threshold.
- plans.sqlserver.max_xml_bytes maps to plan_max_xml_size, default 1048576,
  range 4096..4194304.
- plans.sqlserver.top_operators maps to plan_top_operators, default 10,
  range 1..100. The human report retains its own bounded operator display policy.
- plans.sqlserver.large_scan_rows maps to plan_large_scan_rows, default 100000,
  range 1..1000000000.
- plans.sqlserver.cardinality_ratio_warning and cardinality_ratio_critical map
  to the existing plan_cardinality_ratio_warning and plan_cardinality_ratio_critical.
  Defaults are 10 and 100, range 1..86400, with critical strictly above warning.

## Execution plan scope

The main diagnostic HTML provides engine-specific plan guidance and commands.
It does not fetch a plan, execute EXPLAIN or embed complete plan XML.
Automatic plan collection is outside this configuration contract.
plans.auto_collect and plans.max_plans_per_report have been removed from the
formal schema and example. Either key is rejected as unknown, including false.

The plans.sqlserver keys configure the existing independent SQL Server Plan
Analyzer. XML bytes are an input bound, top_operators is the analyzer selection
count, and the remaining keys control evidence-based analysis thresholds.
Parser element/operator caps and version-profile XML caps remain independent.
The main report may disclose these configured policies as configuration evidence;
that disclosure never causes plan acquisition or analysis.

Existing explicit plan commands retain their own reviewed version, permission,
identity and existing-plan checks. Future deep plan analysis remains independent
of realtime diagnosis. The native command/guidance availability remains specific
to each engine and its existing capability. This change introduces no new plan
query or support claim.

## Evidence and fixed protection

Diagnostic JSON contains diagnostic_configuration with each applicable policy's
effective value, source, overridden values and at_hard_limit flag. HTML metadata
and CLI use these same resolved values and sources. Source labels contain no
credential values or configuration file paths.

Registry limits remain 10000 rows, 16 MiB result bytes and 60 seconds per SQL.
Showplan parsing retains 50000 XML elements and 10000 operators. Human reports
retain their own text, row and output budgets, including the 8 MiB hard output
limit. Driver connection/TLS options, runtime credential controls, SQL text
boundaries, readonly checks, redaction and Oracle acceptance relay parameters
retain their existing independent configuration or fixed safety contracts.
CONFIG-003 exposes no switches to disable those protections.

## Engine-specific and non-policy settings

- SQL Server connect_timeout defaults to 5 seconds. query_timeout is an optional
  stricter cap. encrypt defaults to true and trust_server_certificate to false.
- PostgreSQL connect_timeout defaults to 5 seconds. query_timeout is an optional
  stricter cap. sslmode defaults to verify-full.
- MySQL connect_timeout defaults to 5 seconds. query_timeout is an optional
  stricter cap. charset remains utf8mb4. ssl_enabled, ssl_verify_cert and
  ssl_verify_identity default to true, with ssl_ca unset.
- Oracle connect_timeout defaults to 5 seconds. call_timeout is an optional
  stricter cap. Thin remains the default driver mode.

These values remain under their existing engine settings and Inventory
connection contracts. Inventory Oracle connection.query_timeout maps to
call_timeout. Inventory MySQL connection.ssl_disabled maps to the inverse of
ssl_enabled. Diagnostic overrides never modify those TLS or credential settings.
The existing maximum for connection timeout values remains 86400 seconds.

Runtime paths, credential provider/session configuration, inventory selectors,
driver management and acceptance-only delay/observer controls remain outside
the diagnostics policy tree. Registry SQL source size stays at 1 MiB and metadata
at 64 KiB. Human SQL text remains bounded to 16 Ki characters. No new policy
can change XML node limits, report hard bytes or these Registry protections.

CONFIG-003 was formally accepted on 2026-11-18. Acceptance sections 118–121
record scoped Windows-local four-engine configuration and nonempty threshold
evidence; section 122 records human closure. These lab results do not certify
other versions or complete the separate P5 workbench acceptance.
