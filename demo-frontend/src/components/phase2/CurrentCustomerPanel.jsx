import {
  BadgeCheck,
  BrainCircuit,
  CircleUserRound,
  Fingerprint,
  ScanFace,
  UserRound,
} from "lucide-react";
import { DataAvailabilityNotice } from "../DataAvailabilityNotice";
import { DataSourceBadge } from "../DataSourceBadge";

function value(value, suffix = "") {
  return value === null || value === undefined || value === "" ? "未提供" : String(value) + suffix;
}

function CustomerFacts({ customer }) {
  return (
    <dl className="customer-facts">
      <div><dt>会员等级</dt><dd><span className="tier-badge">{value(customer.tier)}</span></dd></div>
      <div><dt>积分</dt><dd>{value(customer.points)}</dd></div>
      <div><dt>到店次数</dt><dd>{value(customer.visits, customer.visits === "未提供" ? "" : " 次")}</dd></div>
      <div><dt>最近到店</dt><dd>{value(customer.lastVisit)}</dd></div>
      <div><dt>当前 Visit</dt><dd className="mono">{value(customer.visitId)}</dd></div>
      <div><dt>身份来源</dt><dd>{value(customer.source)}</dd></div>
      <div><dt>识别置信度</dt><dd>{value(customer.confidence)}</dd></div>
    </dl>
  );
}

export function CurrentCustomerPanel({ customer, onPreviewChange = () => undefined, source = { kind: "demo" } }) {
  const isDemo = source.kind === "demo";
  const isAnonymous = customer?.kind === "anonymous";

  return (
    <aside className="panel current-customer-panel" aria-labelledby="current-customer-title">
      <div className="panel-heading panel-heading--compact">
        <div>
          <div className="eyebrow">
            <span className="panel-kicker">{isDemo ? "当前顾客" : "最近识别"}</span>
            <DataSourceBadge source={source} />
          </div>
          <h2 id="current-customer-title">会员焦点</h2>
        </div>
        {isDemo && (
          <div className="customer-preview-switch" aria-label="演示身份状态">
            <button aria-pressed={!isAnonymous} className={!isAnonymous ? "is-active" : ""} onClick={() => onPreviewChange("member")} type="button">会员</button>
            <button aria-pressed={isAnonymous} className={isAnonymous ? "is-active" : ""} onClick={() => onPreviewChange("anonymous")} type="button">匿名</button>
          </div>
        )}
      </div>

      {!customer ? (
        <DataAvailabilityNotice source={source} title="暂无可用会员焦点" />
      ) : isAnonymous ? (
        <div className="anonymous-customer">
          <div className="customer-avatar customer-avatar--anonymous"><UserRound aria-hidden="true" size={30} strokeWidth={1.5} /></div>
          <strong>匿名顾客</strong>
          <p>未显示任何伪造会员资料。</p>
          <dl className="anonymous-customer__facts">
            <div><dt>当前 Track</dt><dd className="mono">{customer.track}</dd></div>
            <div><dt>行为状态</dt><dd>{customer.behavior}</dd></div>
            <div><dt>身份状态</dt><dd>{customer.state}</dd></div>
          </dl>
          <div className="anonymous-note"><Fingerprint aria-hidden="true" size={16} />等待可信身份源返回后再展示会员信息。</div>
        </div>
      ) : (
        <>
          <div className="customer-identity">
            <div className="customer-avatar">{customer.name?.slice(0, 1) || "会"}</div>
            <div>
              <div className="customer-identity__name">
                <strong>{customer.name}</strong>
                <BadgeCheck aria-label="会员已识别" size={17} strokeWidth={1.8} />
              </div>
              <span className="mono">{customer.id}</span>
              <p><ScanFace aria-hidden="true" size={14} />{customer.track}</p>
            </div>
          </div>
          <CustomerFacts customer={customer} />
          <section className="customer-section" aria-labelledby="customer-tags-title">
            <h3 id="customer-tags-title">历史偏好与标签</h3>
            {customer.tags?.length ? <div className="tag-list">{customer.tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : <p className="data-field-note">当前画像未提供会员标签。</p>}
          </section>
          <section className="service-recommendation" aria-labelledby="service-advice-title">
            <div><BrainCircuit aria-hidden="true" size={16} strokeWidth={1.8} /><h3 id="service-advice-title">AI 服务建议</h3></div>
            <p>{customer.recommendation}</p>
          </section>
        </>
      )}

      {customer && <div className="customer-panel__footer"><CircleUserRound aria-hidden="true" size={14} />{isDemo ? "当前行为：" + customer.behavior : "仅表示最近识别记录；当前在店与实时行为接口未提供。"}</div>}
    </aside>
  );
}
