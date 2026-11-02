import { forwardRef } from "react";
import { CountUpValue } from "./CountUpValue";
import { getMemberMetrics } from "../data/memberDisplayData";

export const MemberValueCard = forwardRef(function MemberValueCard({ active, data, metricsStarted, privacyMode }, ref) {
  const metrics = getMemberMetrics(data, privacyMode);

  return (
    <section className="member-value-card" ref={ref} aria-label="会员荣耀时刻">
      <div className="member-value-card__edge" aria-hidden="true" />
      <header className="member-value-card__header">
        <p>您的会员荣耀时刻</p>
        <span>MEMBER MOMENTS</span>
      </header>
      <div className="member-value-card__metrics">
        {metrics.map((metric, index) => (
          <article className="member-metric" key={metric.label}>
            <p className="member-metric__value">
              <CountUpValue
                delay={index * 400}
                format={metric.format}
                maskedValue={metric.maskedValue}
                prefix={metric.prefix}
                start={metricsStarted}
                suffix={metric.suffix}
                value={metric.value}
              />
            </p>
            <strong>{metric.label}</strong>
            <small>{metric.caption}</small>
          </article>
        ))}
      </div>
      <footer className={`member-value-card__footer${active ? " member-value-card__footer--visible" : ""}`}>
        <p>{data.emotionalCopy}</p>
        <span>{privacyMode === "emotional_only" ? "感谢一路相伴" : "您已成为我们最珍视的会员之一"}</span>
      </footer>
    </section>
  );
});
