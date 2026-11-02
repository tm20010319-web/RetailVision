import { useEffect, useState } from "react";

export function CountUpValue({ delay = 0, format = String, maskedValue, prefix = "", start, suffix = "", value }) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!start || maskedValue || typeof value !== "number") {
      setDisplay(0);
      return undefined;
    }

    let frameId;
    let startAt;
    const timeoutId = window.setTimeout(() => {
      function update(timestamp) {
        if (!startAt) startAt = timestamp;
        const progress = Math.min((timestamp - startAt) / 860, 1);
        const eased = 1 - ((1 - progress) ** 3);
        setDisplay(Math.round(value * eased));
        if (progress < 1) frameId = window.requestAnimationFrame(update);
      }

      frameId = window.requestAnimationFrame(update);
    }, delay);

    return () => {
      window.clearTimeout(timeoutId);
      window.cancelAnimationFrame(frameId);
    };
  }, [delay, maskedValue, start, value]);

  if (maskedValue) return <span>{maskedValue}</span>;
  if (typeof value !== "number") return <span>{value}</span>;
  return <span>{prefix}{format(display)}{suffix}</span>;
}
