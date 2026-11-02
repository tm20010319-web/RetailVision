import { useLayoutEffect, useRef } from "react";
import { useState } from "react";
import { BrandReveal } from "./BrandReveal";
import { MemberValueCard } from "./MemberValueCard";
import { RobotElephantScene } from "./RobotElephantScene";
import { WelcomeHeadline } from "./WelcomeHeadline";
import { createWelcomeTimeline } from "../animations/welcomeTimeline";

export function MemberWelcomeExperience({ active, data, onComplete, privacyMode, reducedMotion }) {
  const rootRef = useRef(null);
  const stageRef = useRef(null);
  const robotRef = useRef(null);
  const brandRef = useRef(null);
  const headlineRef = useRef(null);
  const valueCardRef = useRef(null);
  const [metricsStarted, setMetricsStarted] = useState(false);

  useLayoutEffect(() => {
    if (!active) {
      setMetricsStarted(false);
      return undefined;
    }

    const timeline = createWelcomeTimeline({
      root: stageRef.current,
      robot: robotRef.current,
      brand: brandRef.current,
      headline: headlineRef.current,
      valueCard: valueCardRef.current,
      onCountUpStart: () => setMetricsStarted(true),
      onComplete,
      reducedMotion,
    });

    return () => timeline.kill();
  }, [active, onComplete, reducedMotion]);

  return (
    <section className={`welcome-experience${active ? " welcome-experience--active" : ""}`} ref={rootRef} aria-hidden={!active}>
      <div className="welcome-experience__stage" ref={stageRef}>
        <div className="welcome-light-path" aria-hidden="true"><i /></div>
        <RobotElephantScene active={active} reducedMotion={reducedMotion} ref={robotRef} />
        <WelcomeHeadline data={data} privacyMode={privacyMode} ref={headlineRef} />
        <MemberValueCard active={active} data={data} metricsStarted={metricsStarted} privacyMode={privacyMode} ref={valueCardRef} />
        <p className="member-service-copy">{data.serviceCopy}</p>
        <BrandReveal ref={brandRef} />
      </div>
    </section>
  );
}
