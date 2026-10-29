import { Environment, Lightformer } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { forwardRef, Suspense, useCallback, useState } from "react";
import { ACESFilmicToneMapping, SRGBColorSpace } from "three";
import { RobotElephant3D } from "./RobotElephant3D";

const TRAIL_PARTICLES = [0, 1, 2, 3, 4, 5];

export const RobotElephantScene = forwardRef(function RobotElephantScene({ active, reducedMotion }, ref) {
  const [modelReady, setModelReady] = useState(false);
  const handleModelReady = useCallback(() => setModelReady(true), []);

  return (
    <div className="robot-elephant-stage" data-model-ready={modelReady} ref={ref} aria-hidden="true">
      <div className="robot-elephant-aura" />
      <div className="robot-flight-trail">
        {TRAIL_PARTICLES.map((particle) => <i key={particle} style={{ "--trail-index": particle }} />)}
      </div>
      <Canvas
        camera={{ far: 12, fov: 27, near: 0.1, position: [0, 0.02, 2.42] }}
        dpr={[1, 1.25]}
        frameloop={active && !reducedMotion ? "always" : "demand"}
        gl={{ alpha: true, antialias: true, powerPreference: "high-performance" }}
        onCreated={({ gl }) => {
          gl.outputColorSpace = SRGBColorSpace;
          gl.toneMapping = ACESFilmicToneMapping;
          gl.toneMappingExposure = 0.76;
          gl.setClearColor(0x000000, 0);
        }}
      >
        <ambientLight color="#dceff4" intensity={0.72} />
        <directionalLight color="#f4fbff" intensity={1.7} position={[2.4, 3.4, 4.2]} />
        <directionalLight color="#80ddf4" intensity={1.35} position={[-3.2, 1.1, 1.8]} />
        <directionalLight color="#e8c889" intensity={0.42} position={[2.8, 0.4, -2.2]} />
        <Suspense fallback={null}>
          <RobotElephant3D active={active} onReady={handleModelReady} reducedMotion={reducedMotion} />
          <Environment resolution={64}>
            <Lightformer color="#eafaff" intensity={1.8} position={[0, 3, -3]} rotation-x={Math.PI / 2} scale={[7, 7, 1]} />
            <Lightformer color="#79dff6" intensity={1.45} position={[-3, 1, 2]} rotation-y={Math.PI / 2} scale={[3, 3, 1]} />
            <Lightformer color="#e8c889" intensity={0.72} position={[3, 0, 1]} rotation-y={-Math.PI / 2} scale={[2, 2, 1]} />
          </Environment>
        </Suspense>
      </Canvas>
    </div>
  );
});
