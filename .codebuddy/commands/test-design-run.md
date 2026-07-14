---
description: 由当前主会话协调确定性测试设计流水线；参数为 run-dir 绝对或仓库相对路径。
argument-hint: <run-dir>
---

# 测试设计多 Agent 协调命令

把 `$1` 作为唯一的 `run-dir`。若 `$1` 为空、目录不存在或不属于当前仓库，停止并要求用户提供有效路径；不得猜测批次目录。

当前**主会话**是唯一 coordinator。子 Agent 不得派生子 Agent，也不得改写编排状态。执行以下闭环，直至进入交付、出现需要用户确认的外部语义，或出现无法自动恢复的门禁失败。

## 0. 自动启动预检

先读取项目 `.codebuddy/settings.json`、`.codebuddy/hooks/guard-agent-tool.py`、5 个角色文件和当前 `agent-status`，并通过 `/agents`、`/hooks` 确认角色发现状态与 PreToolUse/PostToolUse 配置。项目 Agent 未出现在 UI、Agent 工具不可用、TLS/SSL 失败或平台不支持后台并行都属于正常适配状态，**不得询问用户是否改由主会话手工完成，也不得停止正式流程**。

正式优先使用受 hook 保护的 `codebuddy-subagent`。原生 Agent 不能派发时，必须走第 3 节的 supervisor 自动降级，把同一 claim 转换为 `codebuddy-isolated-fallback`；该身份不是 `codebuddy-main-session`，只能写当前任务白名单 output，仍经过相同 source fingerprint、提升、阶段门禁、逐元素 DFX、Cases、独立逻辑 Reviewer 和单写者 Delivery。

## 1. 推进与路由

为本次 coordinator 生成稳定 `coordinator_id`，命令重入时复用。运行：

`scripts/run-test-design.ps1 agent-run --run-dir "$1" --json`

解析 `state`、`claimed_tasks` 和 `runnable_tasks`。每个任务只按其 `agent-task.json` **绝对路径**派发，并按 `agent_role` 设置 `Agent` 工具的 `subagent_type`：

- `discovery` → `test-discovery`
- `plan_dfx` → `test-plan-dfx`
- `risk_arbiter` → `test-risk-arbiter`
- `case_worker` → `test-case-worker`
- `reviewer` → `test-reviewer`

`TaskCreate`、IDE Agent 页中的顶层任务和自然语言角色扮演都不是项目 sub-agent 派发，不得替代 `Agent(subagent_type=...)`。

## 2. 领取前的 Discovery 页面探针

每个任务在 claim 前先生成稳定、唯一、可重放的逻辑 `execution_id`、`executor_id` 和 `wave_id`。它们不是平台事后返回的物理执行句柄。

若 runnable task 为 Discovery，必须在 claim 前完成以下门禁；非 Discovery 跳到第 3 节：

1. 主会话调用 `WaitForMcpServers`，再用 `ToolSearch` 定位当前 Windows 会话已连接、经部署方审核且具等价数据/权限边界的页面控制 MCP，并盘点本批预计使用的全部 exact page tools。若 MCP 将 snapshot、click、fill、select、navigate 等能力拆成不同工具，每个预计使用的 exact tool 都必须进入本次预探清单；不得只探 click 后假设其他工具已授权。
2. 通过 `DeferExecuteTool` 或精确的直接 MCP 调用，在同一 session、transcript 和 MCP server namespace 下逐个成功实调预探清单中的每个 exact tool；操作必须低风险、可恢复，并保存可判定结果。整组记录至少包含一次按物理调用顺序排列的“前置读取 → 实际点击/选择/输入等 mutation → 响应变化后的读取”，前后读取必须成功、非空且响应发生可判定变化；至少选择 3 条记录。无法安全预探的必需工具不得加入授权，也不得 claim Discovery。
3. 每次成功调用后读取 `PostToolUse` 追加的 `PAGE_PROBE_RECORD` 上下文，按调用顺序收集 `record_id`；全部记录的 `session_sha256` 和 `transcript_sha256` 必须一致。不得手工构造、改写或复用记录。
4. 将可判定结果保存为至少一个非空、脱敏的批次证据，路径必须位于 `artifacts/page-probe-evidence/<execution_id>/`；二进制证据同时满足敏感信息审计 sidecar 规则。
5. 用同一个 `execution_id`、`coordinator_id`、session/transcript 哈希、按顺序重复的 `--record-id` 和 run-dir 相对 `--evidence` 提交探针：

   `scripts/run-test-design.ps1 page-probe-commit --run-dir "$1" --task-id <task_id> --execution-id <execution_id> --coordinator-id <coordinator_id> --session-sha256 <session_sha256> --transcript-sha256 <transcript_sha256> --record-id <前读record_id> --record-id <变更record_id> --record-id <后读record_id> --evidence "artifacts/page-probe-evidence/<execution_id>/<证据文件>" --json`

6. 从返回的 `page_probe_receipt` 读取 `receipt_id` 与 `receipt_fingerprint`。receipt 是后续页面工具权限的唯一持久化依据；prompt 文本、截图说明或风险确认都不能形成页面工具授权。

没有已连接工具、记录器上下文缺失、探针失败、只能静态读取、无法观察变化、证据不合格或 `page-probe-commit` 失败时，立即停止且**不得 claim Discovery**。不得用主会话推断、静态截图或风险确认替代实探。

## 3. Claim、派发与提交

每个任务必须在派发前原子 claim。Discovery 必须绑定上一步 receipt：

`scripts/run-test-design.ps1 agent-claim --run-dir "$1" --task-id <task_id> --execution-id <execution_id> --coordinator-id <coordinator_id> --executor-id <executor_id> --executor-kind codebuddy-subagent --wave-id <wave_id> --page-probe-receipt-id <receipt_id> --page-probe-receipt-fingerprint <receipt_fingerprint> --json`

非 Discovery 使用同一命令但不传两个 `--page-probe-receipt-*` 参数。只有 claim 成功后才派发。

用官方 `Agent` 工具派发，`subagent_type` 必须与角色映射一致。prompt 只携带任务包绝对路径、`execution_id`、`executor_id` 和完成后仅返回严格 `AgentResult` JSON 的要求；不得把页面 MCP allowlist 或页面授权编码进 prompt。页面工具 allowlist 只能由当前 claim 绑定的持久化 receipt 派生，receipt 缺失、被 tombstone、指纹不一致或工具不可用时失败关闭。

Discovery、Plan/DFX、条件 Risk 和 Reviewer 一律以前台 `Agent(..., run_in_background: false)` 串行执行。前台返回若包含物理 `agentId`，必须将其与逻辑 `executor_id` 建立一次性映射；若平台只返回其他可验证执行句柄，则记录该句柄，不得臆造 `agentId`。不得让另一物理 Agent、恢复后的不同上下文或重派任务复用该 execution。

coordinator 创建 `$1/orchestration/submissions/`（若不存在），校验 AgentResult 的任务 ID、角色、source fingerprint、状态和物理执行归属。`AgentResult` 契约**没有也不得新增 `execution_id` 字段**；execution 绑定来自 claim、实际派发句柄与当前响应的 coordinator 映射。验证后将响应原样保存到 `$1/orchestration/submissions/<task_id>--<execution_id>.json`，再用**同一个** execution ID 运行：

`scripts/run-test-design.ps1 agent-submit --run-dir "$1" --task-id <task_id> --execution-id <execution_id> --result <AgentResult绝对路径> --json`

claim 后中断、超时或结果丢失时，任务保持 `CLAIMED`；不得自动重派、换 executor 或猜测租约过期。优先恢复原 sub-agent 并核对输出。只有操作员确认该执行**没有任何外部或不可回滚副作用**时，才可显式运行：

`scripts/run-test-design.ps1 agent-release --run-dir "$1" --task-id <task_id> --execution-id <execution_id> --coordinator-id <coordinator_id> --reason "<具体原因>" --confirm-no-side-effects --json`

### 3.1 原生 Agent 不可用时的自动降级

如果官方 `Agent` 工具不存在、SSL/TLS/连接失败，或派发在 Agent 开始执行前失败，不向用户提问，立即记录：

`scripts/run-test-design.ps1 agent-dispatch-failed --run-dir "$1" --task-id <task_id> --execution-id <execution_id> --coordinator-id <coordinator_id> --reason "<原生Agent实际失败原因>" --fallback-executor-id <按角色唯一fallback_executor_id> --json`

- 返回 `RETRY_NATIVE_AGENT`：用完全相同的 claim/execution 再派发一次，不重新 claim、不释放 Discovery receipt。
- 再次失败后重复上述命令；返回 `RUN_ISOLATED_FALLBACK` 时，编排器已确认没有物理 Agent binding、输出、结果或提升，并把同一 execution 原子转换为 fallback。
- fallback 直接读取同一 `agent-task.json` 与 `task-context.json`，只用 Read/Write 类文件工具完成当前角色，禁止 shell 生成产物；主会话守卫只放行该 task 的 exact output allowlist。完成后仍生成严格 AgentResult，并用原 execution 调用 `agent-submit`。
- fallback 的角色顺序、Case 冻结 wave、返工、失败、外部阻塞和提交屏障与 sub-agent 完全相同；不得为了继续运行降低用例预算、跳过页面实探、合并 DFX 场景或跳过 Reviewer。
- 如果失败发生在 Agent 已产生 output、已绑定物理 transcript 或可能产生产品侧副作用之后，自动降级会失败关闭，此时只能恢复原执行或人工对账，禁止覆盖。

## 4. Case Worker 冻结波次

- 把同一次 `agent-run` 返回的全部 `case_worker` 冻结为一个 wave，按 `task_id` 去重并固定顺序；同一任务只派发一次。
- 整个 wave 使用同一 `wave_id`。先为每项生成各自的 `execution_id` / `executor_id` 并逐个 `agent-claim`，**全波 claim 成功后**才启动任何 Worker。
- 支持后台 sub-agent 时，以 `Agent(subagent_type: "test-case-worker", run_in_background: true)` 分别派发。后台调用立即返回的物理句柄是 `task_id`；必须保存每项 `task_id`，用 `TaskOutput` 逐项轮询至终态。只有结果实际返回 `agentId` 时才记录它，不得假设后台派发立即返回 `agentId`。
- 等待期间不得再次派发、根据中间状态补派或把同一任务交给第二个 Worker。整个 wave 结束后再校验全部结果和实际物理句柄映射。
- 全部 `SUCCEEDED` 时，按冻结顺序保存并以每项原 `execution_id` 逐一 `agent-submit`；忽略中间提交返回的新 runnable task，直至全波提交完毕，再运行一次 `agent-run`。
- 任一结果为 `NEEDS_REWORK`、`FAILED` 或 `EXTERNAL_BLOCKED` 时，选冻结顺序中第一个非成功控制结果但不立即提交。逐项确认其余 Case claim 没有产品侧、外部或不可回滚副作用，先用 `agent-release --confirm-no-side-effects` 释放并清空隔离候选 output；**全部释放成功后**再提交控制结果。任何 claim 无法确认时保留相关 claim 并停止等待人工对账。
- 不支持后台并行时，仍先完成全波 claim，再按冻结顺序逐个前台执行并暂存 AgentResult；原生 Agent 也不可用时，每个 claim 按 3.1 自动转换为 fallback 后顺序执行。**不得逐个执行后立即提交**；收齐整个 wave 后复用上面同一统一决策。串行/fallback 只影响吞吐，不改变门禁、owner、产物、预算和 Review 语义。

## 5. Review、交付与重入

Reviewer 优先使用独立 `test-reviewer` Agent；原生 Agent 不可用时使用独立 reviewer task、独立 fallback executor ID 和只读冻结输入执行。无论哪种身份，确定性 Review Gate 都会从实际文件重新计算 Cases、逐元素 DFX 12×4、manifest、traceability、敏感信息和人类可执行性，不能凭 Reviewer 自报通过。Review 未通过时按结构化结果回到责任阶段，禁止交付。

每次提交后检查失败、返工和用户确认状态。仅把页面实探后仍无法理解的外部语义交给用户；页面可验证项必须退回 Discovery 自行操作。

状态为 `DELIVERY_RUNNING` 时，认知 Agent 工作结束，**不要创建 Delivery Agent**。从已验证批次上下文读取真实 `module-path` 与 `batch-id`，仅在两者均确定且非臆造时运行：

`scripts/run-test-design.ps1 complete-deliverables --run-dir "$1" --module-path "<真实模块路径>" --batch-id <真实批次ID>`

参数无法可靠取得时说明缺失项并停止，不得猜测或跳过 `complete-deliverables`。

协调器必须维护 `coordinator_id`、已派发 `task_id`、每项 `execution_id` / `executor_id`、当前冻结 `wave_id`，以及平台实际返回的前台 `agentId`（若有）或后台 `task_id`。平台句柄不得替换 claim 身份。命令重入时先读取 `agent-status`、`claimed_tasks`、receipt 和提交记录；遇到 claim 时只恢复/对账原执行。禁止自动 lease expiry 或未经确认的 `agent-release`。
