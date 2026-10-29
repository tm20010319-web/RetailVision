# Fact Ownership v2

`Fact` 是可审计业务或算法事实；`Projection` 可由权威事实重建；内存 observation
不是数据库 Source of Truth。物理上暂存于 `agent.models` 不改变逻辑所有权。

| Fact | Owning Module | Who Can Create | Who Can Update | Who Can Read | Source of Truth | Kind | Immutable? | Canonicalization Rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DeviceRecognitionInbox` | Identity/Recognition | Recognition Intake | Inbox/association/finalization transition applications，按独立状态轴 | Identity、Resolver、Operations | Inbox row + canonical event id | Fact / delivery ledger | No；但原始事件 provenance 不重写 | Push/poll 以 canonical/source event key 去重 |
| `TrackSnapshot` | Identity/Track Association input projection | Behavior Runtime 通过 Identity-owned `TrackSnapshotSink` 发布 | 同 token 发布新 immutable snapshot；buffer prune | Track Association、Binding heartbeat、Visit activity | 当前 Runtime 内 `TrackSnapshotBuffer`；上游 Person Track 属于 Behavior | Ephemeral projection | 单条 Yes | 完整 `store:camera:runtime:track`，不跨 Runtime |
| `TrackAssociationEvaluation` | Identity/Track Association | Track Association Audit | 不更新算法结论；Review 是独立事实 | Resolver、Binding、Operations | evaluation row | Fact | Yes | assessment fingerprint 幂等；不由 Binding 覆盖 |
| `TrackAssociationFinalizationJob` | Identity/Binding | Resolver 在 canonicalization 后按 Binding contract 创建 | Finalizer claim/application/projection terminal state | Identity、Visit provenance guard、Operations | finalization job row | Workflow fact | No；application provenance 不重写 | recognition id 唯一；Job 的 Visit 为创建时 Canonical Visit |
| `TrackIdentityBinding` | Identity/Binding | Finalizer | Heartbeat 可延长 expiry；conflict/supersede 可终止 | Exact Cache、Behavior backfill、Visit provenance guard | binding row | Fact | 核心 application provenance Yes；lifecycle fields No | exact token 最多一个 unterminated confirmed Binding |
| `CameraTrackIdentityCache` | Identity/Binding runtime projection | Finalizer projection | Heartbeat refresh、track-loss/conflict/runtime cleanup invalidation | Behavior、Visit Entry、Visit Activity | Durable Binding + exact active predicate | Projection | No | exact Store/camera/runtime/track only；无 DB fallback |
| `CustomerEntryEvent` | Visit/Entry | CustomerEntryVisit | Replay returns existing；不改换 track scope | Visit、Greeting、Operations | customer entry row | Fact | Yes in meaning | entry key idempotent；一个 event 最终归属一个 Visit |
| `VisitEntryEvidence` | Visit/Entry | CustomerEntryVisit / convergence transaction | Merge 移到 Canonical Visit | Visit modules | evidence row | Fact | Association可迁移，证据本身不改 | 每个 entry event 指向一个最终 Canonical Visit |
| `Visit` | Visit/Core | Visit Entry 或 Resolver | Resolver、Convergence/Merge、VisitActivity 的窄更新 | Identity provenance validation、Member、Behavior、Effects、Operations | visit row + supersede chain | Fact | No | `resolve_canonical_visit_id` 沿 supersede chain；禁止循环 |
| `MemberArrival` | Visit/Arrival | ReusableVisitResolver；显式 repair tool 例外 | VisitMerge 可 supersede/converge | Effects、Member Statistics、Profile、Operations | arrival ledger | Fact | 原始 occurrence Yes；supersede fields No | 一个 Visit 最多一个 Arrival；仅 Effective Arrival 计数 |
| `MemberArrivalEffect` | Effects | Resolver transaction 通过 EffectIntentRepository | Merge convergence；consumer claim/ACK/fail | Effects、Profile、Operations | effect ledger | Fact / intent ledger | Intent identity Yes；delivery state No | 每个 effective Arrival/effect type 一个 active intent |
| `BehaviorEvent` | Behavior/Events | Behavior Event Store | Visit attach、exact Binding backfill；冲突保留 | Behavior、Operations、Evidence、Visit merge | behavior event row | Fact | 行为 assessment Yes；归属/provenance 可受控补充 | event key 幂等；identity 只来自 exact confirmed Binding |
| `association_outcome` | Visit/Projection | Projection worker | 可重复 rebuild | Operations | Visit + Effective Arrival + Inbox facts | Projection | No | 从当前 Canonical Visit/Effective Arrival 重建 |
| `Member.visit_count` | Member/Statistics | Member creation initializes | MemberStatisticsRecalculator | Member、Operations、API | Effective MemberArrival set | Projection | No | 只数 unsuperseded Visit 上匹配 Member 的 Effective Arrival |
| `Member.last_visit` | Member/Statistics | Member creation initializes | MemberStatisticsRecalculator | Member、Operations、API | Effective MemberArrival occurred_at | Projection | No | Effective Arrival 最大 Event Time |
| `CameraIdentityBindingPolicy` | Identity/Track Association | Policy application | 新版本关闭旧 effective interval | Track Association、Binding、Operations | policy rows | Fact | 每个版本 Yes | camera 在任一 Event Time 至多一个 effective policy |
| `Member` | Member | Member Management | Member Management；statistics 仅写派生字段 | Identity mapping、Visit、Effects、Operations | member row | Fact + projections | Identity fields No | Member id 为主体；不得由 Visit/Behavior 创建 |
| `EvidenceClip` | Evidence | Evidence request port | Evidence processor 状态/路径 | Behavior、Identity audit、Operations | evidence clip row + device recordings | Fact / processing ledger | Request interval Yes | device recordings 是媒体 Source of Truth，gap 显式 |

## Provenance Fence

Phase 2C Convergence 可以读取 `TrackAssociationFinalizationJob` 与
`TrackIdentityBinding` 是否引用参与 Visit。任何引用都会阻断 V1 convergence，且不区分
Job/application status。Visit 模块没有修改、删除或重定向这些 Identity facts 的权限。
