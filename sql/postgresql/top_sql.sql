WITH requested AS (
    SELECT %s::integer AS top_n, %s::text AS metric_name
), candidates AS (
    SELECT s.dbid,
           s.userid,
           s.queryid,
           s.toplevel,
           CASE requested.metric_name
               WHEN 'total_exec_time' THEN s.total_exec_time::numeric
               WHEN 'mean_exec_time' THEN s.mean_exec_time::numeric
               WHEN 'calls' THEN s.calls::numeric
               WHEN 'shared_blks_read' THEN s.shared_blks_read::numeric
               WHEN 'shared_blks_hit' THEN s.shared_blks_hit::numeric
               WHEN 'temp_blks_written' THEN s.temp_blks_written::numeric
               WHEN 'wal_bytes' THEN s.wal_bytes
           END AS ranking_value
    FROM public.pg_stat_statements AS s
    CROSS JOIN requested
), limited AS (
    SELECT dbid, userid, queryid, toplevel, ranking_value
    FROM candidates
    ORDER BY ranking_value DESC NULLS LAST,
             dbid ASC NULLS LAST,
             userid ASC NULLS LAST,
             queryid ASC NULLS LAST,
             toplevel ASC NULLS LAST
    LIMIT (SELECT top_n FROM requested)
)
SELECT dbid, userid, queryid, toplevel, ranking_value
FROM limited
ORDER BY ranking_value DESC NULLS LAST,
         dbid ASC NULLS LAST,
         userid ASC NULLS LAST,
         queryid ASC NULLS LAST,
         toplevel ASC NULLS LAST;
