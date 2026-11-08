"""Database-specific normalization for human-readable evidence fields."""

from __future__ import annotations

import re
from typing import Any

# Human-facing terms shared by terminal and HTML presenters. Domain enums and
# machine-readable JSON remain unchanged; only exact labels/status values are
# translated here. Unknown/native identifiers pass through untouched.
DISPLAY_LABELS = {
    "UNKNOWN": "未知",
    "HEALTHY": "健康",
    "DEGRADED": "需关注",
    "NOT_DETECTED": "未观测到",
    "NOT_ASSESSED": "未评估",
    "BLOCKING": "锁与阻塞",
    "IDLE_TRANSACTION": "长期未提交事务",
    "LONG_RUNNING_SQL": "慢 SQL",
    "HIGH_CONCURRENCY": "高并发",
    "Database": "数据库",
    "Database Name": "数据库名称",
    "Instance": "实例",
    "Database Type": "数据库类型",
    "Database Engine": "数据库类型",
    "Version": "版本",
    "Scenario": "诊断场景",
    "Generated At": "报告生成时间",
    "Environment": "环境",
    "Data Completeness": "证据完整性",
    "Warnings": "提示",
    "Path": "路径",
    "Row Count": "行数",
    "Root Blockers": "根阻塞会话",
    "Root Cause Candidate": "根因候选",
    "Recent SQL Evidence": "最近执行 SQL",
    "Blocking Analysis": "锁与阻塞链",
    "Transaction Analysis": "长期未提交事务",
    "Session Identity": "会话身份与来源",
    "Request Runtime": "请求运行信息",
    "Workload Summary": "当前数据库状态",
    "Long Running SQL": "慢 SQL",
    "Wait Analysis": "等待分析",
    "Appendix — Other Sessions": "其他会话（附录）",
    "Immediate Recovery": "人工处置参考",
    "Collection Quality": "证据完整性",
    "Findings and Recommendations": "诊断发现与建议",
    "Database Space": "数据文件与表空间",
    "Transaction Log": "事务日志与保留证据",
    "Other Storage Evidence": "其他空间与日志证据",
    "Diagnostic Context": "未获取的证据与诊断上下文",
    "Execution Plan Follow-up": "执行计划后续检查",
    "Capture Window": "采集时间窗口",
    "Priority Review Candidates": "优先核查对象",
    "Database ID": "数据库 ID",
    "Session": "会话",
    "Role": "角色",
    "Status": "状态",
    "Login": "用户",
    "Host": "主机",
    "Program": "应用",
    "Client IP": "客户端 IP",
    "Open Txn": "未结束事务数",
    "Elapsed": "持续时间",
    "Wait": "等待事件",
    "Blocked By": "阻塞者",
    "CPU": "CPU",
    "Logical Reads": "逻辑读",
    "Reads": "物理读",
    "Writes": "写入",
    "Root Session": "根会话",
    "Affected Sessions": "影响会话数",
    "Direct Children": "直接子会话",
    "Maximum Depth": "最大阻塞深度",
    "Longest Block": "最长阻塞时长",
    "Collector": "采集器",
    "Impact": "影响",
    "Finding": "发现",
    "Recommendation": "建议",
    "Evidence Reference": "证据位置",
    "Severity": "严重程度",
    "Age": "事务时长",
    "Transaction ID": "事务 ID",
    "Begin Time": "开始时间",
    "Type": "类型",
    "State": "状态",
    "Report contents": "报告目录",
    "Rank": "排序",
    "User": "用户",
    "Reason": "原因",
    "Application": "应用",
    "Runtime seconds": "运行秒数",
    "Transaction age seconds": "事务时长秒",
    "SQL identity": "SQL 原生身份",
    "Transaction identity": "事务标识",
    "Login time": "登录时间",
    "Blocked session count": "影响会话数",
    "Evidence timestamp": "证据时间",
    "Identity recheck": "身份复核",
    "Priority": "优先级",
    "Action": "建议动作",
    "Reference Command": "人工参考命令",
    "Capture Started": "采集开始",
    "Triage Completed": "初报完成",
    "Capture Finished": "采集结束",
    "Snapshot Consistency": "快照一致性",
    "Indirectly Affected": "间接受影响会话",
    "Direct Blocked": "直接阻塞会话",
    "Direct Blocker": "直接阻塞者",
    "SQL identifier type": "SQL 标识类型",
    "Root Blocker": "根阻塞会话",
    "Wait Resource": "等待资源描述",
    "MySQL PROCESSLIST_ID": "MySQL PROCESSLIST_ID",
    "Performance Schema THREAD_ID": "Performance Schema THREAD_ID",
    "PostgreSQL PID": "PostgreSQL PID",
    "Oracle INST_ID": "Oracle INST_ID",
    "Oracle SID": "Oracle SID",
    "Oracle SERIAL#": "Oracle SERIAL#",
    "Observation": "当前观测",
    "Session Open Transaction Count": "会话级未结束事务计数",
    "Identity quality": "身份可信度",
    "Identity reuse note": "会话 ID 复用提示",
    "Target Session": "目标会话",
    "Open Transaction": "未结束事务",
    "Rollback Risk": "回滚风险",
    "Rollback Duration": "回滚耗时",
    "DBA Approval Required": "需要 DBA 审批",
    "Toolkit Executed": "Toolkit 是否执行",
    "Root-blocker Related Requests": "根阻塞相关请求",
    "Non-blocked Long-running Requests": "非阻塞的长运行请求",
    "Blocked Long-running Requests": "被阻塞的长运行请求",
    "MISSING_REQUIRED": "必需证据缺失",
    "OPTIONAL_UNAVAILABLE": "可选证据不可用",
    "SOURCE_COVERAGE_INCOMPLETE": "数据源覆盖不完整",
    "COLLECTION_FAILED": "采集失败",
    "LIMITATION_UNKNOWN": "限制原因未知",
    "NO_EXACT_NATIVE_IDENTITY_MATCH": "未找到完全一致的原生身份",
    "CUMULATIVE": "累计统计",
    "CURRENT": "当前快照",
    "INTERVAL_DELTA": "区间差值",
    "HISTORICAL": "历史统计",
    "NOT_COLLECTED": "未采集",
    "TRUNCATED": "结果已截断",
    "COMPLETE": "完整",
    "FAILED": "失败",
    "CRITICAL": "严重",
    "WARNING": "警告",
    "HIGH": "高",
    "INFO": "提示",
    "YES": "是",
    "NO": "否",
    "ACTIVE": "活动",
    "READY_WITH_WARNING": "就绪（有提示）",
    "AUTO_DETECTED": "自动检测",
    "INSTANCE": "实例级",
    "DATABASE": "数据库级",
    "SQL_STATEMENT": "SQL 语句",
    "MATCHED_AT_SAMPLE": "采样时匹配",
    "sleeping": "休眠",
    "suspended": "挂起",
    "running": "运行中",
    "Blocked": "被阻塞会话",
    "Long Running": "长运行请求",
    "Data completeness": "证据完整性",
    "Emergency evidence": "应急证据",
    "Full profile evidence": "完整配置证据",
    "required evidence": "必需证据",
    "Optional evidence": "可选证据",
    "Required evidence": "必需证据",
    "Optional unavailable": "可选证据不可用",
    "Collector row limit reached": "已达到采集行数上限",
    "NOT_APPLICABLE": "不适用",
    "PARTIAL": "部分可用",
    "INCOMPLETE": "不完整",
    "PENDING": "待采集",
    "IDENTITY_INCOMPLETE": "身份信息不完整",
    "REQUIRED_BEFORE_MANUAL_ACTION": "人工操作前必须重新核验",
    "COLLECTED": "已采集",
    "PERMISSION_DENIED": "权限不足",
    "UNSUPPORTED": "不支持",
    "UNAVAILABLE": "不可用",
    "SOURCE_UNAVAILABLE": "数据来源不可用",
    "SOURCE_NOT_CONFIGURED": "未配置数据来源",
    "QUERY_FAILED": "查询失败",
    "EMPTY": "无记录",
    "MATCHED": "身份已匹配",
    "UNMATCHED": "身份未匹配",
    "NO_ACTIVE_REQUESTS": "无活动请求",
    "TOP_SQL_IDENTITY_INCOMPLETE": "Top SQL 身份字段不完整",
    "SLOW_REQUEST_IDENTITY_INCOMPLETE": "活动请求身份字段不完整",
    "SLOW_REQUEST_SNAPSHOT_UNAVAILABLE": "活动请求快照不可用",
    "PLAN_GENERATION_UNAVAILABLE": "没有唯一的计划代次统计",
    "PLAN_GENERATION_AMBIGUOUS": "计划代次统计存在歧义",
    "STATEMENT_IDENTITY_MISMATCH": "语句身份不匹配",
    "PLAN_IDENTITY_MISMATCH": "计划身份不匹配",
    "AMBIGUOUS_NATIVE_IDENTITY_MATCH": "原生身份匹配存在歧义",
    "Association reason": "关联原因",
    "NOT_ATTEMPTED": "未尝试身份关联",
    "NOT_CONFIGURED": "未配置",
    "READY": "就绪",
    "ERROR": "错误",
    "LOCKED": "已锁定",
    "LIMITED": "受限",
    "NOTE": "说明",
    "MISSING": "缺失",
    "server_version_num（原始值）": "server_version_num（原始值）",
    "Top SQL Summary": "Top SQL 摘要",
    "SQL Ref": "SQL 引用",
    "Session ID": "会话 ID",
    "Request ID": "请求 ID",
    "CPU Time": "CPU 时间",
    "Elapsed Time": "运行耗时",
    "Wait Time (ms)": "等待时长（毫秒）",
    "Blocking Session": "阻塞会话",
    "Root Blocker Status": "根阻塞状态",
    "Database Version": "数据库版本",
    "Collection Status": "采集状态",
    "Collection status": "采集状态",
    "Completeness": "证据完整性",
    "Data Visibility": "数据可见性",
    "Returned Records": "返回记录数",
    "Metric Name": "指标名称",
    "Metric Unit": "指标单位",
    "Metric Semantics": "指标语义",
    "Metric Scope": "统计范围",
    "Collection Scope": "采集范围",
    "Stats Reset": "统计重置时间",
    "Planning Time Included": "是否包含规划耗时",
    "Queryid Stability": "Queryid 稳定范围",
    "Statement Instrument Count": "语句监控项数量",
    "Statement Instrument Disabled Count": "已关闭的语句监控项数量",
    "Statement Instrument Untimed Count": "未计时的语句监控项数量",
    "Instance Id": "实例 ID",
    "Container Id": "容器 ID",
    "Container Name": "容器名称",
    "Limitation Reasons": "限制原因",
    "Error Type": "错误类型",
    "SQL Text Association": "SQL 身份关联",
    "Ranking Metric": "排序指标",
    "Ranking Direction": "排序方向",
    "Profile ID": "采集配置 ID",
    "Collection Timestamp": "采集时间",
    "Evidence Source": "证据来源",
    "Request Status": "请求状态",
    "Wait Type": "等待类型",
    "Requested (KB)": "请求内存（KB）",
    "Required (KB)": "所需内存（KB）",
    "Granted (KB)": "已授予内存（KB）",
    "Used (KB)": "已用内存（KB）",
    "Max Used (KB)": "最大已用内存（KB）",
    "Queue ID": "队列 ID",
    "Wait Order": "等待顺序",
    "Request Identity": "请求身份",
    "Health Assessment": "健康评估",
    "Identity Conflicts": "身份冲突数",
    "Overall": "总体状态",
    "Client elapsed": "客户端耗时",
    "Sampled": "采样时间",
    "Truncated": "是否截断",
    "Unavailable": "不可用",
    "Interpretation": "说明",
    "Detailed HTML Report": "详细 HTML 报告",
    "Identity Status": "身份状态",
    "Query Hash": "Query Hash",
    "SQL Handle": "SQL Handle",
    "Statement Offsets": "语句偏移量",
    "Database ID Context": "数据库 ID 上下文",
    "Plan Handle(s)": "计划句柄",
    "Text Status": "文本状态",
    "No blocker": "无阻塞者",
    "Blocker": "阻塞者",
    "Primary ranking": "主要排序指标",
    "Top N": "Top N",
    "Scope": "采集范围",
    "Collection": "采集状态",
    "Profile": "配置档案",
    "Visibility": "数据可见性",
    "Metric": "指标",
    "Unit": "单位",
    "Semantics": "指标语义",
    "Item Scope": "指标统计范围",
    "Direction": "排序方向",
    "Collected At": "采集时间",
    "Ranked SQL": "Top SQL 排名",
    "Native identity": "原生身份",
    "Limitations": "限制原因",
    "Source Metadata": "来源元数据",
    "Connection Successful": "连接成功",
    "Connection Failed": "连接失败",
    "Database Status": "数据库状态",
    "Current Health": "当前健康状况",
    "Suggested Next Step": "建议下一步",
    "Business reports slow:": "业务报告缓慢时：",
    "Disk/log growth:": "磁盘或日志增长时：",
    "No obvious active issue detected.": "当前采样未发现明显活动异常。",
    "No core storage risk observed in available required evidence.": "现有必需证据未显示核心存储风险。",
    "Connection failed; vendor error details are available below.": "数据库连接失败；详细错误信息见下方。",
    "Some snapshot data is incomplete; review warnings below.": "部分快照数据不完整，请查看下方提示。",
    "No active query in available sample.": "当前采样中没有活动查询。",
    "No active SQL in available sample.": "当前采样中没有活动 SQL。",
    "No lock wait observed in available sample.": "当前采样中未观测到锁等待。",
    "No blocking observed in available sample.": "当前采样中未观测到阻塞。",
    "No blocking edge observed in available sample.": "当前采样中未观测到阻塞关系。",
    "No positive blocking edge observed in this sample.": "当前采样中未观测到明确的阻塞关系。",
    "No InnoDB transaction in available sample.": "当前采样中没有 InnoDB 事务记录。",
    "No confirmed idle transaction in available sample.": "当前采样中没有已确认的空闲事务。",
    "No session rows available.": "当前没有可用的会话记录。",
    "No request rows available.": "当前没有可用的请求记录。",
    "No configured finding in available sample.": "当前采样中没有匹配的诊断发现。",
    "No configured finding in this sample.": "当前采样中没有匹配的诊断发现。",
    "Review current activity and workload baseline.": "核查当前活动，并与正常负载基线比较。",
    "Review current activity and application baseline.": "核查当前活动，并与应用正常负载基线比较。",
    "Review the owning transaction.": "核查事务所属会话及其业务状态。",
    "Review the owning transaction and log reuse evidence.": "核查事务所属会话及日志复用证据。",
    "Review file capacity and underlying volume with the DBA; no resize was attempted.": "请 DBA 核查数据文件容量及所在存储卷；本工具未尝试扩容。",
    "Review TempDB consumers and file capacity; do not shrink automatically.": "核查 TempDB 使用者与文件容量；不要自动收缩文件。",
    "Review log capacity and reuse reason; do not shrink automatically.": "核查日志容量和复用原因；不要自动收缩日志。",
    "Investigate the reported log reuse condition before any manual action.": "人工处理前先核查报告的日志复用等待原因。",
    "Review slot availability and consumer state manually; do not delete the slot automatically.": "人工核查复制槽可用性和消费者状态；不要自动删除复制槽。",
    "Confirm consumer state; ask the DBA to review slot retention manually.": "确认消费者状态，并由 DBA 人工核查复制槽保留情况。",
    "Review archiver destination and recent error evidence.": "核查归档目标和近期错误证据。",
    "Review transaction owner and WAL retention conditions.": "核查事务所属会话及 WAL 保留条件。",
    "Review retention settings and backup/replication requirements; do not purge automatically.": "核查保留设置、备份和复制要求；不要自动清理日志。",
    "Review receiver state and relay-log files manually; size is unavailable here.": "人工核查接收线程状态和中继日志文件；当前无法获得其大小。",
    "Review transaction owner and log impact.": "核查事务所属会话及其对日志的影响。",
    "Review tablespace allocation and underlying storage manually.": "人工核查表空间分配和底层存储容量。",
    "Review temporary-space consumers and capacity manually.": "人工核查临时空间使用者和剩余容量。",
    "Review FRA components and retention with the DBA; do not delete files automatically.": "请 DBA 核查 FRA 组成和保留策略；不要自动删除文件。",
    "Review flashback retention manually.": "人工核查 Flashback 保留策略。",
    "Review archive destination status manually.": "人工核查归档目标状态。",
    "Compare archive generation with backup and standby consumption; do not delete logs.": "比较归档生成速度、备份和备库消耗情况；不要删除日志。",
    "Review the execution plan and runtime metrics in the detailed report.": "在详细报告中核查执行计划和运行指标。",
    "Review replication transport, apply state, and vendor evidence with the database owner.": "与数据库负责人共同核查复制传输、应用状态和数据库证据。",
    "Review the detailed report and validate the observed database impact.": "核查详细报告，并结合业务证据确认观测到的数据库影响。",
    "No obvious active database-side issue detected in this snapshot.": "当前快照未发现明显的数据库侧活动异常。",
    "Review the observed replication evidence with the database owner; Toolkit performs no repair.": "与数据库负责人共同核查复制证据；Toolkit 不执行修复。",
    "No replication configuration was observed on this instance.": "当前实例未观测到复制配置。",
    "Required replication role or status evidence could not be collected.": "无法采集必需的复制角色或状态证据。",
    "An inactive physical slot may retain WAL without a connected consumer.": "未活动的物理复制槽可能在没有连接消费者时继续保留 WAL。",
    "Replication slot reports WAL that is at risk of removal or is no longer available.": "复制槽报告的 WAL 面临被移除的风险或已不可用。",
    "Replication receiver thread is not running.": "复制接收线程未运行。",
    "Replication applier thread is not running.": "复制应用线程未运行。",
    "Availability Group replica is disconnected.": "可用性组副本当前未连接。",
    "Availability Group database is not synchronizing.": "可用性组数据库当前未同步。",
    "Observed source-to-applier time lag exceeds the configured delay.": "观测到的源端至应用端时间延迟超过配置值。",
    "Seconds_Behind_Source is NULL while a replication thread is stopped or disconnected.": "复制线程停止或断开时，Seconds_Behind_Source 为 NULL。",
    "Observed relay-log storage is large; one snapshot cannot infer growth trend.": "观测到的中继日志占用较大；单次快照无法推断增长趋势。",
    "Standby recovery is active but no WAL receiver is visible.": "备库恢复正在运行，但未观测到 WAL 接收器。",
    "Observed WAL retained by a slot exceeds the configured policy threshold; trend is unknown.": "观测到复制槽保留的 WAL 超过策略阈值；增长趋势未知。",
    "Managed recovery reports an error or archive gap.": "托管恢复报告错误或归档缺口。",
    "Oracle archive destination reports an error.": "Oracle 归档目标报告错误。",
    "Oracle database role could not be mapped from V$DATABASE.": "无法根据 V$DATABASE 映射 Oracle 数据库角色。",
    "No managed recovery process was returned for this standby snapshot.": "此备库快照未返回托管恢复进程记录。",
    "No replication channel was returned and local read-only state is unavailable.": "未返回复制通道，且本地只读状态不可用。",
    "DBA Toolkit Check": "DBA Toolkit 检查",
    "DBA Toolkit Doctor": "DBA Toolkit 本机检查",
    "Common DBA Commands": "常用 DBA 命令",
    "Replication Health": "复制健康状况",
    "HTML Report": "HTML 报告",
    "None": "无",
    "DBA Toolkit Runtime": "DBA Toolkit 运行环境",
    "Database Drivers": "数据库驱动",
    "Managed Driver Versions": "受管驱动版本",
    "Driver Information": "驱动信息",
    "Credential Store Test": "凭据存储测试",
    "Credential Backends": "凭据后端",
    "Credential Session": "凭据会话",
    "Credential Security": "凭据安全",
    "Credential Crypto Dependencies": "凭据加密依赖",
    "Selected Provider": "当前提供方",
    "Connection": "连接",
    "Runtime": "运行环境",
    "Account Security": "账号安全",
    "Core Runtime": "核心运行环境",
    "Database Runtimes": "数据库运行组件",
    "Version Detection": "版本检测",
    "Secondary Signals": "次要信号",
    "Collector Summary": "采集器摘要",
    "Incident Summary": "事件摘要",
    "Snapshot": "快照",
    "Time": "时间",
    "Primary Problem": "主要问题",
    "Observed Now": "当前观测",
    "Timing": "时间信息",
    "Category": "类别",
    "Event": "事件",
    "Transaction Age": "事务时长",
    "Source": "来源",
    "Immediate Action": "立即处理建议",
    "Recovery Action": "处置建议",
    "Termination Risk": "终止风险",
    "Safety": "安全边界",
    "No automatic or destructive remediation generated.": "未生成自动或破坏性处置操作。",
    "No session termination command generated.": "未生成会话终止参考命令。",
    "No session termination recommended from current evidence.": "当前证据不支持建议终止会话。",
    "Review SQL and execution-plan evidence in the detailed report.": "请在详细报告中检查 SQL 和执行计划证据。",
    "No confirmed blocking root eligible for a reference command.": "没有已确认且满足条件的阻塞根会话可生成参考命令。",
    "Detailed Report": "详细报告",
    "Plan Tree (optimizer estimates; no actual row-source statistics):": "执行计划树（优化器估算值；不含实际行源统计）：",
    "No complete cached plan tree is available.": "没有可用的完整缓存执行计划树。",
    "HTML report:": "HTML 报告：",
    "Plan is read from the current shared pool; cursor load generation is not captured.": "执行计划读取自当前共享池；未采集游标载入代次。",
    "Adaptive plan candidate/final branches are not classified.": "未区分自适应执行计划的候选分支与最终分支。",
    "Top SQL": "Top SQL 资源排序",
    "Warning": "警告",
    "Error": "错误",
    "Credential Not Found": "未找到凭据",
    "Credential Store Error": "凭据存储错误",
    "Database Runtime Missing": "数据库运行组件缺失",
    "Monitoring Account Rejected": "监控账号被拒绝",
    "Database Rejected Query": "数据库拒绝了查询",
    "Database Connection Failed": "数据库连接失败",
    "Operation Failed": "操作失败",
    "Fix": "处理方式",
    "Suggested Steps": "建议操作",
    "What To Check": "检查方向",
    "Vendor Error": "数据库错误信息",
    "Toolkit Classification": "Toolkit 分类",
    "Toolkit Guidance": "Toolkit 建议",
    "Effective Connection": "生效连接",
    "Database Permission Guidance": "数据库权限说明",
    "Privilege Trigger": "权限风险触发证据",
    "Code": "错误码",
    "SQLSTATE": "SQLSTATE",
    "Message": "错误详情",
    "Detail": "详细信息",
    "Hint": "提示",
    "Context": "上下文",
    "Feature": "功能",
    "Account": "账号",
    "SSL": "SSL",
    "Connect Timeout": "连接超时",
    "Port": "端口",
    "Permission": "权限",
    "Principal": "主体",
    "Object": "对象",
    "Platform": "数据库平台",
    "Local Role": "本地角色",
    "Vendor Role": "数据库角色",
    "Replication Mode": "复制模式",
    "Transport": "传输状态",
    "Apply / Replay": "应用状态",
    "Members": "成员数",
    "Issues": "问题数",
    "Lag / Backlog": "延迟与积压",
    "Candidate only; validate against database and application evidence.": "当前仅为候选判断，请结合数据库和应用证据核实。",
    "No fault candidate in the available snapshot.": "当前快照没有故障候选。",
    "Not applicable because replication is not configured.": "未配置复制，不适用。",
    "No lag metric was returned.": "没有返回复制延迟指标。",
    "No replication issue was observed in this snapshot.": "当前快照未观测到复制异常。",
    "Recommended DBA Review": "建议 DBA 核查",
    "No additional action is indicated by this snapshot.": "当前快照未显示其他建议操作。",
    "Reference Commands": "人工参考命令",
    "Topology Members": "拓扑成员",
    "Credential alias was not found in the injected store.": "所选凭据存储中不存在该凭据别名。",
    "Credential alias was not found in secure stores.": "安全凭据存储中不存在该凭据别名。",
    "Credential alias was not found.": "未找到该凭据别名。",
    "OS secure credential store is unavailable.": "操作系统安全凭据存储当前不可用。",
    "Credential session metadata is unavailable.": "凭据会话元数据不可用。",
    "MockCredentialProvider is prohibited in production.": "生产环境禁止使用 MockCredentialProvider。",
    "Inventory instance is disabled.": "该数据库资产已禁用。",
    "Not implemented for this database type.": "该数据库类型尚未实现此功能。",
    "Connection cleanup failed; check server session state.": "连接清理失败，请核查服务器会话状态。",
    "MySQL connection preflight did not complete.": "MySQL 连接前置检查未完成。",
    "Top SQL requires the validated metric and limit binding interface.": "Top SQL 必须通过已校验的指标和行数绑定接口执行。",
    "Plan diagnosis requires --session with a positive session ID.": "执行计划诊断必须通过 --session 指定正整数会话 ID。",
    "Oracle Plan identity options cannot be used with SQL Server.": "Oracle 执行计划身份参数不能用于 SQL Server。",
    "Oracle Plan uses --sql-id and --child-number, not --session.": "Oracle 执行计划使用 --sql-id 和 --child-number，不使用 --session。",
    "Plan identity options are only supported for plan diagnosis.": "执行计划身份参数仅适用于执行计划诊断。",
    "Plan diagnosis is not implemented for this database type.": "该数据库类型尚未实现执行计划诊断。",
    "Connection timed out; check configured limits.": "连接超时，请检查已配置的超时限制。",
    "Database connection failed.": "数据库连接失败。",
    "Operation interrupted.": "操作已中断。",
    "Target": "目标地址",
    "Python": "Python",
    "Driver": "驱动",
    "Driver Version": "驱动版本",
    "Alias": "别名",
    "Backend": "后端",
    "TLS": "TLS",
    "Classification": "分类",
    "Declared Family": "声明版本系列",
    "Detected Version": "检测到的版本",
    "Detected Family": "检测到的版本系列",
    "Version Status": "版本状态",
    "Active Sessions": "活动会话数",
    "Active Requests": "活动请求数",
    "Database States": "数据库状态统计",
    "Credential": "凭据",
    "Encryption": "加密算法",
    "KDF": "密钥派生算法",
    "Format": "格式",
    "Permission Check": "权限检查",
    "Dependencies": "依赖项",
    "Packages": "软件包",
    "Remaining": "剩余时间",
    "Native": "原生驱动",
    "Plan Tree": "执行计划树",
    "Oracle Cached Execution Plan": "Oracle 缓存执行计划",
    "Root cause candidate": "根因候选",
    "Most likely": "高度疑似",
    "No matching active request was observed.": "未观测到匹配的活动请求。",
    "Toolkit did NOT execute this command.": "Toolkit 未执行此命令。",
    "DBA approval is required.": "需要 DBA 审批。",
    "Reason: required evidence is incomplete.": "原因：必需证据不完整。",
    "Reason: evidence incomplete.": "原因：证据不完整。",
    "NOT_SET": "未设置",
    "SUPPORTED": "支持",
    "DETECTED": "已检测",
    "ABSENT": "不存在",
    "AVAILABLE": "可用",
    "NOT_AVAILABLE": "不可用",
    "LIMIT_REACHED": "已达到上限",
    "SUCCESS": "成功",
    "CANCELLED": "已取消",
    "RUNNING": "运行中",
    "SUSPENDED": "已挂起",
    "RUNNABLE": "可运行",
    "WAITING": "等待中",
    "GRANTED": "已授予",
    "NOT_GRANTED": "未授予",
    "NOT_STARTED": "未开始",
    "UNKNOWN_STATE": "状态未知",
    "Blocked Victims": "被阻塞会话",
    "Active Wait Statements": "主动等待语句",
    "Transaction Identity Quality": "事务身份质量",
    "WAL Retention and Replication Slots": "WAL 保留与复制槽",
    "Oracle Cached Plan Identity": "Oracle 缓存执行计划身份",
    "Plan Semantics and Limitations": "计划语义与限制",
    "Cached Plan Availability": "缓存执行计划可用性",
    "Cached Plan Tree": "缓存执行计划树",
    "Transport, Apply / Replay and Lag": "传输、应用与延迟",
    "Observed Backlog": "观测到的积压",
    "Issues and Vendor Evidence": "问题与数据库证据",
    "Report Evidence Limit": "报告证据上限",
    "Metric value": "指标值",
    "Wait Detail": "等待详情",
    "MySQL connection ID": "MySQL 连接 ID",
    "Area": "资源类别",
    "Value": "观测值",
    "Item": "条目",
    "Observed": "观测值",
    "REVIEW_REQUIRED": "需要人工复核",
    "VERIFIED": "事务关联已核验",
    "VERIFIED_AT_SAMPLE": "采样时核验通过",
    "AMBIGUOUS": "身份存在冲突",
    "TRANSACTION_LOG": "事务日志",
    "WAL": "WAL",
    "BINLOG": "Binlog",
    "REDO": "Redo",
    "ARCHIVE": "归档",
    "FRA": "FRA",
    "UNDO": "Undo",
    "TEMP": "临时空间",
    "DATA": "数据文件",
    "TABLE": "表",
    "INDEX": "索引",
    "ROOT_BLOCKER": "根阻塞",
    "IDLE_OPEN_TRANSACTION": "未结束事务",
    "BLOCKED_VICTIM": "被阻塞会话",
    "ACTIVE_WAIT": "主动等待语句",
    "TERMINATE_SESSION": "终止会话参考",
    "CANCEL_QUERY": "取消查询参考",
    "RECHECK_REQUIRED": "需要重新核验",
    "Waiting for Memory Grant": "等待 Memory Grant",
    "Granted Memory": "已授予的内存",
    "SQL Ordered by CPU Time": "按 CPU 时间排序的当前 SQL",
    "SQL Ordered by Logical Reads": "按逻辑读排序的当前 SQL",
    "SQL Ordered by Elapsed Time": "按运行耗时排序的当前 SQL",
    "SQL Text": "SQL 文本",
    "Execution Plan Availability": "执行计划可用性",
    "Execution Plan Summary": "执行计划摘要",
    "Important Operators": "关键算子",
    "Warnings and Index Evidence": "警告与索引证据",
    "Raw Execution Plan": "原始执行计划",
    "Replication Summary": "复制摘要",
    "Topology": "拓扑结构",
    "Recommended Actions": "建议操作",
    "Oracle Root Blocker SQL Context": "Oracle 根阻塞会话 SQL 上下文",
    "Performance Triage": "性能分诊",
    "Root": "根会话",
    "Display summary": "汇总行",
    "Incident-related sessions": "与事件相关的会话",
    "Begin Time Source": "事务开始时间来源",
    "Required evidence unavailable": "必需证据不可用",
    "Required evidence was not collected.": "必需证据未采集。",
    "Optional context was not collected.": "可选上下文未采集。",
    "source unavailable": "数据来源不可用",
    "No evidence reference": "没有证据引用",
    "Wait Event": "等待事件",
    "Current SQL_ID": "当前 SQL_ID",
    "Current SQL / Availability": "当前 SQL / 可用状态",
    "Previous SQL / Availability": "前序 SQL / 可用状态",
    "Start Time": "开始时间",
    "Start Time Source": "开始时间来源",
    "START_TIME Raw": "START_TIME 原始值",
    "Duration Seconds": "持续时间（秒）",
    "Manual Action": "人工处置建议",
    "Suppression Reason": "未生成建议的原因",
    "Context Note": "上下文说明",
    "Slot collection": "复制槽采集状态",
    "Settings collection": "配置采集状态",
    "Archive collection": "归档采集状态",
    "Current LSN": "当前 LSN",
    "LSN source": "LSN 来源",
    "Observed slot count": "观测到的复制槽数量",
    "WAL keep size": "WAL 保留容量",
    "Max slot WAL keep size": "复制槽 WAL 保留上限",
    "Max WAL size": "WAL 容量上限",
    "Restart LSN": "重启 LSN",
    "Confirmed LSN": "确认 LSN",
    "WAL status": "WAL 状态",
    "Safe WAL bytes": "安全 WAL 字节数",
    "LSN distance bytes": "LSN 距离（字节）",
    "Slot count unknown; collection unavailable.": "复制槽数量未知；采集不可用。",
    "Slot result truncated; total slot count is unknown.": "复制槽结果已截断，无法确定复制槽总数。",
    "Execution plan unavailable; no plan evidence was returned.": "执行计划不可用；未返回计划证据。",
    "A complete cached plan is unavailable.": "完整缓存执行计划不可用。",
    "Object / Index": "对象 / 索引",
    "Estimated Rows": "估算行数",
    "Actual Rows": "实际行数",
    "Estimated Cost": "估算成本",
    "Estimated Bytes": "估算字节数",
    "Parallel Plan": "并行计划",
    "Optimization Level": "优化级别",
    "Missing Index Evidence": "缺失索引证据",
    "Implicit Conversion": "隐式转换",
    "Plan Source": "执行计划来源",
    "Plan Status": "计划状态",
    "Identity Validation": "身份校验",
    "Operator Count": "算子数量",
    "Maximum Operators": "算子上限",
    "Operation / Options": "操作 / 选项",
    "Snapshot Time": "快照时间",
    "Recommended DBA Action": "建议 DBA 处理方式",
    "Candidate": "候选",
    "Evidence": "证据",
    "Member": "成员",
    "Local": "本地",
    "Backlog": "积压",
    "Issue": "问题",
    "Requested Plan Hash": "请求的计划哈希",
    "Current Plan Hash": "当前计划哈希",
    "Child Number": "子游标编号",
    "Instance ID": "实例 ID",
    "Container ID": "容器 ID",
    "Estimate semantics": "估算语义",
    "Adaptive plan": "自适应执行计划",
    "Cursor generation": "游标载入代次",
    "Plan source": "执行计划来源",
    "Current shared-pool identity only; load generation is not captured.": "仅能确认当前共享池中的游标身份；未采集载入代次。",
    "Current V$SQL_PLAN cache rows; no business SQL execution or EXPLAIN.": "证据来自当前 V$SQL_PLAN 缓存记录；未执行业务 SQL 或 EXPLAIN。",
    "A review rank is not a confirmed root cause.": "核查排序不等于已确认根因。",
    "No complete section was omitted.": "未省略完整报告区块。",
    "See --json for structured diagnostic data.": "结构化诊断数据请使用 --json 查看。",
    "Use --json for structured diagnostic data.": "结构化诊断数据请使用 --json 查看。",
    "Report evidence was reduced to stay within the human report size budget.": "报告内容已按人工报告大小上限收敛。",
    "Report evidence was reduced because the human report size limit was reached.": "报告达到大小上限，已收敛部分证据内容。",
    "identity unavailable": "身份不可用",
    "Top SQL was not collected.": "未采集 Top SQL。",
    "Collection status=": "采集状态=",
    "; completeness=": "；完整性=",
    "; data visibility=": "；数据可见性=",
    ". Limitations: ": "。限制原因：",
    "Error type": "错误类型",
    "Native session identity": "会话原生身份",
    "Classification labels": "分类标签",
    "Evidence status": "证据状态",
    "Rule IDs": "规则 ID",
    "Evidence source / time / integrity": "证据来源 / 时间 / 完整性",
    "Uncertainty": "不确定因素",
    "DBA review required": "需要 DBA 复核",
    "Toolkit Executed: NO. No automatic failover, promotion, restart, reset, or repair.": "Toolkit 未执行任何操作；不会自动切换、提升、重启、重置或修复。",
    "characters omitted by report limit.": "字符因报告上限省略。",
    "text blocks omitted": "个文本块已省略",
    "Top SQL rows": "Top SQL 记录",
    "blocking nodes": "阻塞节点",
    "root blockers": "根阻塞会话",
    "plan operators": "执行计划算子",
    "plan warnings": "执行计划警告",
    "plan nodes": "执行计划节点",
    "lag metrics": "延迟指标",
    "backlog metrics": "积压指标",
    "SQL text omitted by human report limit.": "SQL 文本因人工报告长度上限而省略。",
    "statement omitted by report limit": "语句因报告长度上限而省略",
    "SQL text omitted by 1000 character report limit.": "SQL 文本因 1000 字符报告上限而省略。",
    "Top SQL rank": "Top SQL 排名",
    "exactly matched Slow SQL": "与慢 SQL 精确匹配",
    "This scenario does not assess overall database health.": "本场景不评估数据库整体健康度。",
    "Confirmed": "已确认",
    "Further investigation required": "待进一步排查",
    "BEST_EFFORT": "尽力采样",
    "NOT_ESTABLISHED": "尚未确认",
    "CONFIRMED": "已确认",
    "HIGHLY_SUSPECTED": "高度疑似",
    "NEEDS_INVESTIGATION": "待进一步排查",
    "Server SQL execution duration is not separately measured.": "未单独测量服务器端 SQL 执行耗时。",
    "The samples are not an atomic server snapshot.": "这些采样不是同一时刻的服务器快照。",
    "Waiting and granted rows were sampled using separate sequential read-only queries.": "等待记录和已授予记录由两条顺序执行的只读查询分别采样。",
    "A missing request join preserves the grant row while marking request context unavailable.": "请求关联缺失时仍保留内存授予记录，并标明请求上下文不可用。",
    "Elapsed time is client end-to-end, including driver execution, network fetch, and row parsing;": "耗时为客户端端到端时间，包含驱动执行、网络读取和结果解析；",
    "server-side SQL execution duration is not separately measured.": "不单独表示服务器端 SQL 执行耗时。",
}

DISPLAY_VALUES = {
    "UNKNOWN": "未知", "HEALTHY": "健康", "DEGRADED": "需关注",
    "NOT_DETECTED": "未观测到", "NOT_ASSESSED": "未评估", "ACTIVE": "活动",
    "COMPLETE": "完整", "CRITICAL": "严重", "WARNING": "警告", "HIGH": "高", "INFO": "提示",
    "RECHECK_REQUIRED": "需要重新核验", "VERIFIED": "事务关联已核验",
    "VERIFIED_AT_SAMPLE": "采样时核验通过", "AMBIGUOUS": "身份存在冲突",
    "IDENTITY_INCOMPLETE": "身份信息不完整",
    "ROOT_BLOCKER": "根阻塞", "IDLE_OPEN_TRANSACTION": "未结束事务",
    "BLOCKED_VICTIM": "被阻塞会话", "LONG_RUNNING_SQL": "慢 SQL",
    "ACTIVE_WAIT": "主动等待语句", "TERMINATE_SESSION": "终止会话参考",
    "CANCEL_QUERY": "取消查询参考", "TRANSACTION_LOG": "事务日志", "WAL": "WAL",
    "BINLOG": "Binlog", "REDO": "Redo", "ARCHIVE": "归档", "FRA": "FRA",
    "UNDO": "Undo", "TEMP": "临时空间", "DATA": "数据文件", "TABLE": "表",
    "INDEX": "索引",
}
_MODEL_VALUE_LABELS = DISPLAY_VALUES
_STATUS_DISPLAY_VALUES = {
    key: value for key, value in DISPLAY_LABELS.items()
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
}
_STATUS_DISPLAY_VALUES.update(DISPLAY_VALUES)
_STATUS_DISPLAY_VALUES.update({"sleeping": "休眠", "suspended": "挂起", "running": "运行中"})
_REPORT_ENUM_VALUES = {
    "SQL_STATEMENT": "SQL 语句", "DIGEST": "摘要统计", "CHILD_CURSOR": "子游标",
    "INSTANCE": "实例级", "DATABASE": "数据库级", "DESCENDING": "降序",
    "ASCENDING": "升序", "microseconds": "微秒", "milliseconds": "毫秒",
    "seconds": "秒", "bytes": "字节", "rows": "行",
}
_DISPLAY_LABEL_LOOKUP = {
    key.casefold(): value for key, value in DISPLAY_LABELS.items()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
    and key not in {"sleeping", "suspended", "running"}
}
_STATUS_FIELDS = {
    "status", "severity", "completeness", "data completeness", "data visibility",
    "wait category", "主要等待类别",
    "snapshot consistency",
    "collection status", "collection", "identity status", "identity quality",
    "sql text association", "text status", "request status", "health assessment",
    "overall", "transport", "apply / replay", "plan status", "plan availability",
    "evidence status", "connection status", "version status", "root blocker status",
    "采集状态", "状态", "严重程度", "证据完整性", "数据可见性", "身份状态",
    "身份可信度", "SQL 身份关联", "文本状态", "请求状态", "健康评估",
    "总体状态", "传输状态", "应用状态", "执行计划状态", "证据状态", "连接状态",
    "版本状态", "根阻塞状态",
}
_DATABASE_TYPES = {
    "sqlserver": "SQL Server", "mysql": "MySQL",
    "postgresql": "PostgreSQL", "oracle": "Oracle",
}
_SCENARIOS = {
    "slow": "慢 SQL", "plan": "执行计划", "active-sql": "活动 SQL",
    "top-sql": "Top SQL", "memory-grant": "Memory Grant",
    "disk-growth": "磁盘增长", "replication": "复制状态",
}
_ERROR_TITLES = {
    "CredentialNotFound": "未找到凭据",
    "CredentialStoreUnavailable": "凭据存储不可用",
    "CredentialStoreFailed": "凭据存储操作失败",
    "CredentialStoreInvalid": "凭据存储格式无效",
    "CredentialSessionUnavailable": "凭据会话不可用",
    "DriverUnavailable": "数据库运行组件缺失",
    "PrivilegedAccount": "监控账号不符合安全要求",
    "UnsafeMonitoringAccount": "监控账号安全检查未通过",
    "QueryDenied": "数据库拒绝只读查询",
    "ConnectionFailed": "数据库连接失败",
    "ConnectionTimeout": "数据库连接超时",
    "ConnectionTLSFailed": "数据库 TLS 校验失败",
    "RegistryRejected": "SQL 注册表校验未通过",
    "VersionVerificationRequired": "数据库版本尚未核验",
    "FeatureUnsupported": "当前版本不支持此功能",
    "FeatureSupportUnknown": "当前版本的功能支持状态未知",
    "NotImplemented": "当前功能尚未实现",
    "InvalidArgument": "命令参数无效",
    "InvalidMetric": "指标参数无效",
    "InvalidTopN": "Top SQL 行数参数无效",
    "InvalidSession": "会话参数无效",
    "InvalidPlanIdentity": "执行计划身份参数无效",
}
_ERROR_MESSAGES = {
    "CredentialNotFound": "未找到所选凭据别名。请检查别名，并按凭据管理流程完成安全登记。",
    "CredentialStoreUnavailable": "安全凭据存储不可用，操作已停止。请检查已选择的凭据后端及本机安全服务。",
    "CredentialStoreFailed": "凭据存储操作未完成，现有数据未被视为成功更新。请核查存储状态后重试。",
    "CredentialSessionUnavailable": "凭据会话不可用，数据库操作未继续。请检查凭据会话状态。",
    "DriverUnavailable": "所需数据库驱动不可用，未建立数据库连接。请先运行 dba doctor 检查组件状态，并按说明准备受支持的驱动。",
    "PrivilegedAccount": "监控账号不符合只读安全要求，诊断已停止。请使用专用最小权限账号。",
    "UnsafeMonitoringAccount": "监控账号安全检查未通过，诊断已停止。请核查账号属性和授权配置。",
    "QueryDenied": "数据库拒绝了受审核的只读查询。请保留数据库错误证据并查看数据库部署授权说明。",
    "ConnectionFailed": "数据库连接未建立，未采集诊断数据。请核对目标地址、驱动和监控凭据。",
    "ConnectionTimeout": "数据库连接超过配置的等待时间，未采集诊断数据。请核对目标可达性和超时设置。",
    "ConnectionTLSFailed": "数据库 TLS 身份校验未通过，连接已停止。请核对证书和主机名配置。",
    "RegistryRejected": "受审核的 SQL 未通过注册表或执行前完整性校验，查询未执行。请核查正式 SQL 资产。",
    "VersionVerificationRequired": "数据库版本未满足当前诊断配置要求，采集器未运行。请核对声明版本和检测结果。",
    "FeatureUnsupported": "当前数据库版本没有已验证的诊断配置，采集器未运行。请查看版本支持说明。",
    "FeatureSupportUnknown": "当前版本的功能支持尚未验证，采集器未运行。请先核实版本支持情况。",
    "NotImplemented": "当前数据库类型尚未实现此功能。",
    "InvalidArgument": "命令参数不符合当前操作要求。请按命令帮助检查参数。",
    "InvalidMetric": "所选指标不受当前数据库或配置支持。请查看可用指标列表。",
    "InvalidTopN": "Top SQL 结果行数超出允许范围。请调整行数参数。",
    "InvalidSession": "会话参数无效，未查询执行计划。请提供有效的正整数会话 ID。",
    "InvalidPlanIdentity": "执行计划身份参数无效，未采集计划。请核对 SQL_ID、子游标和可选实例身份。",
}
DISPLAY_MESSAGES = {
    key: value for key, value in DISPLAY_LABELS.items()
    if "." in key or key.endswith(":")
}

DISPLAY_FINDINGS = {
    "SQLSERVER_DATAFILE_NEAR_LIMIT": "SQL Server 数据文件接近容量上限",
    "SQLSERVER_TEMPDB_HIGH_USAGE": "SQL Server TempDB 使用率较高",
    "SQLSERVER_LOG_HIGH_USAGE": "SQL Server 事务日志使用率较高",
    "SQLSERVER_LOG_REUSE_BLOCKED": "SQL Server 事务日志复用受阻",
    "SQLSERVER_OLD_ACTIVE_TRANSACTION": "SQL Server 存在长期活动事务",
    "PG_WAL_RETENTION_SLOT": "PostgreSQL 复制槽保留 WAL",
    "PG_ARCHIVE_FAILURE": "PostgreSQL WAL 归档失败线索",
    "PG_LONG_TRANSACTION": "PostgreSQL 存在长期事务",
    "BINLOG_TOTAL_SIZE_HIGH": "MySQL Binlog 总量较大",
    "REPLICA_RELAY_LOG_RISK": "MySQL 复制中继日志风险",
    "LONG_TRANSACTION": "MySQL 存在长期事务",
    "ORACLE_UNDO_HIGH_USAGE": "Oracle Undo 使用率较高",
    "ORACLE_TEMP_HIGH_USAGE": "Oracle 临时空间使用率较高",
    "ORACLE_TABLESPACE_HIGH_USAGE": "Oracle 表空间使用率较高",
    "ORACLE_FRA_HIGH_USAGE": "Oracle FRA 使用率较高",
    "ORACLE_FLASHBACK_HIGH_USAGE": "Oracle Flashback 日志使用率较高",
    "ORACLE_ARCHIVE_DEST_FAILURE": "Oracle 归档目标异常",
    "ORACLE_ARCHIVE_ACCUMULATION": "Oracle 归档量较大",
}


def display_label(value: str) -> str:
    """Translate known UI labels and enum tokens; preserve unknown/native names."""
    status_value = _STATUS_DISPLAY_VALUES.get(value)
    if status_value is not None:
        return status_value
    normalized = " ".join(value.replace("_", " ").split())
    return _DISPLAY_LABEL_LOOKUP.get(normalized.casefold(), value)


def report_model_label(value: str) -> str:
    """Return a stable English key label for the internal ReportModel contract."""
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def display_finding(value: str) -> str:
    """Translate known finding identifiers while retaining unknown source text."""
    return DISPLAY_FINDINGS.get(value, display_label(value))


def display_value(value: Any) -> Any:
    """Translate exact display enum values without changing arbitrary DB evidence."""
    if isinstance(value, str):
        return _STATUS_DISPLAY_VALUES.get(value, value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


def display_status_value(field: str, value: str) -> str:
    """Translate enum-like values only when the field is explicitly a status."""
    normalized = " ".join(field.replace("_", " ").split()).casefold()
    return display_label(value) if normalized in _STATUS_FIELDS else value


def display_report_value(field: str, value: str) -> str:
    """Translate only known presentation text while preserving native evidence."""
    normalized = " ".join(field.replace("_", " ").split()).casefold()
    if normalized in _STATUS_FIELDS:
        return display_status_value(field, value)
    if normalized in {"metric semantics", "semantics"}:
        return display_label(value)
    if normalized in {"metric scope", "collection scope", "ranking direction", "metric unit"}:
        return _REPORT_ENUM_VALUES.get(value, value)
    if normalized in {"recommendation", "recommended dba action", "suggested next step",
                      "interpretation", "health assessment"}:
        return display_message(value)
    if normalized == "limitations":
        return ", ".join(display_label(item.strip()) for item in value.split(","))
    if normalized == "source metadata":
        fields = []
        for item in value.split("; "):
            key, separator, item_value = item.partition(": ")
            fields.append(f"{display_label(key)}{separator}{display_value(item_value) if separator else ''}")
        return "; ".join(fields)
    return value


def display_database_type(value: str) -> str:
    """Use official product spelling for internal database type values."""
    return _DATABASE_TYPES.get(value.casefold(), value)


def display_scenario(value: str) -> str:
    """Translate the fixed diagnostic scenario identifiers for human output."""
    return _SCENARIOS.get(value, value)


def display_error_title(error_type: str) -> str:
    """Translate known application error categories for human output."""
    return _ERROR_TITLES.get(error_type, "操作失败")


def display_error_message(error_type: str, message: str | None = None) -> str:
    """Render application error detail in Chinese without touching vendor evidence."""
    if message is not None:
        translated = display_message(message)
        if translated != message:
            return translated
    return _ERROR_MESSAGES.get(error_type, "操作未完成。请根据错误类别检查参数、运行环境或部署配置。")


def display_quality_impact(value: str) -> str:
    """Translate the controlled Top SQL collection-quality summary, preserving reason codes."""
    if not (value.startswith("采集状态=") and "；证据完整性=" in value
            and "；数据可见性=" in value):
        return value
    for label in ("采集状态", "证据完整性", "数据可见性"):
        prefix = f"{label}="
        start = value.find(prefix)
        if start < 0:
            continue
        value_start = start + len(prefix)
        end = value.find("；", value_start)
        if end < 0:
            end = value.find("。", value_start)
        if end < 0:
            end = len(value)
        raw_status = value[value_start:end]
        localized = _STATUS_DISPLAY_VALUES.get(raw_status)
        if localized is not None:
            value = value[:value_start] + localized + value[end:]
    marker = "限制原因："
    if marker in value:
        before, reason_text = value.split(marker, 1)
        suffix = "。" if reason_text.endswith("。") else ""
        if suffix:
            reason_text = reason_text[:-1]
        localized_reasons = ", ".join(display_label(item.strip()) for item in reason_text.split(","))
        value = f"{before}{marker}{localized_reasons}{suffix}"
    return value


def display_report_summary(value: str) -> str:
    """Translate known status values embedded in application-authored summaries."""
    value = display_message(value)
    labels = (
        "证据完整性", "数据完整性", "数据可见性", "采集状态", "完整性", "可见性",
        "应急证据", "完整配置证据", "必需证据", "可选证据", "一致性",
    )
    for label in labels:
        for separator in ("：", "="):
            prefix = f"{label}{separator}"
            start = 0
            while (index := value.find(prefix, start)) >= 0:
                value_start = index + len(prefix)
                end = next((position for position in (
                    value.find("；", value_start), value.find("。", value_start),
                    value.find("，", value_start), value.find(" ", value_start),
                ) if position >= 0), len(value))
                raw_status = value[value_start:end]
                localized = _STATUS_DISPLAY_VALUES.get(raw_status)
                if localized is not None:
                    value = value[:value_start] + localized + value[end:]
                    start = value_start + len(localized)
                else:
                    start = end + 1
    return value


def report_model_value(value: Any) -> Any:
    """Translate only established presenter enums, preserving raw report values."""
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, str):
        return _MODEL_VALUE_LABELS.get(value, value)
    return value


def display_message(value: str) -> str:
    """Translate an exact, application-owned user message; retain raw evidence."""
    translated = DISPLAY_MESSAGES.get(value)
    if translated is not None:
        return translated
    quoted_suffix = "version_family must be a quoted string"
    if value.endswith(quoted_suffix):
        prefix = value[:-len(quoted_suffix)]
        return prefix + "version_family 必须使用带引号的字符串。"
    supported_family = re.search(
        r"Unsupported non-canonical ([A-Za-z0-9_.-]+) version_family; supported: (.+)", value,
    )
    if supported_family:
        database = display_database_type(supported_family[1])
        prefix = value[:supported_family.start()]
        return f"{prefix}{database} 的 version_family 不受支持。支持的取值：{supported_family[2]}"
    return value

_SQLSERVER_TRANSACTION_TYPE = {1: "Read/Write", 2: "Read-Only", 3: "System", 4: "Distributed"}
_SQLSERVER_TRANSACTION_STATE = {
    0: "Not Initialized", 1: "Initialized", 2: "Active", 3: "Ended",
    4: "Commit Initiated", 5: "Prepared", 6: "Committed", 7: "Rolling Back", 8: "Rolled Back",
}


def sqlserver_transaction_label(value: Any, *, state: bool = False) -> str:
    """Translate documented SQL Server DMV enums; preserve unknown numeric values."""
    if type(value) is not int:
        return "—" if value is None else str(value)
    labels = _SQLSERVER_TRANSACTION_STATE if state else _SQLSERVER_TRANSACTION_TYPE
    return labels.get(value, f"Unknown ({value})")


def postgresql_version_label(value: Any) -> str:
    """Render PostgreSQL server_version or server_version_num consistently."""
    if value is None:
        return "UNKNOWN"
    if type(value) is int:
        return _postgresql_version_num_label(value)
    if not isinstance(value, str):
        return str(value)
    raw = value.strip()
    if raw.isdecimal() and len(raw) >= 5:
        return _postgresql_version_num_label(int(raw))
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if match is None:
        return raw or "UNKNOWN"
    major, minor = int(match[1]), int(match[2])
    if major >= 10:
        return f"{major}.{minor}"
    patch = match[3]
    return f"{major}.{minor}" + (f".{int(patch)}" if patch is not None else "")


def postgresql_server_version_num(value: Any) -> str | None:
    """Return a raw numeric PostgreSQL server_version_num without rewriting it."""
    if type(value) is int and value >= 10000:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and len(value) >= 5:
        return value
    return None


def _postgresql_version_num_label(value: int) -> str:
    if value < 10000:
        return str(value)
    if value >= 100000:
        return f"{value // 10000}.{value % 10000}"
    major, remainder = divmod(value, 10000)
    minor, patch = divmod(remainder, 100)
    return f"{major}.{minor}.{patch}"
