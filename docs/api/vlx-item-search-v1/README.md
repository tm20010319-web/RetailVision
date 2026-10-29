# 监控物品搜索接口文档 V1（前端联调稿）

版本：1.1.0 · 更新日期：2026-10-27 · 部署记录：2026-10-23 曾完成公网联调；当前运行状态以实际服务检查为准。

本文对应独立 VLX 物品搜索联调服务，不修改项目生产 API。服务已连接真实两路摄像头，前端可使用下方地址和原 Java 登录系统签发的 Token 联调。
公网 HTTP/WSS 已完成鉴权、实时帧标注与清空检查；视频与识别质量仍受网络和场景影响。
在线 Swagger： https://desktop-qaer92d.tail173be1.ts.net/docs 。

配套文件：[OpenAPI](openapi.yaml)、[会话示例](mock-session.json)、[带标注的视频帧示例](mock-frame.json)。
OpenAPI 描述 HTTP 接口；WebSocket 消息及绘制规则以本文为准。

## 成功返回格式速查（前端先看这里）

成功响应采用 **JSON**。创建会话成功返回 HTTP `201`，提交或切换搜索成功返回 HTTP `200`，删除会话成功返回 HTTP `204` 且没有响应体。
**提交搜索成功表示查询已被接收，不表示已经找到物品。识别结果异步产生，通过 WebSocket 推送视频帧和该帧的标注框。**

以下示例只展示关键字段，完整 Session 和 frame 结构分别见第 3.4、6.1 节。
示例 session_id 需要替换为创建会话时实际返回的值；jpeg_base64 占位文字不能用于图片解码。

### A. 提交搜索成功：HTTP 200

```json
{
  "session_id": "ses_demo_001",
  "revision": 1,
  "query": "小吉他",
  "state": "searching",
  "results": [
    {
      "target_id": "small_guitar",
      "status": "searching"
    }
  ],
  "stream_path": "/ws/v1/item-search/sessions/ses_demo_001"
}
```

前端保存 session_id 和 revision，连接 stream_path 对应的 WSS 地址，发送鉴权消息后等待 frame。
完整请求地址和鉴权方式见第 2、6 节；stream_path 是路径，需要与当前服务域名组合，不是单独可用的完整 URL。

### B. 识别到物品后：WebSocket frame

```json
{
  "type": "frame",
  "session_id": "ses_demo_001",
  "revision": 1,
  "camera_id": "behavior-detection-1",
  "camera_name": "行为检测-1",
  "frame_id": 1024,
  "width": 800,
  "height": 448,
  "mime_type": "image/jpeg",
  "jpeg_base64": "<实际图片的Base64数据>",
  "annotations": [
    {
      "target_id": "small_guitar",
      "label": "小吉他",
      "bbox": [0.508, 0.718, 0.579, 0.950],
      "color": "#FF99DD",
      "view_score": 90
    }
  ]
}
```

| 字段 | 前端用途 |
| --- | --- |
| session_id、revision | 只处理当前会话、当前查询版本，丢弃旧查询结果 |
| camera_id、camera_name | 定位需要显示画面和框的摄像头卡片 |
| jpeg_base64、mime_type | 显示当前帧，例如 `data:image/jpeg;base64,` 加 jpeg_base64 |
| width、height | 视频原始尺寸，用于缩放图像和标注 |
| annotations | 当前摄像头此帧应画的全部框，必须与这一张图片一起使用 |
| bbox | 归一化的 `[左,上,右,下]`，分别乘图片宽高后绘制矩形 |
| label、color | 显示物品名称、文字颜色及框颜色 |
| view_score | 显示“视角评分 90”，不是识别置信度或准确率 90% |

`annotations: []` 仅表示**这一摄像头的当前帧不画框**，可能是目标在另一路获胜、暂未识别到或已清空查询。
不能仅凭某一路的空数组判断整体没有找到；整体结果查看 Session 的 `results[].status`：
`found` 为有可采用位置，`searching` 为等待识别，其他状态及含义见第 4 节。
前端绘制新帧时先清除旧图和旧框，避免上一帧的框残留。

## 1. 页面需要实现什么

用户输入物品名称，点击搜索或按回车，持续查看两路实时画面；找到后，在该物品更可靠的一路画框，显示“物品名称 视角评分 90”。
不要求“蓝桌上”等自然语言位置描述。页面可突出显示命中的摄像头名称。

| 输入 | 期望搜索目标 |
| --- | --- |
| 小蜜蜂、蜜蜂、蜜蜂玩偶、黄色小蜜蜂玩偶 | 小蜜蜂 |
| 大吉他、大电吉他 | 大吉他 |
| 小吉他 | 小吉他 |
| 吉他、电吉他、白色吉他、白色电吉他 | 大吉他、小吉他，分别标注 |
| 小蜜蜂、吉他 | 三个目标分别搜索；“、”是多目标分隔符 |
| 水杯等其他名称 | 已开放中英文任意文字查询，结果标记 unverified；识别效果需逐类实测 |
| 空字符串 | 继续播放视频，停止识别并立即清除该会话全部标注 |

英文名称中的空格属于名称本身，例如 `white coffee mug` 是一个查询，不能按空格拆成三个目标。
V1 最多 3 个解析后的目标、2 路摄像头；原始 query 最多 80 个 Unicode 字符。

**能力边界：**目前只有小蜜蜂、大吉他、小吉他有用户标注及部分场景验证。
当前 VLX 预览使用独立官方模型，不等于这些标注已用于训练 VLX。
不能从这三种物品的标注推导出其他物品也能稳定识别。其他名称已接入 VLX 官方中英文文字查询，并动态映射目标 ID；
“鼠标”在当前画面中已完成一次实际查询命中，这不是该类别的全面验证。其他名称返回 `validation_status: unverified`。
非法名称返回 `422 QUERY_UNSUPPORTED`；没有结果不代表物品一定不存在。

## 2. 联调地址和鉴权

当前联调地址（电脑及 Tailscale/API 服务需要保持运行）：

```text
API_BASE = https://desktop-qaer92d.tail173be1.ts.net/api/v1/item-search
WS_BASE  = wss://desktop-qaer92d.tail173be1.ts.net/ws/v1/item-search
```

HTTP 使用 `Authorization: Bearer <ACCESS_TOKEN>`，JSON 请求使用 `Content-Type: application/json`。
沿用已有 Java 登录鉴权：HTTP 和 WebSocket 均使用登录系统签发的同一份 Java JWT，无需兑换或领取独立联调 Token。本服务不提供登录、刷新或 Token 签发接口；过期后按原 Java 登录流程获取新 Token。Token 和签名密钥不打包在文档或 Mock 中。
服务端校验 HS256 签名、exp、type=front、正整数 uid 及 sub 与规范化 uid 一致；密钥按已有 JJWT 标准 Base64 语义读取，不作十六进制解码。前端无需传 uid，也不得持有签名密钥。
会话归属使用校验后的 uid；同一用户刷新 Token 后仍可访问自己的未过期会话。退出后的即时撤销取决于原系统撤销机制，本地验签不另行实现撤销。
所有会话及帧均校验 Token 所属用户，不允许凭 session_id 读取他人画面。

浏览器 WebSocket 不能按 fetch 的方式设置 Authorization 头，因此使用连接后的第一条鉴权消息，见第 6 节。
当前 Funnel 使用 HTTPS/WSS，前端不需要安装 Tailscale。CORS/WS 默认允许 localhost/127.0.0.1 的 5173、3000 端口及同源页面；其他前端域名需添加到 config/vlx_item_search_api.yaml 的 allowed_origins 后重启服务。
前端无需也不应获得摄像头内网地址、RTSP 地址或摄像头密码。

## 3. HTTP 接口一览

以下路径均相对于 API_BASE，均已实现。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/catalog` | 查询已登记目标、别名、任意文字查询能力及并发限制 |
| GET | `/cameras` | 获取允许访问的摄像头及在线状态 |
| POST | `/sessions` | 创建本浏览器的搜索会话，默认空查询 |
| GET | `/sessions/{session_id}` | 获取会话和最新搜索摘要；不是精确同步画框数据 |
| PUT | `/sessions/{session_id}/query` | 开始搜索、切换查询或清空查询 |
| DELETE | `/sessions/{session_id}` | 结束会话并释放搜索资源 |

### 3.1 GET /catalog

当前响应结构示例：

```json
{
  "items": [
    {"target_id":"bee_plush","label":"小蜜蜂","aliases":["小蜜蜂","蜜蜂","蜜蜂玩偶","黄色小蜜蜂玩偶"],"validation_status":"scene_checked","color":"#FFD54A"},
    {"target_id":"large_guitar","label":"大吉他","aliases":["大吉他","大电吉他"],"validation_status":"scene_checked","color":"#48D7FF"},
    {"target_id":"small_guitar","label":"小吉他","aliases":["小吉他"],"validation_status":"scene_checked","color":"#FF99DD"}
  ],
  "groups": [{"aliases":["吉他","电吉他","白色吉他","白色电吉他"],"target_ids":["large_guitar","small_guitar"]}],
  "general_queries":"enabled_unverified",
  "max_targets":3,
  "max_active_searches":1,
  "default_preview_fps":10,
  "recognition_interval_target_ms":3000
}
```

`general_queries` 为 `disabled` 或 `enabled_unverified`；前端可接受任意文字输入，未启用时显示后端的明确提示。
`scene_checked` 只表示已有部分场景检查，不表示任意摆放、遮挡或角度都通过。
一般查询启用后的目标 ID 由后端分配，例如 `query_7c21`；前端不要自行生成、推断或翻译模型类别。
颜色和显示名称使用后端字段，其他物品默认色为 `#7CFF6B`。

V1 约定一张显卡同时仅接纳 **1 个活跃搜索会话**；其他用户提交非空查询时返回 `409 ENGINE_BUSY`，不能覆盖当前用户的搜索。
空查询仅观看画面不占识别名额；多用户独立并发识别属于后续能力。

### 3.2 GET /cameras

```json
{
  "cameras":[
    {"camera_id":"behavior-detection-1","name":"行为检测-1","status":"online","width":800,"height":448},
    {"camera_id":"behavior-detection-3","name":"行为检测-3","status":"online","width":800,"height":448}
  ]
}
```

`status`：`connecting`、`online`、`stale`、`offline`。宽高为最近可用视频尺寸；未知时可省略。
启动时身份检查偶发超时的摄像头会每 10 秒重试，只有身份验证通过才重新接入；另一路可继续使用。
不是把两个画面拼成一张带窗口标题的桌面截图；后端按摄像头发送原始视频图像和独立标注。

### 3.3 POST /sessions

请求：

```json
{
  "query":"",
  "camera_ids":["behavior-detection-1","behavior-detection-3"],
  "preview_fps":10
}
```

`query` 可省略，默认 `""`；camera_ids 必填，1～2 个不同的授权摄像头；preview_fps 可省略，默认每路 10，范围 1～15。
预览 FPS 是发送上限，不是外网流畅度或模型推理速度保证。
创建成功返回 `201`：

```json
{
  "session_id":"ses_demo_001",
  "revision":0,
  "query":"",
  "state":"idle",
  "camera_ids":["behavior-detection-1","behavior-detection-3"],
  "preview_fps":10,
  "targets":[],
  "results":[],
  "created_at":"2026-10-23T08:00:00.000Z",
  "expires_at":"2026-10-23T08:30:00.000Z",
  "stream_path":"/ws/v1/item-search/sessions/ses_demo_001"
}
```

query 非空时也只创建会话并提交异步任务，返回 `revision:1,state:searching`，不要让 HTTP 等待模型识别完成。
每次有鉴权 HTTP 请求或活动中的已鉴权 WebSocket，后端续期 30 分钟；完全断开且无请求 30 分钟后失效并释放资源。

### 3.4 PUT /sessions/{session_id}/query

请求：

```json
{"query":"小吉他","expected_revision":0}
```

`expected_revision` 是客户端最后确认的版本，用于防止多个请求覆盖。
返回 `200` 和完整 Session 对象，示例见 `mock-session.json`：

```json
{
  "session_id":"ses_demo_001",
  "revision":1,
  "query":"小吉他",
  "state":"searching",
  "camera_ids":["behavior-detection-1","behavior-detection-3"],
  "preview_fps":10,
  "targets":[{"target_id":"small_guitar","label":"小吉他","validation_status":"scene_checked","color":"#FF99DD"}],
  "results":[{"target_id":"small_guitar","status":"searching"}],
  "created_at":"2026-10-23T08:00:00.000Z",
  "expires_at":"2026-10-23T08:30:02.000Z",
  "stream_path":"/ws/v1/item-search/sessions/ses_demo_001"
}
```

解析后的目标改变时 revision 递增，旧结果立即清除，旧任务即使完成也不得回写新版本。
相同目标集合的重复 PUT 不重复启动任务；可更新 query 显示文字，revision 不变。
版本不匹配返回 `409 REVISION_CONFLICT`，details 给出 current_revision；前端重新 GET 同步，不能盲目重放旧搜索。
非法查询返回 `422` 并保留服务端原查询；前端编辑时应按第 8 节先清空，因此不会保留旧框。

**停止搜索并清空**，发送同一接口：

```json
{"query":"","expected_revision":1}
```

返回 revision=2、state=idle、targets=[]、results=[]；视频仍播放，每帧 annotations=[]。
如果本来就是 idle，重复清空保持 revision 不变。

### 3.5 GET /sessions/{session_id} 与 DELETE

GET 返回同一 Session 结构。`results` 每个目标只有一条摘要。
命中示例：

```json
{
  "target_id":"small_guitar",
  "status":"found",
  "winner_camera_id":"behavior-detection-1",
  "view_score":90.0,
  "confirmed_at":"2026-10-23T08:00:03.000Z"
}
```

其他结果可为 `searching`、`not_found`、`ambiguous`、`unavailable`，此时没有 winner_camera_id、view_score、confirmed_at。
GET 摘要不附带画框坐标，防止前端把过时坐标叠加到另一个时刻的视频。
正常结束会话用 DELETE，成功返回 `204` 空响应；WebSocket 随后以 `1000` 关闭。
未知或过期的 session_id 返回 `404 SESSION_NOT_FOUND`。

## 4. 状态含义

| Session.state | 含义与页面提示 |
| --- | --- |
| idle | 无查询，仅播放视频 |
| searching | 新版本等待首轮有效结果，显示“正在查找” |
| active | 持续识别正常；有无找到分别看各 target 的 status |
| degraded | 某路不可用或有效结果变旧，可用的一路继续；提示摄像头状态 |
| failed | 当前会话引擎故障；清除框并显示错误，等待恢复或重新提交 |

| Result.status | 含义 |
| --- | --- |
| searching | 尚未完成当前版本的有效识别，不能提示“没有” |
| found | 当前存在可采用的位置和获胜摄像头 |
| not_found | 两路所选在线画面均完成当前版本的有效检查，但暂没有可采用结果；提示“暂未识别到” |
| ambiguous | 候选无法可靠区分，暂不画框 |
| unavailable | 没有可用摄像头或有效结果均过期，不能视为物品不存在 |

选中的部分摄像头离线且没有命中时，使用 unavailable，不返回肯定的 not_found；只有已选的所有摄像头可用且均检查过才返回 not_found。
找到时即使另一路离线，也可以保留当前可用一路的 found，同时 Session.state=degraded。

## 5. 框与画面的坐标约定

每个视频帧由一条 WebSocket `frame` 消息完整携带 JPEG 和该帧应显示的 annotations。
**必须画消息中这一张图及这一组框，不把 GET 的摘要或上一帧的框随意套到新视频上。**

- bbox 为 `[x1,y1,x2,y2]`，使用原始视频有效图像归一化坐标，左上为 (0,0)、右下为 (1,1)。
- 必须满足 `0 <= x1 < x2 <= 1` 和 `0 <= y1 < y2 <= 1`。
- 图像不包含桌面窗口、状态栏或前端留白。前端尽量让 Canvas 自身保持 width:height 比例并在 Canvas 外布局状态栏。
- 若有效显示尺寸为 W×H，矩形为 `(x1*W,y1*H,(x2-x1)*W,(y2-y1)*H)`；若视频容器有 letterbox，先计算有效图像尺寸和偏移再画框。
- annotations 仅包含该目标的获胜一路；没有框时必须明确发 `[]`，前端先清空 Canvas 再绘制，不保留历史框。
- target_id 表示搜索目标，不是全局物理实例 ID；同类外观相似的多个实物仍可能无法区分。

`view_score` 在 0～100，标签显示“视角评分 90”，**不是识别置信度或正确率 90%**。
同一 target_id 在同一 revision 下只能有一条获胜标注；不同目标可以分别选择不同摄像头。
切换获胜摄像头时，后端先清空旧一路的该目标标注，再发新一路的框；前端也按 target_id 在另一画面立即移除旧框。

## 6. WebSocket 实时画面协议

连接 `wss://<实际域名>/ws/v1/item-search/sessions/{session_id}`，在 5 秒内发送：

```json
{"type":"authenticate","access_token":"<ACCESS_TOKEN>"}
```

成功后先收到 `ready`，再收到完整 `state`，然后持续接收每路的 `frame`。
鉴权前不推送图像；失败关闭码 1008；Token 过期关闭码 4001，刷新 Token 后重连。
不要把长期 Token 拼在 URL 里。HTTP 和 WebSocket 使用同一用户身份。

所有服务端消息都有以下公共字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| type | string | ready / state / frame / heartbeat / error |
| stream_id | string | 本次连接 ID，重连后改变 |
| event_id | integer | 当前连接严格递增，不能跨连接比较 |
| session_id | string | 所属会话 |
| revision | integer | 所属查询版本 |
| server_time | string | 服务器发送时间，ISO 8601 UTC |

ready 的额外字段为 `heartbeat_interval_ms:10000`。
state 的额外字段为 `session`（完整 Session）及 `cameras`（与 GET /cameras 中 cameras 数组结构相同）。
heartbeat 无额外字段，即使摄像头断开也每 10 秒发送；持续 20 秒无任何消息则客户端认为连接中断。
error 的额外字段为 `error:{code,message,request_id}`；出现致命引擎错误还要推送 failed 状态和空标注帧，不能仅保留旧框。

### 6.1 frame 消息

完整可用示例见 `mock-frame.json`（包含实际 JPEG 的 Base64，便于前端离线演示）。
下例的 JPEG 内容为文档占位符，不可解码：

```json
{
  "type":"frame",
  "stream_id":"stream_demo_001",
  "event_id":15,
  "session_id":"ses_demo_001",
  "revision":1,
  "server_time":"2026-10-23T08:00:04.080Z",
  "camera_id":"behavior-detection-1",
  "camera_name":"行为检测-1",
  "camera_status":"online",
  "frame_id":1024,
  "frame_received_at":"2026-10-23T08:00:04.000Z",
  "width":800,
  "height":448,
  "mime_type":"image/jpeg",
  "jpeg_base64":"<JPEG_BASE64>",
  "annotations":[{
    "target_id":"small_guitar",
    "label":"小吉他",
    "validation_status":"scene_checked",
    "color":"#FF99DD",
    "bbox":[0.508,0.718,0.579,0.950],
    "view_score":90.0,
    "position_source":"optical_flow",
    "model_frame_id":1001,
    "confirmed_at":"2026-10-23T08:00:03.000Z"
  }],
  "metrics":{"preview_fps":10.0,"inference_ms":1120,"confirmation_interval_ms":3280}
}
```

`frame_id` 在同一路、本次 stream_id 内递增；不同摄像头的 frame_id 不能比较。
`frame_received_at` 是服务端收到视频帧的时间，不冒充摄像头硬件采集时间，也不解析画面内水印时间。
`model_frame_id` 是最后一次模型识别所用的源帧，可能小于 frame_id；经过 CPU 跟踪后，bbox 已对应当前 frame_id。
`confirmed_at` 是模型最近确认时间，`position_source` 为 `vlx` 或 `optical_flow`。
metrics 中尚无测量值的字段可省略。

视频持续刷新，VLX 周期性复核；已有本机场景测得双路平均复核约 3.28 秒，这不是所有查询、外网或所有部署的时延承诺。
双路模式下，偶发空结果不会立刻删除经过当前画面验证的跟踪框；多个候选只有与已有轨迹唯一匹配时才可继续采用，未初始化的歧义目标仍不画框。
沿用框仍标记 position_source=optical_flow，model_frame_id 和 confirmed_at 保留真实的上次确认值，不因一次漏检刷新。
无法跟踪、画面不新鲜、超过有效期时必须清框；接收帧年龄超过 2 秒不可标注。双路模式的模型源帧有效期现为 **16 秒**，以跨过共享模型的短暂连续漏检；期间逐帧验证跟踪，前端不能自行冻结框或延长有效期。
本机窗口沿用旧确认时显示“跟踪中”；这不代表模型已经解决远景外观混淆，也不保证长时间漏检仍能保留框。单路旧入口默认仍为 8 秒。
双路模式对本会话中已识别过、主查询本轮漏掉的目标，最多增加一次独立文字复核，仍使用同一张图和同一组自动候选；因此遇到漏检时复核间隔可能增加，实际耗时以 metrics 为准。
摄像头无新图时不重复伪造 frame_id 或刷新旧 frame_received_at；通过 state 消息报告 stale/offline，前端清除该路框并显示画面中断。

### 6.2 顺序、重连与背压

前端只接受当前 session_id、当前 revision、当前 stream_id 的帧。
HTTP 成功切换版本后，所有旧版本 frame/state 都丢弃；编辑期间还要在本地禁止任何标注重现。
每路异步解码 JPEG 后再次核对 revision 和 frame_id，丢弃落后的帧，避免旧解码任务覆盖新画面。
帧包较大时不排队补播；后端及前端每路只保留最新待处理帧，慢客户端降帧，不能把几秒前的视频伪装成实时。

连接中断立即清除标注；重连间隔 1、2、4、8、10 秒，之后最多每 10 秒一次。
重连后会收到新 stream_id 和完整 state，先同步版本再绘制。关闭页面时关闭 WS 并尽力 DELETE，意外断开由会话超时清理。
本版本用 JSON/Base64 帧便于浏览器直接绘制，存在编码与带宽开销；实际外网 FPS 需测量。
后续若升级 WebRTC 等传输，需另行约定逐帧时间与框同步，不能只替换视频地址后继续套用旧坐标。

## 7. 错误格式

```json
{
  "error":{
    "code":"QUERY_UNSUPPORTED",
    "message":"当前服务尚未启用其他物品查询，请先测试小蜜蜂、大吉他或小吉他",
    "request_id":"req_demo_001",
    "details":{}
  }
}
```

| HTTP | code | 前端处理 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 修正字段、空 camera_ids 或参数范围 |
| 401 | AUTH_TOKEN_MISSING / AUTH_TOKEN_INVALID / AUTH_TOKEN_EXPIRED | 补充原 Java 登录 Token；过期时按原登录流程刷新后重试 |
| 401 | AUTH_TOKEN_TYPE_INVALID / AUTH_UID_INVALID / AUTH_SUBJECT_MISMATCH | 核对 Java 登录 Token 合同，不能修改 Token 绕过校验 |
| 403 | FORBIDDEN | 无摄像头或会话访问权限 |
| 404 | SESSION_NOT_FOUND | 创建新会话，不复用已过期的 ID |
| 409 | REVISION_CONFLICT | GET 同步最新版本，提示请求已变化 |
| 409 | ENGINE_BUSY | 当前识别名额已占用，保留输入并允许稍后重试 |
| 422 | QUERY_UNSUPPORTED / TOO_MANY_TARGETS | 展示具体原因，不能显示“物品不存在” |
| 429 | RATE_LIMITED | 按 Retry-After 秒数退避 |
| 503 | ENGINE_NOT_READY | 模型正在加载或不可用，按 Retry-After 秒数重试 |

未找到目标是正常搜索状态，不是 HTTP 404。未识别、候选不唯一、摄像头离线三种状态要区分。

## 8. 前端调用顺序和绘制示例

1. 获取 catalog 和 cameras；POST 创建 query 为空的独立 session。
2. 建立 WS、发送 authenticate，收到 ready/state 后播放两路视频。
3. 输入框开始编辑时，立即隐藏全部框；若原查询非空，串行发送一次 PUT 空查询并等待版本确认。
4. 点击搜索或回车，将完整输入发送 PUT。控制请求串行，使用最后确认的 expected_revision。
5. PUT 成功后采纳新 revision，解除本地编辑锁；展示 searching，等待该版本 frame 绘制标注。
6. 切换或清空时重复版本处理；不按每个键输入立即启动模型，不用前端定时器硬凑 3 秒识别。
7. 接收 frame 时统一缩放图和框；frame 内 annotations=[]、state 为 failed 或相机断流时清框。

下面只演示**一条合法 frame 消息的 Canvas 绘制**，不包含完整的网络、鉴权、状态机或解码队列。
label 必须当普通文本绘制；若放 DOM，使用 textContent。

```js
function drawFrame(canvas, imageBitmap, message) {
  canvas.width = message.width;
  canvas.height = message.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(imageBitmap, 0, 0, canvas.width, canvas.height);
  for (const item of message.annotations) {
    const [x1, y1, x2, y2] = item.bbox;
    const x = x1 * canvas.width, y = y1 * canvas.height;
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, (x2 - x1) * canvas.width, (y2 - y1) * canvas.height);
    ctx.font = '17px Microsoft YaHei, sans-serif';
    ctx.fillStyle = item.color;
    ctx.fillText(`${item.label} 视角评分 ${Math.round(item.view_score)}`, x, Math.max(18, y - 6));
  }
}
// CSS: canvas { display:block; width:100%; height:auto; }
```

HTTP 示例（将 ACCESS_TOKEN 替换为单独发放的联调凭证）：

```bash
curl -X PUT 'https://desktop-qaer92d.tail173be1.ts.net/api/v1/item-search/sessions/ses_demo_001/query' \
  -H 'Authorization: Bearer <ACCESS_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{"query":"小吉他","expected_revision":0}'
```

## 9. 前端交付验收与运行方式

前端先用 mock-frame.json 中的图和框完成布局，Mock 画面是 2026-10-23 的保存画面，并非实时接口数据。
其中得分及时间为协议演示值，不是新的模型实测记录。

前端验收：大小琴分别显示、只标搜索物品、同目标只在获胜一路画框、清空立即去框、
旧版本不回框、画面缩放不偏移、无结果/歧义/断流/加载分别提示，评分不显示为置信度百分比。

HTTP/WS、鉴权、会话版本、单活跃搜索限制、通用文字查询、同帧序列化及最新帧发送均已接入。
这是独立开发联调服务；相似实物的持久身份、多用户同时识别、任意物品准确率与外网帧率仍未完成资格验证。
没有修改已有生产服务、业务数据库、身份链路、Offline V1 或 Provider A。

在本机 PowerShell 的项目目录执行：

```powershell
# 启动后台 API（自动将现有 VLX 桌面预览切换为单个后台 GPU 工作进程）
.\scripts\start_vlx_item_search_api.ps1
# 发布 HTTPS/WSS；Tailscale 需已登录且 Funnel 已启用
.\scripts\publish_vlx_item_search_funnel.ps1
# 关闭公网入口
.\scripts\publish_vlx_item_search_funnel.ps1 -Stop
# 停止 API 与 GPU 工作进程
.\scripts\start_vlx_item_search_api.ps1 -Stop
```

启动入口：`scripts/start_vlx_item_search_api.ps1`；后端程序：`scripts/run_vlx_item_search_api.py`。
本机接口监听 `http://127.0.0.1:8787`，由 Funnel 转发。
前端复用已有 Java 登录 Token，不再使用旧 frontend-token.txt。服务端密钥由 JAVA_FRONT_JWT_SECRET 环境变量提供，或从 config/vlx_item_search_api.yaml 中 java_front_jwt_secret_file 指定的仓库外文件读取。密钥缺失或无效时启动失败，不回退旧 Token。
前端在运行时传入登录 Token，不将实际 Token 或签名密钥编译进公开页面。
运行日志与当前进程指针在 `D:/project/RetailVisionDev/runtime/vlx-item-search-api/`。
外网入口验证记录：HTTPS health=200、未鉴权 catalog=401、已鉴权 catalog=200、localhost:5173 CORS 预检通过、WSS 收到真实小吉他框、清空新版本框为空。
