import { forwardRef } from "react";

export const WelcomeCopy = forwardRef(function WelcomeCopy(_, ref) {
  return (
    <div className="welcome-copy" ref={ref}>
      <p className="welcome-copy__line welcome-copy__line--lead">欢迎回来，尊贵会员</p>
      <p className="welcome-copy__line welcome-copy__line--meeting">很高兴，再次与您相遇</p>
      <div className="welcome-copy__line welcome-copy__line--promise">
        <span>您的专属智能体验</span>
        <strong>已为您悄然开启</strong>
      </div>
      <div className="welcome-copy__line welcome-copy__line--signature">
        <i aria-hidden="true" />
        <span>让科技，更懂每一次相遇</span>
      </div>
    </div>
  );
});
