import { forwardRef } from "react";
import { getMemberIdentity } from "../data/memberDisplayData";

export const WelcomeHeadline = forwardRef(function WelcomeHeadline({ data, privacyMode }, ref) {
  const identity = getMemberIdentity(data, privacyMode);
  return (
    <div className="member-welcome-headline" ref={ref}>
      <p className="member-welcome-headline__title" data-split-text>{identity.greeting}</p>
      <p className="member-welcome-headline__subtitle">{identity.subtitle}</p>
      <span className="member-welcome-headline__glow" aria-hidden="true" />
    </div>
  );
});
