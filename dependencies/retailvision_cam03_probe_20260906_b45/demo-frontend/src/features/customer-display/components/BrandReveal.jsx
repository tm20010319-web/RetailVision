import { forwardRef } from "react";

export const BrandReveal = forwardRef(function BrandReveal(_, ref) {
  return (
    <div className="provider-signature" ref={ref}>
      <span>POWERED BY</span>
      <strong>秒象科技</strong>
      <i aria-hidden="true" />
    </div>
  );
});
