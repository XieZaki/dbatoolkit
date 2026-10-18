SELECT st.session_id, s.login_time,
    a.transaction_id, a.transaction_begin_time,
    a.transaction_type, a.transaction_state,
    CAST(DATEDIFF(DAY, a.transaction_begin_time, GETDATE()) AS bigint) * 86400
      + DATEDIFF(SECOND, DATEADD(DAY, DATEDIFF(DAY, a.transaction_begin_time, GETDATE()),
          a.transaction_begin_time), GETDATE()) AS transaction_age_seconds
FROM sys.dm_tran_session_transactions AS st
JOIN sys.dm_tran_active_transactions AS a ON a.transaction_id = st.transaction_id
JOIN sys.dm_exec_sessions AS s ON s.session_id = st.session_id
WHERE s.is_user_process = 1 AND st.session_id <> @@SPID
ORDER BY a.transaction_begin_time, st.session_id;
