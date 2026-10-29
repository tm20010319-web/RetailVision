export function IdleExperience({ hidden }) {
  return (
    <section className={`idle-experience${hidden ? " idle-experience--hidden" : ""}`} aria-hidden={hidden}>
      <div className="idle-orbit" aria-hidden="true"><i /><i /><i /></div>
      <p className="idle-kicker">每一次相遇，都值得被认真记住</p>
      <h1 className="idle-brand"><span>秒象</span>科技</h1>
      <div className="idle-rule" aria-hidden="true"><i /></div>
      <p className="idle-tagline">让科技，更懂每一次相遇</p>
      <p className="idle-support">智慧体验，正在发生</p>
    </section>
  );
}
