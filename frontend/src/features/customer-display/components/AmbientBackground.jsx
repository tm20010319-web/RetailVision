const PARTICLES = [
  [8, 18, 7, 0.2], [14, 72, 4, 2.4], [22, 43, 5, 4.7], [29, 83, 3, 1.6],
  [36, 14, 4, 5.4], [43, 63, 6, 3.2], [51, 28, 3, 6.1], [58, 78, 5, 0.9],
  [65, 48, 4, 2.8], [72, 19, 6, 4.1], [79, 68, 3, 5.8], [86, 37, 5, 1.3],
  [92, 82, 4, 3.7], [6, 89, 3, 6.7], [96, 10, 3, 2.1], [47, 92, 4, 4.9],
];

const CARD_PARTICLES = [
  [18, 74, -44, -72], [25, 22, -20, 58], [42, 12, 22, 72], [56, 86, 62, -45],
  [68, 36, -74, 26], [80, 70, -105, -34], [90, 47, -130, 45], [11, 46, 72, -34],
];

export function MainBackground() {
  return (
    <>
      <div className="soft-aurora soft-aurora--upper" />
      <div className="soft-aurora soft-aurora--lower" />
      <div className="ambient-horizon" />
      <div className="ambient-grain" />
      <div className="strands-texture" />
    </>
  );
}

export function FocusLightEffect({ active }) {
  return (
    <div className={`focus-light-effect${active ? " focus-light-effect--active" : ""}`} aria-hidden="true">
      <i className="focus-light-effect__ray focus-light-effect__ray--left" />
      <i className="focus-light-effect__ray focus-light-effect__ray--right" />
      <i className="focus-light-effect__core" />
    </div>
  );
}

export function ParticleLayer({ active }) {
  return (
    <>
      <div className="ambient-particles">
        {PARTICLES.map(([x, y, size, delay], index) => (
          <i
            key={`${x}-${y}`}
            style={{
              "--particle-x": `${x}%`,
              "--particle-y": `${y}%`,
              "--particle-size": `${size}px`,
              "--particle-delay": `${delay}s`,
              "--particle-shift": `${index % 2 === 0 ? -18 : 18}px`,
            }}
          />
        ))}
      </div>
      <div className={`card-particles${active ? " card-particles--active" : ""}`}>
        {CARD_PARTICLES.map(([x, y, tx, ty]) => (
          <i key={`${x}-${y}`} style={{ "--particle-x": `${x}%`, "--particle-y": `${y}%`, "--target-x": `${tx}px`, "--target-y": `${ty}px` }} />
        ))}
      </div>
    </>
  );
}

export function AmbientBackground({ active }) {
  return (
    <div className={`ambient-background${active ? " ambient-background--active" : ""}`} aria-hidden="true">
      <MainBackground />
      <FocusLightEffect active={active} />
      <ParticleLayer active={active} />
    </div>
  );
}
