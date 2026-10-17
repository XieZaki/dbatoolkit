-- EXPLICIT DISPOSABLE TEST FIXTURE ONLY. Never loaded by the Toolkit Registry.
CREATE TABLE dbo.ToolkitSlowFixture (id int NOT NULL PRIMARY KEY, value int NOT NULL);
INSERT INTO dbo.ToolkitSlowFixture VALUES (1, 0), (2, 0);
CREATE USER toolkit_workload FOR LOGIN toolkit_workload;
GRANT SELECT, UPDATE ON dbo.ToolkitSlowFixture TO toolkit_workload;
