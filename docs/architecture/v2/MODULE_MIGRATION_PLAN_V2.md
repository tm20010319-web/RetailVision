# Module Migration Plan v2

状态：`DESIGN_ONLY`。完整真实 Phase 2A-2C E2E 完成前不执行任何物理迁移。

## Stage 状态

| Stage | Scope | Status | Reason / exit gate |
| --- | --- | --- | --- |
| A | root governance、generated artifacts、tool grouping | `DEFER` | Batch 1 已完成；进一步 cleanup 与业务架构无关，不应打断 E2E |
| B | Demo isolation | `DEFER` | 方向仍有效，但不是当前业务风险最高项；先冻结 E2E baseline |
| C | Legacy isolation | `NEEDS_REDESIGN` + `BLOCKED_BY_E2E` | Phase 2B/2C 后 new Identity runtime 依赖更深；需先去除 eager import 并证明 mode parity |
| D | capability packages and repository Ports | `BLOCKED_BY_E2E` | `agent.models`、Resolver、Finalizer、StoreRuntime blast radius 很高 |
| E | final package/ORM split and compatibility removal | `BLOCKED_BY_E2E` | 只能在 repository parity、schema tests、full import scan 后执行 |

## 推荐顺序

### Stage B: Demo

将 greeting/camera profile/demo frontend 迁到单向依赖 production public interfaces 的
Demo composition root。不得让 production import Demo。无业务逻辑变化。

### Stage C: Legacy

1. 为 Legacy 建立独立 composition root。
2. 移除 `pipelines/__init__.py` 的 eager re-export。
3. 将 `run_store_runtime.py` 的 `create_member_business_runtime` import 延迟到 legacy branch。
4. 从 `pipelines/device_identity_runtime.py` 的 new import graph 移出
   `EntryEventCoordinator`、`MemberBusinessRuntime`、`RealtimeEntryPipeline`。
5. 从 `CustomerEntryVisit` 隔离旧 `_customer_visit_identity` command，保留新-mode
   Visit Entry writer 与 Phase 2C narrow reuse。
6. 通过 same-DB mode/Lease tests 后再移动文件。

### Stage D: Capability split

按以下顺序一次迁移一个 Interface，不复制两套可执行实现：

1. Behavior `TrackObservation` / snapshot publication Interface；
2. Identity `track_association` 与 `binding` 分离；
3. Visit Entry、Core、Merge/Convergence、Activity Interfaces；
4. Resolver 对 Effects/Member/Identity provenance 的 Port；
5. Recognition intake/runtime；
6. Member、Effects、Evidence、Operations/Profile；
7. Runtime composition 只保留 factories 和 supervision。

每一步保留一个短期 compatibility import，调用者迁完立即移除。不得在 path move 中
改变 Event Time、threshold、transaction、lease 或 provenance。

### Stage E: Persistence and final packages

Repository Port 稳定后，按事实所有权拆 `agent.models`：Identity、Behavior、Visit、
Member、Effects、Evidence。Schema evolution 仍由统一 migration runner 排序，但每个
migration 的 owner 明确。最后消除 `pipelines` broad exports 和临时 aliases。

## 每阶段 Gate

- 工作树和 baseline commit 已记录；用户改动不被 stage/revert。
- 当前聚焦测试与完整 Phase 2A-2C E2E 均通过。
- import graph 证明 new StoreRuntime 不加载 Legacy/Demo。
- Schema diff 为空，除非该 stage 有单独批准的 migration。
- Production Gate 默认值、Face Spatial status 和真实数据库事实未变化。
- 回滚单位是单一迁移 commit，不使用 destructive reset。

