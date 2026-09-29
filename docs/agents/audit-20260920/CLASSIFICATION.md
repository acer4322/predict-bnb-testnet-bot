# Astra instruction audit — 2026-09-20

本次只重構指令閱讀路線。45 份儲存庫指令／參考來源及 2 份相關外部技能都有分類；所有原件指紋見 BEFORE.json。分類單位是指令或同一目的的條款群，不把每個換行誤算成一條指令。舊研究包整份歸為 historical/reference：其中的命令式文字不因此取得目前的執行權限。

五類：**invariant** 每次任務的重要邊界；**conditional** 工作流程觸發後才讀；**historical/reference** 保留查證；**obsolete/redundant** 從有效閱讀路線移除；**conflicting** 依目前使用者授權、主機規則與適用實驗契約明確調和。

## Scope and preservation

- 實際自動載入的儲存庫檔案只有根 AGENTS 與既有協作子目錄 AGENTS。沒有找到專案自有 SKILL.md、AGENTS.override.md、Claude/Cursor/Copilot 規則；排除 .git、node_modules、.venv*、.tmp 和備份。
- 根 AGENTS 原件完整保存為 AGENTS.original.txt；其他舊模板、角色提示、交接、凍結來源及兩份外部技能維持原位原字節，避免破壞來源 hash 或其他工具相容性。從預設讀取路線移除即不再是 always-loaded 指令，不需要搬動研究證據。
- 不修改全域 Codex 設定、已安裝技能、記憶檔案、應用程式、策略、服務、派工器或研究結果。未派工、未重啟、未部署。
- 配置中的 gpt-6-astra 不單獨證明桌面會話模型；此次沒有改模型、推理強度或技能註冊。載入驗證使用已安裝 Codex 的實際 prompt builder，詳見 VERIFICATION.md。

## Conflict resolutions

| Conflict | Resolution |
| --- | --- |
| 自動記憶／人格維護 vs 主機限定明示記憶寫入 | 移除自動維護路線；研究延續寫入具名 CURRENT／成果，不寫私人記憶。 |
| 任何網路動作／不確定都重問 vs 既有使用者授權 | 保留動作範圍與生產邊界；不把 SSH、逾時查詢或已授權維護變成重複批准。 |
| generic 12 threads / four lanes vs 当前研究單工作 | V49 一個重型工作、4 threads；其他具名多工作計畫才用既有 wave 條款，未改工具預設。 |
| 舊 <=180 秒、Pair/TTL、固定尺寸、cap 與目前學習方法 | 不移植舊策略硬閘門；當前凍結 protocol 持有參數，真實合法性／收據／pending 保留。 |
| 舊 30 分鐘通知／收尾 vs 2026-09-05 override | 沒有專案層 30 分鐘限制；使用進度及真實故障條件。 |
| @5m bot／低流動性測試網 vs 本機研究範圍 | 不載入插件、恢復測試網；新使用者指示可改範圍。 |
| August/September 10 文件自稱 current vs R68 | 新小型 pointer 指向已保存 R68，明說不是即時 worker 狀態；舊歷史不重寫。 |
| 現實 HFT 才有正式證據 vs 快速微縮世界 | 微縮世界是合法研究篩選；不把它宣稱為正式執行／實單驗收。 |
| live 不可動 vs 已授權維護需重啟 | 研究不具重啟權；明確涵蓋服務的維護採真實前後狀態比對。 |
| 檔案裡的角色 prompt vs 執行授權 | 只作來源；既有協作所有權保留，讀 prompt 不產生角色、工作或委派。 |

## Progressive disclosure and savings

根只保留不可猜測的金融／資料／工作區邊界與 5 條路線；research、worker、runtime、development 是按需普通文件，不是假装已安裝的新技能。沒有增加任何巢狀 AGENTS。模板與已載入的技能內容不重複搬進 root。

通用人格、語氣、表情、群聊、日曆、心跳、重複 checklist 和工具使用常識不需要在本專案反覆提示 Astra。保留的限制是專案特有的真實約束；沒有因模型能力更強就刪掉交易安全或科學證據標準。此做法參照 [Astra 官方指引](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)。

固定節省只計實測根指令文字。條件文件只在相關工作讀取；它們的新增成本及整個原有提示的差異另列 VERIFICATION.md。SOUL 等模板原本未被此本機 prompt builder 自動注入，因此它們不是額外固定 token 節省。全球工具、技能目錄與本串已存在訊息不受本次檔案修改影響。

## Directive classification

分類筆數（不是 token）：invariant 5, conditional 72, historical/reference 73, obsolete/redundant 23, conflicting 25. 行號是修改前原件；JSON 含完整結構與各來源其餘文字的預設分類。

| Source / lines | Class | Existing instruction | Disposition |
| --- | --- | --- | --- |
| AGENTS.md:3-3 | obsolete/redundant | Workspace personification | Remove; no project constraint. |
| AGENTS.md:7-7 | obsolete/redundant | First-run identity/bootstrap and deletion | Remove OpenClaw onboarding; no BOOTSTRAP file found; no deletion performed. |
| AGENTS.md:11-17 | invariant | Reuse supplied startup context; reread only for missing detail or user request | Keep one root reuse/routing sentence; remove the generic memory/persona file roster. |
| AGENTS.md:21-24 | historical/reference | Daily/long-term memory layout | Leave existing memory files untouched; not a Codex startup route. |
| AGENTS.md:26-26 | conflicting | Automatically capture decisions and potentially secrets | Use task research artifacts for continuity; account/private data remains protected. No implicit memory write authority. |
| AGENTS.md:30-30 | conditional | Main-session-only private memory reading | Host memory policy governs; no new repository memory-loading path. |
| AGENTS.md:31-33 | conflicting | Freely update/consolidate personal memory | Remove; current host restricts memory writes to explicit user requests. |
| AGENTS.md:37-37 | conflicting | Read and concretely update memory files | Remove automatic memory-maintenance workflow; current host governs authorized writes. |
| AGENTS.md:39-39 | conditional | Remember-this request triggers a memory update | Handled by the current host memory workflow; do not duplicate a different path. |
| AGENTS.md:40-41 | conflicting | Autonomously rewrite AGENTS/TOOLS after lessons or mistakes | Remove unconditional self-modification; record research results in scoped artifacts. |
| AGENTS.md:45-45 | invariant | Do not exfiltrate private data | Retain project-specific account/credential/tape confidentiality. |
| AGENTS.md:46-46 | conflicting | Ask before every destructive command | Preserve dirty/frozen data; authorization must cover destructive scope, not a mandatory repeated question when already authorized. |
| AGENTS.md:47-47 | conditional | Inspect and preserve configuration/schedulers before changing | Root authorization boundary plus runtime.md. |
| AGENTS.md:48-48 | obsolete/redundant | Prefer trash over rm | Remove POSIX command prescription in Windows; host filesystem safety and scoped preservation remain. |
| AGENTS.md:49-49 | conflicting | Ask whenever uncertain | Remove blanket clarification trigger; resolve material scope/contract conflicts, otherwise use evidence and existing authorization. |
| AGENTS.md:53-53 | conditional | Preflight existing solutions, reject unsuitable options, no unapproved spend | development.md for new subsystems/dependencies; remove mandatory web/plugin search on every routine edit. |
| AGENTS.md:57-57 | obsolete/redundant | Generic read/search/workspace/calendar permissions | Remove generic capability checklist; does not authorize unrelated calendar access. |
| AGENTS.md:59-59 | conflicting | Ask for every external action/anything leaving machine | Remove blanket network reapproval; preserve authorized scope and private data. No authority to message/publish to others. |
| AGENTS.md:63-63 | obsolete/redundant | Do not act as user proxy in groups | Remove chat-platform scaffolding; host policy retains message authorization/privacy. |
| AGENTS.md:67-73 | obsolete/redundant | When to speak or stay silent; avoid triple responses | Remove group-chat response heuristics. |
| AGENTS.md:77-77 | obsolete/redundant | Use emoji reactions at most once | Remove social-platform behavior scaffolding. |
| AGENTS.md:81-81 | conditional | Load relevant skills and keep machine notes in TOOLS | Host skill rules already apply; use relevant workflow routes, not blank/example TOOLS as machine authority. |
| AGENTS.md:83-83 | obsolete/redundant | Prefer sag voice storytelling | Remove unrelated TTS recipe. |
| AGENTS.md:85-89 | obsolete/redundant | Discord and WhatsApp formatting rules | Remove platform-specific formatting from trading repository. |
| AGENTS.md:93-95 | conflicting | Edit HEARTBEAT and choose scheduling from OpenClaw conventions | Remove; a file is not Codex automation authority and this task changes no scheduler. |
| AGENTS.md:97-109 | obsolete/redundant | Unrequested email/calendar/weather checks and state JSON | Remove proactive personal monitoring template. |
| AGENTS.md:111-113 | obsolete/redundant | Proactive contact/quiet-hour heuristics | Remove; actual requested automations retain their own settings. |
| AGENTS.md:115-115 | conflicting | Unconditional proactive memory edits, commit and push | Remove blanket authorization; task scope governs edits and external publication. |
| AGENTS.md:119-121 | conflicting | Scheduled memory consolidation and check-ins | Remove unrelated automatic personal-memory workflow. |
| AGENTS.md:125-125 | obsolete/redundant | Evolve personality/conventions freely | Remove generic self-evolution instruction. |
| AGENTS.md:129-131 | historical/reference | OpenClaw website-relative reference links | Preserve original in audit archive; do not route from root. |
| SOUL.md:3-17 | obsolete/redundant | Persona, helpfulness, opinions, resourcefulness, respect | Leave verbatim but unroute; Astra/host already provides behavior. |
| SOUL.md:21-21 | obsolete/redundant | Generic privacy reminder | Covered by host and root project-specific privacy. |
| SOUL.md:22-24 | conflicting | Ask on any uncertain external act; group-chat/message quality | Unroute; host authorization and messaging policy applies. |
| SOUL.md:28-28 | obsolete/redundant | Generic tone prescription | Unroute; user/host style applies. |
| SOUL.md:32-38 | conflicting | Read/update memory and self-evolve every session | Unroute; no automatic memory/self-edit authority. |
| SOUL.md:42-42 | historical/reference | OpenClaw personality link | Unrouted reference. |
| USER.md:3-3 | conflicting | Continuously update user profile | Unroute; host memory policy governs writes. |
| USER.md:5-13 | historical/reference | Blank profile fields and prompts to build context | No actual user facts; retain untouched as a template. |
| USER.md:17-21 | obsolete/redundant | Respect user and generic external workspace link | Not a current project workflow. |
| IDENTITY.md:3-18 | obsolete/redundant | Invent identity/avatar at first conversation | Unroute unused onboarding. |
| IDENTITY.md:22-25 | historical/reference | Identity file schema, avatar format and OpenClaw sync precedence | Other-harness reference; no Codex identity/config edits. |
| IDENTITY.md:29-29 | historical/reference | OpenClaw workspace link | Unrouted reference. |
| TOOLS.md:3-3 | historical/reference | Local note categories | Unrouted template; actual worker info is in the runbook. |
| TOOLS.md:7-21 | historical/reference | Example cameras, SSH address and TTS voice | Examples are not real project configuration; retained verbatim. |
| TOOLS.md:25-29 | obsolete/redundant | Generic shared-skill/local-note guidance and add-whatever prompt | Remove from active reading path. |
| TOOLS.md:33-33 | historical/reference | OpenClaw workspace link | Unrouted reference. |
| HEARTBEAT.md:1-5 | historical/reference | Comments-only heartbeat template and scheduling hints | Unrouted; file and schedulers remain unchanged. Not proof of Codex automation state. |
| data/research/multi_chat_coordination_v1/AGENTS.md:3-3 | conditional | Read local CURRENT and WORKBOARD for coordination | Retain unchanged, only in that workflow. |
| data/research/multi_chat_coordination_v1/AGENTS.md:4-5 | conditional | Only 00 writes shared state; lanes have isolated ownership | Retain as genuine nested directory rules. |
| data/research/multi_chat_coordination_v1/AGENTS.md:6-7 | conditional | Bundled prompts are proposals; onboarding cannot dispatch/train/change production | Retain unchanged; root has explicit subtree route for root-started tasks. |
| data/research/multi_chat_coordination_v1/AGENTS.md:8-8 | conditional | Evidence for task/model settings; unknown null | Retain unchanged. |
| data/research/multi_chat_coordination_v1/AGENTS.md:9-9 | conditional | Separate component/native/economic evidence; preserve blocks | Retain nested reminder; root/research rules cover general evidence. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:6-6 | conditional | No project 30-minute limit; progress/failure based stopping | research.md and worker.md retain; older memory timer is non-operative. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:9-9 | conditional | Bounded queues, attach/dedup, artifact completion, timeout recovery | worker.md; queue-specific details loaded only for multi-job work. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:14-24 | conflicting | This dated file is current; default long read order and August references | Replace active route with RESEARCH_CURRENT; keep historical file unchanged. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:26-26 | conditional | Read producing artifact/tool for the selected subproblem | research.md. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:30-38 | conditional | Realistic HFT evidence; optimistic execution diagnostic only | research.md; does not ban user-authorized microworld screening. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:182-205 | historical/reference | Dated mainline, method bans, positive-OOS criterion and pivots | Preserve scope-specific history; not universal current training policy. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:209-211 | invariant | Strict-past inputs; offline Target/outcome labels | Root causal boundary; research route labels oracle diagnostics. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:212-218 | conditional | Execution-grade evidence, no research orders, frozen theory and sealed holdouts | Root live/frozen boundaries and research.md holdout rules; no frozen rule rewritten. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:219-219 | obsolete/redundant | Repeat duration override | One workflow rule retained. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:223-223 | invariant | Preserve dirty worktree and unrelated changes | Root invariant. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:225-225 | historical/reference | Preferred historical experiment output path | Use selected current run directory; do not repoint old artifacts. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:229-238 | conditional | Compact outcome/evidence/next-action report and duration reminder | research.md; exact metrics remain protocol-specific. |
| BTC5M_CODEX_RESEARCH_HANDOFF.md:240-1974 | historical/reference | Accumulated results, evolving policies and dated follow-up research | Search relevant sections only; preserve all original content/hashes. No blanket interpretation as current instructions. |
| SECOND_PC_DISPATCH_LOCK_20260830.md:3-8 | historical/reference | Past explicit dispatch authorization and its date | Keep as authorization history; existing user authorization persists unless revoked. |
| SECOND_PC_DISPATCH_LOCK_20260830.md:9-9 | conditional | Main host lightweight; heavy work second PC | worker.md. |
| SECOND_PC_DISPATCH_LOCK_20260830.md:10-10 | conditional | Revocation stops dispatch | User direction governs, preserved. |
| SECOND_PC_DISPATCH_LOCK_20260830.md:11-11 | conditional | Strict-past, realistic evidence, deterministic protection and live boundary | Retain appropriate current/frozen run scope; do not transplant historical strategy semantics. |
| BTC5M_LAN_WORKER_V1.md:9-24 | historical/reference | Documented worker identity/address/hardware/runtime | worker.md retains identity baseline; fresh probe required, no permanent IP assumption. |
| BTC5M_LAN_WORKER_V1.md:32-46 | historical/reference | Dispatcher/agent implementation and storage layout | Load relevant runbook section only for commands/paths. |
| BTC5M_LAN_WORKER_V1.md:54-68 | historical/reference | Generic resource defaults and runtime environment behavior | Keep tool defaults unchanged; not evidence of live capacity. |
| BTC5M_LAN_WORKER_V1.md:72-72 | conditional | Stage/monitor; startup gates are not RAM quota | worker.md capacity/stage/progress workflow. |
| BTC5M_LAN_WORKER_V1.md:78-87 | conditional | Bounded wake-up probe; strict host key; submit timeout is status-first | worker.md; no repeated blind submit. |
| BTC5M_LAN_WORKER_V1.md:88-89 | conflicting | Old thread/parallel defaults versus current named-job envelope | Current V49: one heavy job, four threads. Other queue profiles remain explicit and resource-scoped. |
| BTC5M_LAN_WORKER_V1.md:91-158 | conditional | Probe/smoke/submit/status/tail/cancel CLI examples | Reference syntax only; example IDs do not authorize jobs. |
| BTC5M_LAN_WORKER_V1.md:166-166 | conditional | Worker research-only; live authority stays on main host; bounded copies | worker.md. |
| BTC5M_LAN_WORKER_V1.md:174-202 | conditional | Local SSD staging and result collection | worker.md; runbook for syntax. |
| BTC5M_LAN_WORKER_V1.md:210-234 | historical/reference | GPU command example, historical driver/framework state | Verify only for an actual GPU workflow; no installation/change in this audit. |
| BTC5M_LAN_WORKER_V1.md:240-242 | historical/reference | Sleep guard and heartbeat implementation behavior | Keep reference; distinguish runner liveness from progress in worker.md. |
| BTC5M_LAN_WORKER_V1.md:244-244 | conditional | Exact-job timeout checks; collect before successors; UTF-8 logs | worker.md; do not duplicate collectors. |
| BTC5M_LAN_WORKER_V1.md:246-246 | historical/reference | Historical patch/rollback evidence | Preserve untouched. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:3-7 | historical/reference | Effective date, original broad scope and motivation | Keep historical source; active router reconciles later task-specific constraints. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:10-13 | conditional | Heavy second PC; pre-defined independent queues use wave | worker.md, queue runbook only when multiple jobs actually exist. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:14-14 | conflicting | Default max_parallel=4 | Current V49 envelope is one heavy job/four threads; fewer lanes already permitted by this clause. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:15-16 | conditional | Collect/refill finished lanes; harvest existing jobs | worker.md for real authorized queues. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:17-19 | conditional | Attach existing job; validate artifact; timeout/status first | worker.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:20-21 | conditional | Pinned worker interpreter and compact UTF-8 output | worker.md and unchanged dispatcher runbook. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:24-26 | conditional | Continue already-defined work in-turn; no background promises | worker.md; not authorization for unspecified research or scheduling. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:29-33 | conditional | Artifact-first recovery before replacement | research.md and worker.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:36-41 | conditional | Strict-past, realistic claims, sealed cohorts and live separation | Root and research.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:42-42 | conflicting | Reinstate <=180 seconds new-exposure prohibition | Not applicable to current user-authorized learning line: user explicitly rejected this strategy gate. Preserve frozen old experiment semantics only. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:43-43 | conditional | No duplicate economic ownership across processes | Root and worker.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:46-50 | historical/reference | Dispatcher command capabilities | Reference, inspect actual tool before use. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:52-53 | historical/reference | Historical speedup observation | Not a current throughput guarantee. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:55-56 | conflicting | All future conversations must load the whole old contract | Replace mandatory all-file reading with worker route; queue-specific contract still available. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:59-69 | conditional | 60-second progress audit, timeout is checkpoint, no between-turn monitoring promise | worker.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:71-72 | conditional | Check existing jobs before idle/stall claim | worker.md. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:74-79 | conditional | Wave progress artifact/postprocess and interruption attachment | Keep queue-only reference; no extra collector setup in docs audit. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:81-81 | historical/reference | Historical wave smoke evidence | No transfer to current job success. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:84-90 | conditional | Prepared/running/terminal state distinctions; authorized prepared jobs dispatch, existing jobs attach | worker.md plus honest state reporting; no job requested by merely reading a proposal. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:92-98 | conditional | Terminal recovery/reporting before unrelated analysis | worker.md; avoid indefinite uncollected success. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:100-108 | conditional | Existing auto-collector ownership, idempotent collection and reconcile | Use existing tool behavior; do not add another watcher from a prose command. |
| data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md:110-110 | historical/reference | Historical auto-collector self-test | Reference only. |
| BTC5M_LAB_GPT6_MONTH_MASTER_HANDOFF_LAUNCHER_20260910.md:6-10 | conditional | Read small entry first; history only as needed | research.md progressive disclosure; dated entry no longer default. |
| BTC5M_LAB_GPT6_MONTH_MASTER_HANDOFF_LAUNCHER_20260910.md:12-22 | historical/reference | Monthly history/registry/manifest pointers | Keep narrow-search references. |
| BTC5M_LAB_GPT6_MONTH_MASTER_HANDOFF_LAUNCHER_20260910.md:24-45 | historical/reference | Dated diagnosis and prohibition on generic classifier training | Scope-specific historical research, not current R68 policy. |
| BTC5M_LAB_GPT6_MONTH_MASTER_HANDOFF_LAUNCHER_20260910.md:47-56 | conditional | Bounded compute, protected live/fresh cohorts, bounded data reads | Current root/research/worker routes retain needed controls; old Extreme setting is not changed. |
| BTC5M_LAB_GPT6_MONTH_MASTER_HANDOFF_LAUNCHER_20260910.md:58-59 | conflicting | 5m bot launch instruction and dated current task | Current research is local; no plugin dependency. Keep archived text unchanged. |
| README_協作方案.md:1-124 | historical/reference | September 11 proposed roles, setup, scope, duplicated boundaries and example workflows | Reference only; existing coordination AGENTS/CURRENT/WORKBOARD govern actual selected coordination. No roles/jobs spawned by this refactor. |
| 核對來源與版本落差.md:1-27 | historical/reference | Dated evidence/version reconciliation; proposed settings and boundaries | Preserve historical claims; research.md retains evidence separation. It is not a runtime probe. |
| BTC5M_PROJECT_HANDOFF.md:1-1188 | historical/reference | Long chronological handoff with dated results and superseded instructions | Leave exact file unchanged; only retrieve a relevant historical section, not a startup read. |
| PROMPT_00_總控_整合.md:1-1 | conflicting | 5m bot plugin launch | Historical proposal; current user explicitly uses local tools. |
| PROMPT_00_總控_整合.md:2-2 | conditional | Continue existing work; do not imply roles/jobs are running | research.md and actual coordination state. |
| PROMPT_00_總控_整合.md:4-4 | conditional | Funding/venue/live/worker/smoke constraints for the named old role | Scope-specific reference; current protocol owns sizing/funding, root/worker preserve physical/legal/live boundaries. |
| PROMPT_00_總控_整合.md:6-9 | historical/reference | Dated mandatory handoff read roster | Do not load by default; actual selected line CURRENT controls. |
| PROMPT_00_總控_整合.md:11-11 | historical/reference | V3 status, actor architecture and consumed markets | Frozen historical evidence, not current model architecture. |
| PROMPT_00_總控_整合.md:13-13 | conditional | Bounded data reads, exact job recovery, no safety bypass | research.md and worker.md. |
| PROMPT_00_總控_整合.md:15-15 | conditional | Lane ownership and provenance-rich handoff | Retained in coordination scope; no general requirement to create roles. |
| PROMPT_00_總控_整合.md:17-25 | historical/reference | Role-specific A/B/C/D/00 tasks, staged deliverables and proposed gates | Historical assignment until explicitly selected; all clauses in this range remain reference, not independent action authority. |
| PROMPT_A_目標機理_經濟教學.md:1-1 | conflicting | 5m bot plugin launch | Historical proposal; current user explicitly uses local tools. |
| PROMPT_A_目標機理_經濟教學.md:2-2 | conditional | Continue existing work; do not imply roles/jobs are running | research.md and actual coordination state. |
| PROMPT_A_目標機理_經濟教學.md:4-4 | conditional | Funding/venue/live/worker/smoke constraints for the named old role | Scope-specific reference; current protocol owns sizing/funding, root/worker preserve physical/legal/live boundaries. |
| PROMPT_A_目標機理_經濟教學.md:6-9 | historical/reference | Dated mandatory handoff read roster | Do not load by default; actual selected line CURRENT controls. |
| PROMPT_A_目標機理_經濟教學.md:11-11 | historical/reference | V3 status, actor architecture and consumed markets | Frozen historical evidence, not current model architecture. |
| PROMPT_A_目標機理_經濟教學.md:13-13 | conditional | Bounded data reads, exact job recovery, no safety bypass | research.md and worker.md. |
| PROMPT_A_目標機理_經濟教學.md:15-15 | conditional | Lane ownership and provenance-rich handoff | Retained in coordination scope; no general requirement to create roles. |
| PROMPT_A_目標機理_經濟教學.md:17-27 | historical/reference | Role-specific A/B/C/D/00 tasks, staged deliverables and proposed gates | Historical assignment until explicitly selected; all clauses in this range remain reference, not independent action authority. |
| PROMPT_B_原生執行_主被動協作.md:1-1 | conflicting | 5m bot plugin launch | Historical proposal; current user explicitly uses local tools. |
| PROMPT_B_原生執行_主被動協作.md:2-2 | conditional | Continue existing work; do not imply roles/jobs are running | research.md and actual coordination state. |
| PROMPT_B_原生執行_主被動協作.md:4-4 | conditional | Funding/venue/live/worker/smoke constraints for the named old role | Scope-specific reference; current protocol owns sizing/funding, root/worker preserve physical/legal/live boundaries. |
| PROMPT_B_原生執行_主被動協作.md:6-9 | historical/reference | Dated mandatory handoff read roster | Do not load by default; actual selected line CURRENT controls. |
| PROMPT_B_原生執行_主被動協作.md:11-11 | historical/reference | V3 status, actor architecture and consumed markets | Frozen historical evidence, not current model architecture. |
| PROMPT_B_原生執行_主被動協作.md:13-13 | conditional | Bounded data reads, exact job recovery, no safety bypass | research.md and worker.md. |
| PROMPT_B_原生執行_主被動協作.md:15-15 | conditional | Lane ownership and provenance-rich handoff | Retained in coordination scope; no general requirement to create roles. |
| PROMPT_B_原生執行_主被動協作.md:17-25 | historical/reference | Role-specific A/B/C/D/00 tasks, staged deliverables and proposed gates | Historical assignment until explicitly selected; all clauses in this range remain reference, not independent action authority. |
| PROMPT_C_完整學生_狀態記憶.md:1-1 | conflicting | 5m bot plugin launch | Historical proposal; current user explicitly uses local tools. |
| PROMPT_C_完整學生_狀態記憶.md:2-2 | conditional | Continue existing work; do not imply roles/jobs are running | research.md and actual coordination state. |
| PROMPT_C_完整學生_狀態記憶.md:4-4 | conditional | Funding/venue/live/worker/smoke constraints for the named old role | Scope-specific reference; current protocol owns sizing/funding, root/worker preserve physical/legal/live boundaries. |
| PROMPT_C_完整學生_狀態記憶.md:6-9 | historical/reference | Dated mandatory handoff read roster | Do not load by default; actual selected line CURRENT controls. |
| PROMPT_C_完整學生_狀態記憶.md:11-11 | historical/reference | V3 status, actor architecture and consumed markets | Frozen historical evidence, not current model architecture. |
| PROMPT_C_完整學生_狀態記憶.md:13-13 | conditional | Bounded data reads, exact job recovery, no safety bypass | research.md and worker.md. |
| PROMPT_C_完整學生_狀態記憶.md:15-15 | conditional | Lane ownership and provenance-rich handoff | Retained in coordination scope; no general requirement to create roles. |
| PROMPT_C_完整學生_狀態記憶.md:17-27 | historical/reference | Role-specific A/B/C/D/00 tasks, staged deliverables and proposed gates | Historical assignment until explicitly selected; all clauses in this range remain reference, not independent action authority. |
| PROMPT_D_資料_獨立驗收.md:1-1 | conflicting | 5m bot plugin launch | Historical proposal; current user explicitly uses local tools. |
| PROMPT_D_資料_獨立驗收.md:2-2 | conditional | Continue existing work; do not imply roles/jobs are running | research.md and actual coordination state. |
| PROMPT_D_資料_獨立驗收.md:4-4 | conditional | Funding/venue/live/worker/smoke constraints for the named old role | Scope-specific reference; current protocol owns sizing/funding, root/worker preserve physical/legal/live boundaries. |
| PROMPT_D_資料_獨立驗收.md:6-9 | historical/reference | Dated mandatory handoff read roster | Do not load by default; actual selected line CURRENT controls. |
| PROMPT_D_資料_獨立驗收.md:11-11 | historical/reference | V3 status, actor architecture and consumed markets | Frozen historical evidence, not current model architecture. |
| PROMPT_D_資料_獨立驗收.md:13-13 | conditional | Bounded data reads, exact job recovery, no safety bypass | research.md and worker.md. |
| PROMPT_D_資料_獨立驗收.md:15-15 | conditional | Lane ownership and provenance-rich handoff | Retained in coordination scope; no general requirement to create roles. |
| PROMPT_D_資料_獨立驗收.md:17-27 | historical/reference | Role-specific A/B/C/D/00 tasks, staged deliverables and proposed gates | Historical assignment until explicitly selected; all clauses in this range remain reference, not independent action authority. |
| BTC5M_多對話協作啟動包_V2_含推理配置_20260911/PROMPT_D_資料_獨立驗收.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| BTC5M_多對話協作啟動包_V2_含推理配置_20260911/PROMPT_C_完整學生_狀態記憶.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| BTC5M_多對話協作啟動包_V2_含推理配置_20260911/PROMPT_B_原生執行_主被動協作.md:1-45 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| BTC5M_多對話協作啟動包_V2_含推理配置_20260911/PROMPT_A_目標機理_經濟教學.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| BTC5M_多對話協作啟動包_V2_含推理配置_20260911/PROMPT_00_總控_整合.md:1-45 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/PARALLEL_LANE_D_TARGET_TEACHER_PROMPT.md:1-21 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/PARALLEL_LANE_C_EXECUTION_CERTAINTY_PROMPT.md:1-20 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/PARALLEL_LANE_B_PROTECTION_PROMPT.md:1-20 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_favorable_structure_decomposition_v1_20260906/10_GPT6_ENTRY_PROMPT.md:1-7 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_favorable_structure_decomposition_v1_20260906/08_GPT6_ENTRY_PROMPT.md:1-36 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_target_direction_persistence_v1_20260907/10_GPT6_ENTRY_PROMPT.md:1-7 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_target_direction_persistence_v1_20260907/08_GPT6_ENTRY_PROMPT.md:1-46 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907/08_GPT6_ENTRY_PROMPT.md:1-212 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/BTC5M_V12_TO_R54_HANDOFF_20260919/source_snapshots/S0347_AGENTS.md:1-131 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_resource_service_round3_b_policy_challenge_v1_20260906/04_GPT6_ENTRY_PROMPT.md:1-27 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_ms4_marginal_expand_value_pack_v2_20260906/07_GPT6_ENTRY_PROMPT.md:1-82 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_target_our_system_synthesis_challenge_v1_20260906/10_GPT6_ENTRY_PROMPT.md:1-30 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906/evidence_round2/15_GPT6_ROUND2_ENTRY_PROMPT.md:1-14 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906/07_GPT6_ENTRY_PROMPT.md:1-75 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/r4_v0/p0_provenance_v1/GPT6_DETAIL_INTELLIGENCE_PACKAGE_V1_LAUNCH_PROMPT_20260905.md:1-110 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/multi_chat_coordination_v1/bundle/PROMPT_D_資料_獨立驗收.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/multi_chat_coordination_v1/bundle/PROMPT_C_完整學生_狀態記憶.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/multi_chat_coordination_v1/bundle/PROMPT_B_原生執行_主被動協作.md:1-45 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/multi_chat_coordination_v1/bundle/PROMPT_A_目標機理_經濟教學.md:1-47 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| data/research/multi_chat_coordination_v1/bundle/PROMPT_00_總控_整合.md:1-45 | historical/reference | Archived entry prompt, role copy, compatibility pointer or snapshotted AGENTS | Preserve exact bytes. All embedded read/role/compute/model directives apply only as historical reference unless the user explicitly selects that scoped workflow; no automatic startup route. |
| external:btc5m-worker-dispatch:1-7 | historical/reference | External skill frontmatter, old allowed-tools and trigger metadata | Outside repository and read-only; actual host tool/skill registration governs. New repo routes are not auto-registered skills. |
| external:preserve-live-runtime:1-7 | historical/reference | External skill frontmatter, old allowed-tools and trigger metadata | Outside repository and read-only; actual host tool/skill registration governs. New repo routes are not auto-registered skills. |
| external:btc5m-worker-dispatch:13-13 | conditional | Authorized named native/HFT only, no host heavy work or new-experiment authority | worker.md; existing authorization sufficient. |
| external:btc5m-worker-dispatch:17-17 | conflicting | Always read current, workboard and role bindings | worker.md narrows coordination documents to actual coordination work. |
| external:btc5m-worker-dispatch:18-19 | conditional | Named-job authorization, mainline/blocker and identity verification | worker.md. |
| external:btc5m-worker-dispatch:23-28 | conditional | Probe, existing status, stage/load-only, one job/four threads, timeout recovery, terminal audit | worker.md preserves the sequence and evidence. |
| external:btc5m-worker-dispatch:32-34 | conditional | Reuse handoff, compact output and stop for genuine preflight blockers | worker.md. |
| external:btc5m-worker-dispatch:38-39 | obsolete/redundant | Repeated timeout and ended runner-error advice | Consolidated once in worker.md. |
| external:btc5m-worker-dispatch:40-40 | historical/reference | C4 overlay example | Historical component-only evidence; not a promotion claim. |
| external:btc5m-worker-dispatch:41-41 | invariant | Same-plan cancellation is not terminal release | Root pending/ownership invariant; diagnostic details remain external reference. |
| external:btc5m-worker-dispatch:45-47 | obsolete/redundant | Repeated identity/submission/accounting checklist | Consolidated worker procedure. |
| external:btc5m-worker-dispatch:48-48 | conditional | Preserve native failure frame/receipt prefix/source index before repair | worker.md. External skill itself unchanged. |
| external:preserve-live-runtime:13-13 | conditional | Only authorized changes requiring restart; never enable unknown trading state | runtime.md. |
| external:preserve-live-runtime:17-19 | conditional | Verify scope and capture real pre-restart enabled/armed/strategy/stake state | runtime.md, no hardcoded root path requirement. |
| external:preserve-live-runtime:23-23 | conditional | Focused Python/dashboard validation | development.md; affected package only, dashboard-v2 separately verified. |
| external:preserve-live-runtime:24-27 | conditional | Existing launcher, readiness and exact state parity; restore only prior authorization, no fake order | runtime.md, launcher applies to its owning service only. |
| external:preserve-live-runtime:31-32 | conditional | Compact snapshots and narrow ledger endpoint | runtime.md. |
| external:preserve-live-runtime:33-33 | conditional | Stop after relevant checks; distinguish baseline failure | runtime.md and development.md. |
| external:preserve-live-runtime:37-40 | obsolete/redundant | Repeated pause/quote-access/stake/restart pitfalls | Consolidated into runtime procedure; no safety rule lost. |
| external:preserve-live-runtime:44-47 | obsolete/redundant | Repeated health/state/quote-access verification checklist | Consolidated into runtime procedure. External skill unchanged. |

## Coverage limits

這是代理指令及其閱讀路線審核，不是逐一重判所有市場研究結果。兩份長期研究交接、凍結 entry prompts 及其歷史指令整體歸類為 reference，只有有關目前流程的條款另列；不聲稱已重驗裡面每個實驗。外部技能在審核範圍但不在修改範圍；平台注入的所有其他工具／技能說明保持原狀。

可重做的原件、來源 hashes、逐項分類 JSON 與載入實測都保留在本目錄，正常任務不必讀這份完整審核。
