import { useRef } from "react";
import {
  BoxSelect,
  Code2,
  Crosshair,
  Expand,
  MapPinned,
  Radio,
  RotateCcw,
  Route,
  ScanLine,
  VideoOff,
} from "lucide-react";
import { DEMO_CAMERA_IMAGE } from "../../data/operationsDemo";
import { DataAvailabilityNotice } from "../DataAvailabilityNotice";
import { DataSourceBadge } from "../DataSourceBadge";

const overlayControls = [
  { id: "boxes", label: "检测框", icon: BoxSelect },
  { id: "zones", label: "区域", icon: MapPinned },
  { id: "paths", label: "轨迹", icon: Route },
  { id: "world", label: "世界坐标", icon: Crosshair },
  { id: "debug", label: "调试数据", icon: Code2 },
];

function OverlayControl({ control, disabled, enabled, onToggle }) {
  const Icon = control.icon;
  return (
    <button aria-pressed={enabled} className={"camera-tool camera-tool--toggle" + (enabled ? " is-active" : "")} disabled={disabled} onClick={() => onToggle(control.id)} title={disabled ? "当前接口未提供实时 Overlay" : undefined} type="button">
      <Icon aria-hidden="true" size={14} strokeWidth={1.8} />{control.label}
    </button>
  );
}

export function LiveCameraPanel({ cameraMode, onCameraModeChange, onOverlayToggle, overlays, source = { kind: "demo" } }) {
  const stageRef = useRef(null);
  const isDemo = source.kind === "demo";

  function openFullscreen() {
    const request = stageRef.current?.requestFullscreen?.();
    if (request) request.catch(() => undefined);
  }

  return (
    <section className="panel live-camera-panel" aria-labelledby="live-camera-title">
      <div className="panel-heading panel-heading--camera">
        <div>
          <div className="eyebrow"><span className="live-signal"><Radio aria-hidden="true" size={12} />{isDemo ? "实时" : "视频接口"}</span><DataSourceBadge source={source} /></div>
          <h2 id="live-camera-title">前厅实时摄像头</h2>
          <p>{isDemo ? "front-cam-01 · 1080p · 当前运行会话 front-runtime-20260730-18" : "当前后端没有直播帧、运行会话或 Overlay 查询接口。"}</p>
        </div>
        <div className="camera-toolbar" aria-label="摄像头工具">
          <label className="sr-only" htmlFor="camera-select">选择摄像头</label>
          <select disabled={!isDemo} id="camera-select" defaultValue="front-cam-01"><option value="front-cam-01">前厅 01</option><option value="front-cam-02">前厅 02</option></select>
          <div className="segmented-control" aria-label="画面模式">
            <button aria-pressed={cameraMode === "live"} className={cameraMode === "live" ? "is-active" : ""} disabled={!isDemo} onClick={() => onCameraModeChange("live")} type="button"><Radio aria-hidden="true" size={14} />实时</button>
            <button aria-pressed={cameraMode === "replay"} className={cameraMode === "replay" ? "is-active" : ""} disabled={!isDemo} onClick={() => onCameraModeChange("replay")} type="button"><RotateCcw aria-hidden="true" size={14} />回放</button>
          </div>
          {overlayControls.map((control) => <OverlayControl control={control} disabled={!isDemo} enabled={Boolean(overlays[control.id])} key={control.id} onToggle={onOverlayToggle} />)}
          <button className="camera-tool" disabled={!isDemo} onClick={openFullscreen} title={isDemo ? "全屏查看" : "当前接口未提供实时画面"} type="button"><Expand aria-hidden="true" size={15} strokeWidth={1.8} />全屏</button>
        </div>
      </div>

      {!isDemo ? (
        <div className="camera-stage camera-stage--unavailable" ref={stageRef}>
          <VideoOff aria-hidden="true" className="camera-stage__unavailable-icon" size={34} strokeWidth={1.5} />
          <DataAvailabilityNotice source={source} title="实时画面暂不可用" />
          <div className="camera-stage__status"><ScanLine aria-hidden="true" size={15} strokeWidth={1.8} /><span>等待只读 live-view 接口</span><strong>Overlay 不可用</strong></div>
        </div>
      ) : (
        <div className="camera-stage" ref={stageRef}>
          <img alt="受控演示摄像头画面，展示店内人员识别与迎宾区域。" className="camera-stage__image" src={DEMO_CAMERA_IMAGE} />
          <div className="camera-stage__shade" aria-hidden="true" />
          {overlays.zones && <><div className="camera-zone camera-zone--entry"><span>entry_trigger</span></div><div className="camera-zone camera-zone--greeting"><span>greeting_area</span></div></>}
          {overlays.paths && <><div className="camera-path camera-path--customer" /><div className="camera-path camera-path--staff" /></>}
          {overlays.boxes && <><div className="person-box person-box--customer"><span className="person-box__label">customer · track-42</span></div><div className="person-box person-box--staff"><span className="person-box__label">staff · track-7 · vest 0.93</span></div><div className="distance-line"><span>2.4 m</span></div></>}
          {overlays.world && <><div className="world-coordinate world-coordinate--customer">foot · x 2.41 · y 4.18</div><div className="world-coordinate world-coordinate--staff">foot · x 1.15 · y 4.51</div></>}
          {overlays.debug && <div className="camera-debug"><div><span>role</span><strong>customer / staff</strong></div><div><span>greeting</span><strong>WAITING_FOR_GREETING</strong></div>{overlays.world && <div><span>world</span><strong>x 2.41 · y 4.18</strong></div>}<div><span>latency</span><strong>31 ms</strong></div></div>}
          <div className="camera-stage__status"><ScanLine aria-hidden="true" size={15} strokeWidth={1.8} /><span>{cameraMode === "live" ? "实时分析中" : "回放演示"}</span><strong>等待迎宾</strong></div>
        </div>
      )}
      <div className="camera-legend" aria-label="摄像头图层图例">
        {isDemo ? <><span><i className="legend-marker legend-marker--customer" />顾客 track-42</span><span><i className="legend-marker legend-marker--staff" />员工 role / 蓝马甲</span><span><i className="legend-marker legend-marker--zone" />迎宾区域</span><span>演示画面来自受控行为 Runtime 输出</span></> : <span>画面、检测框、区域、轨迹和世界坐标均等待后端只读接口。</span>}
      </div>
    </section>
  );
}
