# CodeBuddy 多 Agent 适配与调度

本文说明怎样把仓库的确定性测试设计编排器接到 CodeBuddy。它只定义执行适配，不改变页面实探、DFX、用例质量、Review 或交付门禁。

## 结论

- 核心编排器不调用模型。`agent-run` 只推进状态机、生成隔离任务并返回 `runnable_tasks`；认知任务必须由 CodeBuddy 或其他模型执行器完成，再通过 `agent-submit` 提交。
- `.codebuddy/agents/` 注册 5 个认知角色：`test-discovery`、`test-plan-dfx`、`test-risk-arbiter`、`test-case-worker`、`test-reviewer`。
- 主 CodeBuddy 会话是唯一 coordinator。它读取 `runnable_tasks`、选择角色、调度前台或后台 sub-agent、收集结果并顺序调用 `agent-submit`。
- 不注册 Delivery Agent。交付始终由确定性单写者 `complete-deliverables` 完成。
- 项目命令 `/test-design-run <run-dir>` 是推荐入口；导入或打开仓库不会自动创建或启动任何任务。
- 默认 Discovery 只带 `Read`、`Write` 和动态 MCP 装载元工具；这些元工具不是页面能力。Windows 正式执行必须在 claim 前实际探测一个已连接、经审核且能点击并观察结果的页面控制 MCP，并逐个成功预探本批预计使用的全部 exact page tools，否则停在 Discovery 前。

这三层必须保持分离：

| 层 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| 确定性核心 | 状态机、任务包、指纹、输出接受范围、门禁、合并、返工、Review 有效性、交付事务 | 调用模型、决定模型输出内容、拦截模型通过其他通道发起的任意文件系统写入 |
| CodeBuddy 适配层 | 角色注册、任务路由、串并行调度、结果回传 | 声明门禁通过、直接写正式资产 |
| 认知角色 | 页面实探、计划/DFX、风险仲裁、功能点用例、独立审查 | 推进全局状态、修改其他任务或执行交付 |

## CodeBuddy 中会显示在哪里

CodeBuddy Code 的项目级自定义 sub-agent 文件位于 `.codebuddy/agents/`。启动新会话后使用 `/agents` 查看和管理；手工新增定义后，需要在下一次 CodeBuddy Code 会话加载。这里唯一承诺的项目 Agent 注册入口是 **CodeBuddy Code 的 `/agents`**。官方说明见 [CodeBuddy Code Sub-Agents](https://www.codebuddy.ai/docs/cli/sub-agents)。

“自定义 Agent 注册表”和“IDE Agent 页面”不是同一个概念：

- CodeBuddy Code 的 `/agents` 用于查看可调用的自定义 Agent。
- CodeBuddy IDE 的 Agent 页面主要展示任务/会话。仓库中的 5 个定义不会因为导入项目就自动变成 5 个已运行任务卡片；只有实际创建或运行任务后，任务列表才会变化。参见 [IDE Agent 模式快速开始](https://www.codebuddy.ai/docs/ide/User-guide/Agent-Mode/Quickstart)。
- 因此，IDE Agent 任务页没有显示 5 个项目角色是预期现象，不能据此判断 `.codebuddy/agents/` 加载失败。

如果当前页面看不到这些角色，按以下顺序排查：

1. 确认打开的项目根目录就是包含 `.codebuddy/agents/` 的仓库根目录。
2. 确认 5 个 Markdown 定义文件未被同步工具忽略。
3. 结束并重新启动当前 CodeBuddy Code 会话，然后执行 `/agents`。
4. 如果当前客户端没有兼容的 CodeBuddy Code `/agents`，将本次预检标为“未验证”，改用支持该入口的 CodeBuddy Code 版本/界面；不要从 IDE 任务列表推断注册状态。
5. 完成下述启动前预检后执行 `/test-design-run <run-dir>`；项目导入本身不会启动 coordinator 或 sub-agent。

## 用户启动前预检

`/agents` 和 `/hooks` 会打开交互式管理界面，不是 `/test-design-run` 可以嵌套执行的普通步骤。CodeBuddy 每条消息只执行一个 slash command，因此用户必须在同一项目的**新 CodeBuddy Code 会话**中分别完成：

1. 单独执行 `/agents`，确认 5 个项目 Agent 已加载且没有 Delivery Agent。
2. 单独执行 `/hooks`，审核并启用 `guard-agent-tool.py` 的 `PreToolUse` 和 `record-page-probe.py` 的 `PostToolUse`；不得跳过。
3. 另发一条消息执行 `/test-design-run <run-dir>`，并明确告知前两项已通过。

导入、升级或修改 `.codebuddy` 配置后必须新开会话，因为 CodeBuddy 在会话启动时获取配置快照。当前对话没有用户的明确预检确认时，协调命令失败关闭，不得先 claim 再补验。Slash command 语义见 [CodeBuddy Code Slash Commands](https://www.codebuddy.ai/docs/cli/slash-commands)，hook 审核与启动快照见 [CodeBuddy Code Hooks](https://www.codebuddy.ai/docs/cli/hooks)。

## Coordinator 的固定职责

主会话必须是唯一 coordinator，不能把二次调度交给 sub-agent。CodeBuddy 官方约束是 sub-agent 不能再创建 sub-agent；后台并发也必须由主会话发起。详见 [Sub-Agents 的限制与后台执行](https://www.codebuddy.ai/docs/cli/sub-agents)。

coordinator 每轮执行以下闭环：

1. 调用 `scripts/run-test-design.ps1 agent-run --run-dir <run-dir> --json`。
2. 读取返回的 `runnable_tasks`，按 `task_id` 去重，并检查每个任务包中的 role、source fingerprint、输入快照和输出白名单。
3. 遇到 Discovery 时，先生成稳定唯一的 `execution_id`，再在 claim 前对已连接的页面 MCP 做一次真实“前读→点击/选择/输入→变化后读”探针。若 MCP 把 snapshot、click、fill、select、navigate 拆成多个 exact tools，必须对本批预计使用的每个工具逐个完成低风险、可恢复且成功的探测，并把同一 server 的这些工具一并纳入 receipt；未探测工具保持拒绝。`PostToolUse` 记录器返回 `PAGE_PROBE_RECORD` 哈希上下文；协调器把同一 session/transcript 的有序记录和 `artifacts/page-probe-evidence/<execution_id>/` 下的证据交给 `page-probe-commit`。探针或 receipt 提交失败时不得 claim。
4. 先生成稳定唯一 `execution_id` / `executor_id`，再用 `agent-claim` 原子领取任务；Discovery claim 必须传入 receipt id 与 fingerprint。随后用官方 `Agent` 工具按 `subagent_type` 路由到匹配的项目 Agent，Agent 只能读冻结任务输入并写自己的 `output/`。
5. 收集严格 `AgentResult`。前台派发仅在实际返回时记录物理 `agentId`；后台派发立即返回的物理句柄是 `task_id`，由 `TaskOutput` 轮询到终态，只有结果实际提供 `agentId` 时才补记。结果正文不得混入任务 `output/`。`AgentResult` schema 没有 `execution_id` 字段，也禁止为了方便绑定而新增；执行归属由 claim、平台实际返回的物理执行句柄和当前响应的一次性映射证明。
6. coordinator 保存结果并用同一 `execution_id` 调用 `agent-submit`；只以编排器响应为准，不接受 Agent 自报“阶段通过”。
7. 状态进入 `DELIVERY_RUNNING` 后，执行 `complete-deliverables`；不得调用任何 Delivery Agent。

领取命令必须显式声明执行身份：

```powershell
scripts/run-test-design.ps1 agent-claim `
  --run-dir <run-dir> `
  --task-id <task-id> `
  --execution-id <调用方稳定唯一执行ID> `
  --coordinator-id <coordinator-id> `
  --executor-id <executor-id> `
  --executor-kind codebuddy-subagent `
  --wave-id <wave-id> `
  --json
```

Discovery 在上述命令中还必须追加 `--page-probe-receipt-id <receipt-id>` 与 `--page-probe-receipt-fingerprint <receipt-fingerprint>`；非 Discovery 禁止携带这两个参数。receipt 已预先绑定同一个 task、execution、coordinator 和 source fingerprint，不能跨执行复用。

核心契约声明了 `codebuddy-subagent`、`codebuddy-main-session`、`codebuddy-agent-team` 和 `external-session` 四种 `executor-kind`，但**声明枚举不等于执行隔离已经认证**。当前 CodeBuddy 严格适配只认证由本项目 hook 约束的 `codebuddy-subagent`；其余三种均不得用于正式生成、Review 或交付。调用方必须在首次领取前生成稳定唯一 `execution_id`；领取响应丢失时，用同一 coordinator、executor、wave、execution 和 task 参数重试会返回同一 claim。其他 coordinator、执行身份或 execution 不能抢占已领取任务。`agent-run` 不再返回 `CLAIMED` 任务，`agent-submit` 缺少或使用错误 `execution_id` 时拒绝。

领取不自动超时，也没有租约到期重派。若 coordinator 或 Agent 崩溃，优先恢复原执行器并继续使用原 `execution_id`；尤其 Discovery 可能已经创建或修改页面数据，不能因“长时间无响应”自动重做。只有确认没有业务、页面或数据副作用时，才可显式释放：

```powershell
scripts/run-test-design.ps1 agent-release `
  --run-dir <run-dir> `
  --task-id <task-id> `
  --execution-id <execution-id> `
  --coordinator-id <coordinator-id> `
  --reason "<可审计原因>" `
  --confirm-no-side-effects `
  --json
```

## 写入边界与失败关闭

“Agent 只能写自己的 `output/`”需要执行适配层和确定性核心共同完成：

- 项目 Agent 不自动加载 Skill。Plan/DFX、Risk、Case、Reviewer 只声明 `Read, Write`；Discovery 额外声明 `ToolSearch, DeferExecuteTool, WaitForMcpServers`，但只能调用当前 claim 的持久化 page-probe receipt 精确授权的页面 MCP。
- `.codebuddy/settings.json` 注册项目级 `PreToolUse` hook。对 `subagents/` 下的 transcript，文件名必须严格为 canonical `agent-*.jsonl`；目录内其他命名也按失败关闭，不能借名称绕过保护。guard 要求 transcript 唯一绑定 `docs/test-assets/batch-runs/<run>/` 下的 task，manifest 必须为 `CLAIMED`，task/context/全部冻结输入的实际指纹必须与 claim 一致，事件哈希链中必须恰好有一个完整的当前 claim 事件，并且 transcript 必须包含本次 `execution_id` / `executor_id`。
- `Read` 只允许任务 meta、冻结 inputs 和本任务 output；`Write` 只允许 output 白名单；Edit、搜索和 shell 工具一律拒绝。页面元工具和 MCP 只允许已 claim 的 Discovery：guard 从 claim 验证 receipt id、fingerprint、execution、coordinator、source fingerprint、单一 MCP server 和 `approved_page_mcp_tools`，并核对 receipt 的有序 `PAGE_PROBE_RECORD`、证据哈希、事件链与项目级一次性消费绑定。直接 MCP 调用必须位于该精确 allowlist；`DeferExecuteTool` 必须由顶层唯一 `tool_name` 精确选择 allowlist 内工具，嵌套、备注或冲突名称不能形成授权。prompt 文本不形成页面权限。畸形、缺失、tombstone、截断、替换或改写 transcript，伪 run-dir、未 claim、packet/context/input/claim event 篡改或解释器启动失败都拒绝。
- guard 对已验证 transcript 和事件链维护项目隔离的 append-only checkpoint：首次流式验证全部 JSONL，后续对已处理前缀重算完整 `prefix_sha256`、只 JSON 解析新增完整记录；中部改写后再追加、截断、替换或哈希不一致都会 poison 当前物理 transcript，同一 transcript 也不能切换 claim。claim 身份完整扫描结果随 claim digest 缓存，不会在每次工具调用重复 JSON 解析；task/context/冻结 inputs 的内容指纹只计算一次，后续逐文件核对不可变状态，任何变化均失败关闭。checkpoint 的读取、校验和原子更新使用每 transcript 排他锁，避免并发 Hook 丢失进度。该机制不设置 transcript 总大小硬上限，避免长时间全量 Discovery 因 16MB 累计内容中途失效；单条异常巨大的 JSONL 仍受防拒绝服务上限约束。缓存位于 CodeBuddy 本机运行缓存目录，不属于交付资产，认知 Agent 的白名单工具也无权修改它。
- guard 首次验证 claim 后，以 `execution_id` 为键用原子 no-replace marker 将 run/task/coordinator/executor、冻结指纹和 canonical `subagents/agent-*.jsonl` transcript 的路径、首段大小/哈希及可用文件身份绑定。相同 transcript 重入幂等，其他 transcript 抢用同一 claim 会拒绝；`agent-submit`、独立 Review 和 Delivery 会重验 marker 与首次 transcript 前缀，marker/transcript 删除、替换、截断或前缀篡改均失败关闭。release 不删除 marker，旧 `execution_id` 永不复用。
- 普通主会话 transcript 不受该 hook 约束，可在 claim 前做探针；但**任何** canonical sub-agent（包括与测试设计无关的 sub-agent）若触发上述 matcher 而没有有效任务 claim，也会被拒绝，避免“无法识别就放行”。需要运行其他可写 sub-agent 时，应使用分离会话/项目或另行认证并配置边界。
- 首次导入或升级后，用户必须在调用协调命令之前单独进入 `/hooks`，检查 guard 与 recorder 均已加载并明确允许其运行；`/test-design-run` 不能内嵌该交互面板。如果客户端不支持 hook、hook 未获信任或保护脚本不可用，命令必须失败关闭，不能以“稍后检查 Git”替代写入前保护。
- 确定性核心在 `agent-submit` 时只接受当前任务 workspace 中符合白名单、无路径穿越且无符号链接的产物，并校验指纹与哈希。它能拒绝接纳越界产物，但不能追溯证明模型没有通过未受保护的工具修改其他文件。
- 因此，文档中的“隔离 workspace”是强制执行约束，不是仅靠 prompt 的承诺；CodeBuddy 保护 hook 是该客户端适配的必选组成，核心校验是第二道门禁。

## Windows 页面控制能力

当前项目不把 `Browser` 或 `ComputerUse` 写入 Agent：它们不是当前 CodeBuddy Code 官方工具清单中可依赖的正式名称。部署方应先连接一个实际支持当前 Windows CodeBuddy 会话的页面控制 MCP，再在新会话中由用户单独执行 `/agents` 检查 `test-discovery`，并让主 coordinator 通过 `WaitForMcpServers`、`ToolSearch`、`DeferExecuteTool` 完成真实点击探针。官方工具边界见 [CodeBuddy Code Tools Reference](https://www.codebuddy.ai/docs/cli/tools-reference)。

Discovery 的动态 MCP 元工具只解决“等待、发现、执行 deferred MCP”，不证明目标工具能控制页面。coordinator 必须先盘点本批预计使用的全部 exact page tools；若 MCP 将 snapshot、click、fill、select、navigate 拆开，每个工具都要在同一 session、transcript 和 MCP server 下完成低风险、可恢复且成功的真实调用。再从 recorder 的 `PAGE_PROBE_RECORD` 中选取这些调用，并用 `page-probe-commit` 同时证明每个授权工具至少有一条成功记录，以及整组至少一次“前读 → mutation → 响应变化后的读”。正式证据必须位于 `artifacts/page-probe-evidence/<execution_id>/`，receipt 再通过 `--page-probe-receipt-id` / `--page-probe-receipt-fingerprint` 绑定 Discovery claim。未预探工具继续拒绝；没有有效 receipt、工具调用失败或证据不合格时不领取任务。若 MCP 仅暴露一个泛化工具，receipt 只能约束该 tool name，不能把一次 action 的预探表述成对所有 action 的独立证明；部署方必须把这种粒度列为 MCP 适配信任边界。不得以 prompt 文本或截图说明伪造授权。

## 串行阶段

Discovery、Plan/DFX、条件 Risk 和 Reviewer 都按状态机串行执行。每轮只处理当前唯一任务，提交完成后再调用一次 `agent-run` 取得下一任务。Discovery 永远不能拆成并行 sub-agent，否则页面状态、同一测试数据 ID 和 CRUD 生命周期会失去单一事实 owner。

如果当前 CodeBuddy 客户端支持 sub-agent 但不支持后台并行，仍先冻结并 claim 完整 Case wave，再按固定顺序逐个前台执行、校验物理句柄并暂存 AgentResult，期间不得 `agent-submit`。收齐整波结果后才统一决策：全成功按冻结顺序提交；存在控制结果则释放其余 claim 后提交冻结顺序中的第一个控制结果。波次中途不重新派发，波次收口后才重新取得任务。这样只降低速度，不改变 task owner、source fingerprint、累计门禁、合并顺序或最终结果要求。

## Case Worker 并行波次

只有 `runnable_tasks` 当前返回的 Case Worker 可以并行。coordinator 必须使用“冻结波次”，不能边提交边扩散新任务：

1. 把一次 `agent-run` 返回的 Case Worker 复制为当前 wave，按 `task_id` 去重并固定顺序，为本波生成稳定 `wave-id`。
2. 为每个 worker 生成独立、稳定的 `execution-id` 和 `executor-id`，再按冻结顺序逐个调用 `agent-claim`，共同使用本波 `wave-id`。任一领取失败时停止启动 Agent；尚未产生副作用的已领取任务可经显式确认后释放。
3. 全部领取成功后，通过 `Agent(subagent_type: "test-case-worker", run_in_background: true)` 为 wave 中每个任务启动后台 sub-agent，并保存立即返回的后台 `task_id`；不要在 wave 执行期间再次调用 `agent-run`。
4. 用 `TaskOutput` 按后台 `task_id` 等待整个 wave 全部结束，再检查每个 AgentResult；仅当结果实际返回 `agentId` 时才记录，不得假设后台调用立即返回 `agentId`。
5. 若全部结果为 `SUCCEEDED`，按冻结顺序用各自 `execution_id` 逐一调用 `agent-submit`。忽略中间 `agent-submit` 响应里出现的新 `runnable_tasks`，直到整个 wave 提交完毕。
6. 若任一结果为 `NEEDS_REWORK`、`FAILED` 或 `EXTERNAL_BLOCKED`，选定冻结顺序中的第一个控制结果，但不要立即提交。先逐个审计其余 `CLAIMED` Case 任务；确认 Case Worker 没有产品侧副作用后，对每个任务执行 `agent-release --confirm-no-side-effects`（核心同时清空其隔离 output），全部释放成功后再提交该控制结果。核心拒绝在同 wave 已提交成功 peer 后再接受控制结果，也拒绝未释放 peer 的控制提交；不得直接丢弃结果、等待超时或换 execution 重复提交。
7. 如果任一成功结果在 `agent-submit` 时被门禁拒绝或触发返工，立即停止当前 wave；先用 `agent-status` 审计尚未提交的 claim，确认没有产品侧副作用后逐个显式 release，再重新取任务。任何无法确认无副作用的 claim 都必须恢复原 execution 处理，不能直接重新派发。
8. wave 收口后再调用一次 `agent-run`，由编排器决定下一波或进入确定性合并。

冻结波次负责固定本轮 owner 和提交顺序，原子 claim 负责跨 coordinator、崩溃恢复和重复调用下的唯一执行权；两者共同避免任务重复派发，也避免中间提交返回的任务与旧波次混装。并行只改变执行时间，不改变编排器的接受顺序和合并结果。

## 平台能力降级

| CodeBuddy 能力 | 操作方式 | 质量影响 |
| --- | --- | --- |
| 支持 sub-agent、探针通过的页面 MCP 和后台执行 | 非 Case 串行；Case 使用冻结 wave 并行 | 当前认证的完整能力 |
| 支持 sub-agent 和页面 MCP，不支持后台并行 | 非 Case 逐任务执行；Case 全波 claim 后逐个前台执行、收齐结果再统一决策和顺序提交 | 质量门禁不降级；吞吐下降 |
| 支持 sub-agent，但没有探针通过的页面 MCP | 不 claim Discovery | 正式流程阻断 |
| 不支持 sub-agent | 主会话只能做非正式诊断 | 正式流程阻断 |

完全不支持 sub-agent 时，主会话不在本项目 hook 的隔离范围内；即使它按角色提示词串行生成，核心也无法证明其没有越界写入。因此 `codebuddy-main-session` 只允许非正式诊断，不能进入正式 Review/Delivery。当前仓库也没有为 `external-session` 或 Agent Team 提供等价的 transcript 识别、claim 绑定、读写拦截和物理执行身份认证适配，所以它们同样**未认证且默认阻断正式交付**。仅另开 Reviewer 会话不能修复此前未受保护的生成过程。

CodeBuddy 官方仍把 Agent Teams 标为实验性能力，并明确列出会话无法恢复、任务状态可能延迟等限制。Agent Teams 可用于独立研究或非正式诊断，但其成员默认工具/权限和共享协调语义不等于本适配的隔离 workspace。除非未来增加并通过独立的 Team adapter 认证测试，否则不能把 `codebuddy-agent-team` 枚举当作正式运行后门。详见 [Agent Teams 的已知限制](https://www.codebuddy.ai/docs/cli/agent-teams#known-limitations)。

## 验收标准

CodeBuddy 适配完成不等于创建了若干 Markdown 文件。至少应满足：

- `/agents` 能识别 5 个项目认知角色，且没有 Delivery Agent。
- 用户在新 CodeBuddy Code 会话中分别完成 `/agents`、`/hooks` 启动前预检；两者不能在 `/test-design-run` 内嵌套。
- 导入项目不会自动创建任务；CodeBuddy IDE 的 Agent 页面只是任务/会话列表，不是项目 Agent 注册表。
- `/test-design-run <run-dir>` 由主会话调度，不由 sub-agent 二次调度。
- Discovery claim 前已用同一 MCP server 完成有序页面探针，`page-probe-commit` 产出持久化 receipt 并绑定 claim；无能力或无 receipt 时失败关闭。
- 每个任务在执行前经 `agent-claim` 绑定唯一 `execution_id`；不存在自动超时重派，崩溃释放必须明确确认无副作用。
- AgentResult 不含 `execution_id`；coordinator 以 claim、平台实际返回的物理执行句柄和响应映射验证执行归属，不臆造 `agentId`。
- 非 Case 角色始终串行；Case 并行时使用冻结、去重、全波等待和顺序提交。
- 不支持并行时能按同一任务与门禁协议退化为串行；Case 仍须收齐完整 wave 后统一决策，不能逐个执行后立即提交，差异仅记录为吞吐下降。
- 不支持 sub-agent 时只能做非正式诊断；`codebuddy-main-session`、`codebuddy-agent-team`、`external-session` 都未通过本适配认证，不得正式交付。
- 项目写入保护 hook 未启用时失败关闭；hook 拒绝越界工具调用，确定性核心拒绝接纳越界产物、指纹不一致、输出缺失或 Review 未通过的提交。
