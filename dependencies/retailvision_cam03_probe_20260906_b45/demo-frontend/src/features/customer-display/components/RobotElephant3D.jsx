import { useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { MathUtils } from "three";

export const ROBOT_ELEPHANT_MODEL_PATH = "/customer-display/models/miaoxiang-robot-elephant.glb";

export function RobotElephant3D({ active, onReady, reducedMotion }) {
  const groupRef = useRef(null);
  const glowRef = useRef(null);
  const glowLightRef = useRef(null);
  const activeSinceRef = useRef(null);
  const { scene } = useGLTF(ROBOT_ELEPHANT_MODEL_PATH);
  const model = useMemo(() => scene.clone(true), [scene]);

  useEffect(() => {
    model.traverse((child) => {
      if (!child.isMesh) return;

      child.castShadow = false;
      child.receiveShadow = false;
      const hasMultipleMaterials = Array.isArray(child.material);
      const materials = hasMultipleMaterials ? child.material : [child.material];
      const tunedMaterials = materials.map((material) => {
        const tuned = material.clone();
        if ("metalness" in tuned) tuned.metalness = Math.max(tuned.metalness, 0.48);
        if ("roughness" in tuned) tuned.roughness = Math.min(tuned.roughness, 0.38);
        if ("envMapIntensity" in tuned) tuned.envMapIntensity = 1.15;
        return tuned;
      });
      child.material = hasMultipleMaterials ? tunedMaterials : tunedMaterials[0];
    });
    onReady();
  }, [model, onReady]);

  useEffect(() => {
    activeSinceRef.current = null;
  }, [active]);

  useFrame(({ clock }, delta) => {
    if (!groupRef.current || !active) return;

    if (activeSinceRef.current === null) activeSinceRef.current = clock.elapsedTime;
    const elapsed = clock.elapsedTime - activeSinceRef.current;
    const hover = reducedMotion ? 0 : Math.sin(elapsed * 1.55) * 0.018;
    const turn = reducedMotion ? 0 : Math.sin(elapsed * 0.78) * 0.045;
    const flightTilt = elapsed < 2.2 ? -0.13 * (1 - elapsed / 2.2) : elapsed > 10.9 ? 0.1 : 0;

    groupRef.current.position.y = MathUtils.damp(groupRef.current.position.y, hover, 5, delta);
    groupRef.current.rotation.y = MathUtils.damp(groupRef.current.rotation.y, turn, 4, delta);
    groupRef.current.rotation.z = MathUtils.damp(groupRef.current.rotation.z, flightTilt, 5, delta);

    const pulse = reducedMotion ? 0.72 : 0.56 + Math.sin(elapsed * 2.7) * 0.2;
    if (glowRef.current) glowRef.current.material.opacity = pulse;
    if (glowLightRef.current) glowLightRef.current.intensity = 2.4 + pulse * 2.2;
  });

  return (
    <group ref={groupRef} scale={3.75}>
      <primitive object={model} position={[0.206, -0.82, 0]} />
      <group position={[0, -0.04, 0.135]}>
        <mesh ref={glowRef}>
          <sphereGeometry args={[0.014, 20, 20]} />
          <meshBasicMaterial color="#57ddff" opacity={0.76} transparent toneMapped={false} />
        </mesh>
        <pointLight ref={glowLightRef} color="#7de5ff" distance={1.7} intensity={3.4} />
      </group>
    </group>
  );
}

useGLTF.preload(ROBOT_ELEPHANT_MODEL_PATH);
