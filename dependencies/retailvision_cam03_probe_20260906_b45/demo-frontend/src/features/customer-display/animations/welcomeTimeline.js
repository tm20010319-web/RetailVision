import { gsap } from "gsap";

export function createWelcomeTimeline({
  brand,
  headline,
  robot,
  onComplete,
  onCountUpStart,
  reducedMotion,
  root,
  valueCard,
}) {
  const title = headline.querySelector(".member-welcome-headline__title");
  const subtitle = headline.querySelector(".member-welcome-headline__subtitle");
  const glow = headline.querySelector(".member-welcome-headline__glow");
  const metrics = valueCard.querySelectorAll(".member-metric");
  const footer = valueCard.querySelector(".member-value-card__footer");
  const serviceCopy = root.querySelector(".member-service-copy");
  const cardEdge = valueCard.querySelector(".member-value-card__edge");
  const robotEnterX = -Math.round(window.innerWidth * 0.85);
  const robotExitX = Math.round(window.innerWidth * 0.45);

  gsap.set(root, { autoAlpha: 0 });
  gsap.set(robot, { autoAlpha: 0, x: robotEnterX, y: 460, rotate: -8, scale: 0.72 });
  gsap.set(headline, { autoAlpha: 0 });
  gsap.set([title, subtitle], { autoAlpha: 0, y: 22 });
  gsap.set(glow, { autoAlpha: 0, scaleX: 0.2 });
  gsap.set(valueCard, { autoAlpha: 0, xPercent: -50, y: 28, scale: 0.94, filter: "blur(16px)" });
  gsap.set(metrics, { autoAlpha: 0, y: 16 });
  gsap.set(footer, { autoAlpha: 0, y: 14 });
  gsap.set(serviceCopy, { autoAlpha: 0, y: 12 });
  gsap.set(brand, { autoAlpha: 0, y: 16 });
  gsap.set(cardEdge, { xPercent: -420 });

  if (reducedMotion) {
    gsap.set(root, { autoAlpha: 1 });
    gsap.set(robot, { autoAlpha: 1, x: 0, y: 0, rotate: 0, scale: 1 });
    gsap.set([headline, title, subtitle, valueCard, metrics, footer, serviceCopy, brand], { autoAlpha: 1, y: 0, scale: 1 });
    onCountUpStart();
    return gsap.timeline({ onComplete }).to({}, { duration: 3.2 });
  }

  return gsap.timeline({ onComplete })
    .to(root, { autoAlpha: 1, duration: 0.35, ease: "power1.out" }, 0)
    .to(robot, { autoAlpha: 1, x: 0, y: 34, rotate: 3, scale: 1, duration: 1.25, ease: "power3.out" }, 0.82)
    .to(robot, { y: 0, rotate: 0, duration: 0.58, ease: "sine.inOut" }, 2.02)
    .to(headline, { autoAlpha: 1, duration: 0.1 }, 1.75)
    .to(title, { autoAlpha: 1, y: 0, duration: 0.68, ease: "power3.out" }, 2)
    .to(subtitle, { autoAlpha: 1, y: 0, duration: 0.56, ease: "power2.out" }, 2.62)
    .to(glow, { autoAlpha: 1, scaleX: 1, duration: 0.8, ease: "power2.out" }, 2.8)
    .to(valueCard, { autoAlpha: 1, y: 0, scale: 1, filter: "blur(0px)", duration: 0.9, ease: "power3.out" }, 3.35)
    .to(brand, { autoAlpha: 1, y: 0, duration: 0.42, ease: "power2.out" }, 3.55)
    .to(metrics, { autoAlpha: 1, y: 0, duration: 0.42, stagger: 0.12, ease: "power2.out" }, 3.8)
    .call(onCountUpStart, [], 3.98)
    .to(footer, { autoAlpha: 1, y: 0, duration: 0.58, ease: "power2.out" }, 6.12)
    .to(serviceCopy, { autoAlpha: 1, y: 0, duration: 0.58, ease: "power2.out" }, 6.35)
    .to(cardEdge, { xPercent: 1180, duration: 0.95, ease: "power2.inOut" }, 5.8)
    .to(robot, { y: -14, duration: 1.2, ease: "sine.inOut", repeat: 2, yoyo: true }, 7.7)
    .to([valueCard, serviceCopy, headline], { autoAlpha: 0, y: -14, duration: 0.58, stagger: 0.06, ease: "power2.in" }, 10.42)
    .to(robot, { x: robotExitX, y: -520, rotate: 9, scale: 0.86, autoAlpha: 0, duration: 1.08, ease: "power2.in" }, 11.2)
    .to(root, { autoAlpha: 0, duration: 0.32, ease: "power1.in" }, 12.18)
    .to({}, { duration: 0.01 }, 12.49);
}
