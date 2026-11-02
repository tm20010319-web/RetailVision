# Staff Greeting 当前合同

正式 Offline 入口为 `scripts/run_staff_greeting_offline.py`，编排位于
`agent/behavior/staff_greeting_offline.py`，正式规则为
`agent/behavior/staff_greeting_evaluation.py` 的 OfficialGreetingCore。
Staff Role 是服装观测，不是具名员工身份；演示 mock name 不能当作 Staff Identity。

逐帧使用 verified PTS，输出同 Run 的 `greeting_summary.json`、视频和 manifest。
这是 artifact-only、track-only 路径，不写 Member、Visit、BehaviorEvent 或员工身份。
消费者只能使用经过校验的 Summary/Artifact Result，并明确 `source=offline_mp4` 与 analysis_run_id。
现有 `/api/staff-greetings/summary` 是 legacy DB 查询，尚未接入这个 Offline Summary。

confirmed 必须满足 Staff 主动接近、移动趋势、距离缩短、区域、近距和持续时间等现有门槛。
终态 confidence 使用最终连续 qualifying sustained window 的最小值，阈值保持 0.7。
Approach 可能在该窗口前锁定；expiry 使用 lifetime confidence 作诊断，不能计入成功数。
不能以 median/percentile、最近员工回退、Track 拼接或特定视频分支替代当前规则。

`BEHAVIOR_REALTIME_GREETING_ENABLED` 默认 false；V2 shadow 子开关不能绕过父开关。
`demo/greeting/` 是单独宽松演示，不能替代正式 Offline qualification。

单视频已经 Demo ready；已知限制包括 Track fragmentation、sticky Staff Role 的潜在过期风险、
Vest torso guard 的泛化范围、imgsz=1280 的跨 GPU 性能未验证、provisional 标定、缺多视频统计基线。
approach confidence 的风险目前是理论可能，不能描述为已发现的误确认。
优先积累多视频 Golden Replay、正式相机标定/部署 benchmark，再设计只读 Summary 消费。
完整风险分级、已解决项和限定证据保留在
[Greeting 风险与加固参考](../repository/STAFF_GREETING_V1_REMAINING_RISKS_AND_HARDENING_20260831.md)。
