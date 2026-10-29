# Phase to Module Mapping

Phase 是 capability delivery milestone，不是 package 或事实所有者。以下 `Phase
Ownership` 只表示首次交付/主要变更来源。

## Phase 1

| Module | Delivered capability |
| --- | --- |
| `runtime/application` | Unified StoreRuntime、启动/停止顺序 |
| `runtime/platform` | Store Lease、mode fence、runtime instance |
| `identity/application/runtime` | new Identity runtime composition |
| `behavior/application/runtime` | Behavior runtime composition |

## Phase 2A

主要模块：`identity/track_association`。

| Module | Delivered capability |
| --- | --- |
| `behavior/tracking` | Person Track observation 和 exact `track_token` 的生产输入 |
| `identity/recognition` | Event Time、Device/Camera/Store resolution 输入 |
| `identity/track_association` | Snapshot buffer、runtime registry、candidate provider、Face Spatial、Primary Snapshot、assessment、immutable Evaluation、policy/audit |
| `runtime` | 共享 snapshot/cache/registry 的 composition；不应用 identity |
| `visit/resolver` | 接受 Evaluation 结果，但不把 Phase 2A 等同于 Visit module |

Phase 2A 结束于 Evaluation/Shadow audit，不创建 Binding。

## Phase 2B Step 1-3

| Module | Delivered capability |
| --- | --- |
| `identity/binding` | Finalization Job、Binding application、conflict、recovery、exact Cache、heartbeat、unique fence |
| `behavior/events` | confirmed exact Binding provenance backfill |
| `runtime/application` | capability gate、worker lifecycle、fatal supervisor、shutdown barrier |
| `identity/recognition` | terminal finalization projection onto Inbox |
| `visit/resolver` | canonical Visit 确定后创建 Finalization Job |

Phase 2B 跨模块，但 Durable Binding 决策只属于 `identity/binding`。

## Phase 2B Step 4

主要模块：`visit/activity`。

| Module | Delivered capability |
| --- | --- |
| `visit/activity` | exact confirmed Track Event Time 单调推进 Canonical `Visit.last_seen_at` |
| `identity/binding` | 提供只读 exact active projection，不拥有 Visit update |
| `runtime/application` | refresher lifecycle 与 capability gate |
| `behavior/application/runtime` | 在 frame path 提交 activity observation |

它不关闭 Visit、不延长 Binding、不做 Backfill。

## Phase 2C

| Module | Delivered capability |
| --- | --- |
| `visit/convergence` | exact Anonymous entrance Visit promotion/merge planning、provenance fence |
| `visit/merge` | audit-preserving Visit/Arrival/Effect convergence |
| `visit/resolver` | recognition transaction 内先 convergence 再创建 Finalization Job |
| `visit/entry` | Recognition-first/Entry-later 时窄复用 exact confirmed Canonical Member Visit |
| `identity/binding` | 只提供 provenance guard 和 exact Cache projection；Phase 2C 不修改它 |

状态为 `IMPLEMENTATION_COMPLETE / FULL_REAL_E2E_PENDING`。

## Future Phase 2D

主要模块：`behavior/merchandise`。

| Module | Planned capability |
| --- | --- |
| `behavior/merchandise/tracking` | Frame -> MerchandiseObservation；SAM3 是 Adapter |
| `behavior/merchandise/association` | exact Person Track <-> Product Track，允许 ambiguous |
| `behavior/merchandise/interaction` | pickup_candidate/pickup/holding/put_back temporal state |
| `behavior/events` | 复用或最小演进 BehaviorEvent |
| `evidence` | reviewable clips/overlays |
| `operations/api` | 只在核心事实稳定后增加 read model |

Phase 2D 不修改 Identity Binding、Visit canonicalization、MemberArrival、StoreRuntime
platform 或 Production Gate。

## 禁止的 Phase 目录

```text
phase2a/  phase2b/  phase2c/  phase2d/
```

禁止原因：一个 Phase 横跨多个长期模块，而同一模块也会跨多个 Phase 演进。按 Phase
建目录会破坏事实所有权、接口 locality 和后续维护。

