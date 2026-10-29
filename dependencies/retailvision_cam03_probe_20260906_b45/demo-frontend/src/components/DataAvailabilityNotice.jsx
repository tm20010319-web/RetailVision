import { CircleAlert, LoaderCircle, RefreshCw, Unplug } from "lucide-react";

const icons = {
  error: CircleAlert,
  loading: LoaderCircle,
  unavailable: Unplug,
};

export function DataAvailabilityNotice({ onRetry, source, title }) {
  const kind = source?.kind || "unavailable";
  const Icon = icons[kind] || Unplug;
  const message = source?.message || "当前接口尚未提供此数据。";

  return (
    <div className={"data-availability data-availability--" + kind} role={kind === "error" ? "alert" : "status"}>
      <Icon aria-hidden="true" className={kind === "loading" ? "is-spinning" : ""} size={19} strokeWidth={1.8} />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {kind === "error" && onRetry && (
        <button className="secondary-action" onClick={onRetry} type="button">
          <RefreshCw aria-hidden="true" size={14} strokeWidth={1.8} />
          重试
        </button>
      )}
    </div>
  );
}
