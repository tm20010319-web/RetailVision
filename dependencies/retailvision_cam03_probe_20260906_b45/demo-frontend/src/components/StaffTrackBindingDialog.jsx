import { useState } from "react";
import { Link2, ShieldAlert, X } from "lucide-react";
import { useDialogFocus } from "../hooks/useDialogFocus";

export function StaffTrackBindingDialog({ onClose, onConfirm, staff, task }) {
  const [staffId, setStaffId] = useState(staff.find((entry) => entry.staffId)?.staffId || "");
  const dialogRef = useDialogFocus(onClose);

  return (
    <div className="binding-dialog-backdrop" role="presentation">
      <section ref={dialogRef} aria-labelledby="binding-title" aria-modal="true" className="binding-dialog" role="dialog">
        <header>
          <div>
            <span className="panel-kicker">人工绑定 · DEMO DATA</span>
            <h2 id="binding-title">绑定当前 Staff Track</h2>
          </div>
          <button aria-label="关闭人工绑定" className="icon-button" onClick={onClose} type="button">
            <X aria-hidden="true" size={18} />
          </button>
        </header>
        <div className="binding-dialog__body">
          <div className="binding-warning">
            <ShieldAlert aria-hidden="true" size={17} />
            阶段 1 只演示界面，不写入数据库，也不改变员工统计。
          </div>
          <dl className="binding-context">
            <div><dt>store_id</dt><dd>STORE-SH-001</dd></div>
            <div><dt>camera_id</dt><dd>{task.camera}</dd></div>
            <div><dt>runtime_session_id</dt><dd>front-runtime-20260730-18</dd></div>
            <div><dt>track_token</dt><dd>{task.staffTrack}</dd></div>
            <div><dt>binding_source</dt><dd>manual</dd></div>
            <div><dt>expires_at</dt><dd>Track 结束时失效</dd></div>
          </dl>
          <label className="binding-select">
            <span>绑定员工</span>
            <select onChange={(event) => setStaffId(event.target.value)} value={staffId}>
              {staff.filter((entry) => entry.staffId).map((entry) => (
                <option key={entry.staffId} value={entry.staffId}>{entry.staffName} · {entry.staffId}</option>
              ))}
            </select>
          </label>
        </div>
        <footer>
          <button className="secondary-button" onClick={onClose} type="button">取消</button>
          <button className="primary-button" disabled={!staffId} onClick={() => onConfirm(task, staffId)} type="button">
            <Link2 aria-hidden="true" size={16} />
            确认演示绑定
          </button>
        </footer>
      </section>
    </div>
  );
}
