import { useState } from "react";
import { Play, ShieldCheck, Video, VideoOff } from "lucide-react";
import { DEMO_CAMERA_IMAGE } from "../data/operationsDemo";

function normalizeEvidence(evidence) {
  if (typeof evidence === "string") {
    const normalized = evidence.trim().toLowerCase();
    const available = normalized === "已就绪" || normalized === "ready" || normalized === "available";
    return { available, source: "demo", status: evidence };
  }
  return {
    available: Boolean(evidence?.available),
    id: evidence?.id || null,
    source: evidence?.source || "live",
    status: evidence?.status || "无证据",
    videoUrl: evidence?.videoUrl || null,
  };
}

export function EvidenceClipPlayer({ evidence, label }) {
  const [isPreviewing, setIsPreviewing] = useState(false);
  const item = normalizeEvidence(evidence);
  const isDemo = item.source === "demo";
  const canPreview = item.available && (isDemo || Boolean(item.videoUrl));
  const playLabel = canPreview
    ? (isDemo ? "查看 " + label + " 的演示证据" : "加载 " + label + " 的证据视频")
    : "证据不可用";

  return (
    <figure className="evidence-player">
      <div className="evidence-player__frame">
        {isDemo && item.available ? <>
          <img alt={label + " 的演示证据画面"} src={DEMO_CAMERA_IMAGE} />
          <div className="evidence-player__overlay"><Video aria-hidden="true" size={18} strokeWidth={1.8} /><span>DEMO 证据片段</span></div>
        </> : isPreviewing && canPreview ? (
          <video aria-label={label + " 的证据视频"} controls preload="none" src={item.videoUrl} />
        ) : (
          <div className="evidence-player__unavailable"><VideoOff aria-hidden="true" size={27} strokeWidth={1.5} /><span>{item.available ? "证据已就绪，等待加载视频" : "证据视频尚不可用"}</span></div>
        )}
        <button
          aria-label={playLabel}
          aria-pressed={isPreviewing}
          className="evidence-player__play"
          disabled={!canPreview}
          onClick={() => setIsPreviewing((current) => !current)}
          title={canPreview ? (isDemo ? "查看演示证据" : "加载证据视频") : "当前没有可播放的证据片段"}
          type="button"
        >
          <Play aria-hidden="true" fill="currentColor" size={17} />
        </button>
        {isDemo && isPreviewing && <div className="evidence-player__preview" role="status">演示证据预览已打开，阶段 1 不播放真实录像。</div>}
      </div>
      <figcaption>
        <span><ShieldCheck aria-hidden="true" size={14} />{item.status}</span>
        <small>{isDemo ? "阶段 1 为静态预览，不请求或播放真实录像。" : (item.available ? "点击播放按钮后才请求证据视频。" : "Evidence 状态由只读接口返回。")}</small>
      </figcaption>
    </figure>
  );
}
