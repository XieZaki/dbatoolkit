SELECT name, value, unit, time_computed, datum_time
FROM SYS.V_$DATAGUARD_STATS
WHERE name IN ('transport lag', 'apply' || ' lag', 'apply' || ' finish time')
ORDER BY name
