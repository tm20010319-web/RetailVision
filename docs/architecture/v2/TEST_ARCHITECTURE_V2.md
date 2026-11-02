# Test Architecture v2

当前自动测试目录为 `test/`，实际分组与基线见根 test/README.md。逻辑上按业务模块和验证类型组织，不按 Phase
建立长期测试 package。

## 1. 测试类型

| Type | Scope | External dependencies |
| --- | --- | --- |
| Unit | domain rule、DTO validation、pure policy | none/in-memory |
| Integration | application + repository + SQLite transaction | isolated temporary DB |
| Concurrency | claims、lease、unique fence、multi-writer | SQLite + controlled threads |
| Crash Recovery | post-commit retry、expired claim、fail-stop | fault injection + restart |
| Schema | migration、indexes、legacy data compatibility | disposable DB copies |
| Runtime Composition | startup order、gate、fatal/shutdown barrier、no legacy execution | fake runtimes/adapters |
| Hardware / Camera | RTSP、YOLO/ByteTrack、Face spatial transform、GPU | controlled device/recording |
| End-to-End | real Event Time through final facts | isolated StoreRuntime and test DB first |

## 2. 按模块测试范围

| Module | Unit | Integration / advanced |
| --- | --- | --- |
| `identity/recognition` | normalization、dedupe keys、state transitions | push/poll same Inbox、cursor-after-commit、mapping wait/recovery |
| `identity/track_association` | candidate eligibility、Primary Snapshot、Face Spatial clocks、ambiguity | snapshot window/watermark、Evaluation idempotency、policy audit |
| `identity/binding` | application gate、exact token、active predicate | Finalization claims、recovery、conflict、unique fence、Cache/backfill、heartbeat、fail-stop |
| `visit/entry` | crossing/result validation、replay | Entry facts、Anonymous Visit、exact confirmed narrow reuse、lease/provenance mismatch |
| `visit/resolver` | reusable validation、time anomaly | one transaction creates/reuses Visit/Arrival/Effect and terminal Inbox state |
| `visit/convergence` | exact candidate and ambiguity planning | promotion、merge、provenance fence rollback、Job created after canonicalization |
| `visit/activity` | coalescing、event-time monotonic rule | canonical resolution、stale/closed/future no-op、multi-camera max |
| `behavior/tracking` | token/bbox/role validation | YOLO/ByteTrack adapter、lost track、snapshot publication |
| `behavior/events` | assessment/event validation | idempotent record、anonymous event、exact backfill、provenance conflict |
| `effects` | effect key/expiry rules | same-transaction intent、claim fencing、ACK/fail/idempotency |
| `member` | tier/priority | management、device sync、statistics from Effective Arrival only |
| `runtime` | state machine | startup rollback、Lease loss、persistent fatal、shutdown join order、gate disabled no mutation |
| `evidence` | interval/coverage | recording gaps、multi-segment trim、status recovery |
| `operations/api` | DTO/query shaping | read-only behavior、route never opens ORM transaction |

## 3. Phase 2A-2C E2E 顺序

1. `runtime/platform`：单 Store Lease、mode、每次启动唯一 runtime instance。
2. `behavior/tracking`：真实 camera/recording 产生 Person Track、snapshot 和 watermark。
3. `identity/recognition`：真实/受控 Device Recognition Event 进入同一 Inbox，Event Time 正确。
4. `identity/track_association`：Device/Camera mapping、Primary Snapshot、Face Spatial 和 Evaluation。
5. `visit/entry`：Entry-first 与 Recognition-first 两种时序，Entry facts 可审计。
6. `visit/resolver`：Visit/Arrival/Effect Intent 一次事务和幂等 replay。
7. `visit/convergence`：Anonymous -> Member Canonical，含 zero/one/many candidate 与 fence。
8. `identity/binding`：只在 verified test configuration 下启用 Finalization，验证 Binding/Cache/backfill/heartbeat/recovery。
9. `visit/activity`：confirmed exact Track 只推进 Canonical `last_seen_at`。
10. `effects/member/operations`：Effective Arrival 统计、Intent ledger 和读模型一致。
11. 完整 restart/crash/lease-loss 回归；确认 Legacy/new 不竞争同一 Store/DB。

前一步失败时不得用后续模块的写入绕过。真实生产 Gate 仍需独立授权。

## 4. 商品行为测试层

对商品能力变更先测试 tracking adapter，再测 association 和 interaction，之后
才测 BehaviorEvent 与 anonymous/member attribution。SAM3 hardware benchmark 不得混入
Phase 2A-2C E2E baseline。
