# Development Guide v2

## 1. 开发前四问

1. 哪个 Business Capability 拥有这个决策？
2. 调用者需要学习的最小 Interface 是什么？
3. 事务、Event Time、lease、幂等和错误模式由谁隐藏？
4. 它在 Unit/Integration/E2E 哪一层验证？

不要先问文件放哪里，也不要按 Phase 编目录。

## 2. 新能力位置

| Change | Owning module | First Interface | Rule |
| --- | --- | --- | --- |
| Device recognition transport | `identity/recognition/adapters` | normalized intake | push/poll 同一 Inbox |
| Person tracker | `behavior/tracking/adapters` | `Frame -> PersonObservation[]` | `track_token` 是 Person namespace |
| Track association algorithm | `identity/track_association` | `assess(request)` | 结束于 immutable Evaluation |
| Binding/finalization | `identity/binding` | finalization/application interface | only module allowed to mutate Binding |
| Visual entry rule | `behavior/entry` | `TrackObservation -> CustomerEntryObservation` | 不写 Visit |
| Visit Entry | `visit/entry` | `confirm_entry` | 可写 Entry facts；窄复用不等于 member resolution |
| Visit reuse/convergence | `visit/resolver` / `visit/convergence` | Resolver / convergence plan | provenance fence before writes |
| Visit activity | `visit/activity` | exact confirmed activity input | 只推进 `last_seen_at` |
| Behavior event | `behavior/events` | record/attach identity | 可以匿名；不创建 Arrival |
| Effect | `effects` | intent + consumer interfaces | Intent 与 external execution 分离 |
| Customer Journey | `operations/read_model` + `api` | query DTO | 只读组合现有事实 |
| VIP Welcome | `effects/consumer` + frontend | effect executor | 不在 Resolver 内执行外部动作 |
| SAM3 | `behavior/merchandise/tracking/adapters` | `MerchandiseTracker` | 只输出视觉事实 |
| Person/Product association | `behavior/merchandise/association` | association result incl. ambiguous | 不决定 Member |
| pickup/holding/put_back | `behavior/merchandise/interaction` | temporal assessment | SAM3 单帧不能直接确认 |

## 3. Phase 2D 开发规则

Phase 2D 开始前必须先有独立 SAM3 PoC 和冻结的 MerchandiseObservation contract。
然后按顺序实现：tracking Adapter、PersonProductAssociation、Interaction、BehaviorEvent
integration、read model。不要先改 Schema 或 StoreRuntime。

Product namespace 必须独立：

```text
Person:  store:camera:runtime:track
Product: product_track_id / optional product_instance_id
```

SAM3 不能补身份、查 Member、创建 Visit、触发 Arrival/Effect 或改变 Binding。

## 4. Interface 评审

- Interface 包含参数，也包含 scope、clock、ordering、transaction、retry、error 和性能约束。
- 接受依赖，不在 domain/application 内构造具体 Adapter。
- 返回 assessment/result；副作用集中在 application Interface 后。
- 两个真实 Adapter 之前不要创造假 seam；但 production/in-memory adapter 已存在的
  persistence、clock、camera、model seam 应保留。
- 删除测试：删除模块后若复杂性散落到多个 caller，模块有 depth；若复杂性消失，说明
  它只是 pass-through。

## 5. Persistence 与 API

- 新 business code 不再直接扩大 `agent.models` 依赖；先定义 capability-local Port。
- 当前 Schema 受保护；migration 需单独批准并有 disposable-DB tests。
- API Route 只能调用 command/query Interface，不打开 Session、不查询 ORM。
- Operations queries 只读，不通过“修复显示”修改事实。

## 6. Review Checklist

1. 使用 `CONTEXT.md` 术语且没有用 Phase 替代模块名。
2. 事实 owner、创建者、更新者与 [FACT_OWNERSHIP_V2.md](FACT_OWNERSHIP_V2.md) 一致。
3. 依赖方向满足 [MODULE_DEPENDENCY_RULES_V2.md](MODULE_DEPENDENCY_RULES_V2.md)。
4. exact Track 必须包含 Store/camera/runtime/id/token 全 scope。
5. Event Time 与 processing time 不混淆。
6. Binding/Evaluation/Visit provenance 不被 repair、projection 或 Phase 2D 重写。
7. Production import graph 不新增 Legacy/Demo。
8. 测试穿过同一 Interface，覆盖 failure、ambiguity、retry 和 idempotency。
9. 新事实先确定 Source of Truth，再决定是否需要 projection。
10. 文档和模块映射随新的 Interface 一起更新。

