-- EXPLICIT DISPOSABLE TEST FIXTURE ONLY. Never loaded by the Toolkit Registry.
CREATE TABLE dbo.ToolkitPlanFixture (
    id int NOT NULL PRIMARY KEY CLUSTERED,
    value int NOT NULL,
    payload char(80) NOT NULL
);
WITH numbers AS (
    SELECT 1 AS id
    UNION ALL
    SELECT id + 1 FROM numbers WHERE id < 1000
)
INSERT INTO dbo.ToolkitPlanFixture (id, value, payload)
SELECT id, id % 10, 'plan fixture' FROM numbers OPTION (MAXRECURSION 0);
CREATE INDEX IX_ToolkitPlanFixture_value ON dbo.ToolkitPlanFixture(value);
CREATE USER toolkit_workload FOR LOGIN toolkit_workload;
GRANT SELECT ON dbo.ToolkitPlanFixture TO toolkit_workload;
