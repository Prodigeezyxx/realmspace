"use client";

import { Environment, Grid, Line, OrbitControls, Text } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

import { getEventContext } from "@/lib/event-context";
import { peopleTracks, positionsAt } from "@/lib/mock/people";
import { session, zones, type Zone } from "@/lib/mock/session";
import { useActiveSession } from "@/lib/session/store";
import {
  resolveTwinSurfaces,
  type TwinSurface,
} from "@/lib/twin/touchpoint-layout";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";

const DEFAULT_W = session.boothSize.width;
const DEFAULT_D = session.boothSize.depth;

function toWorld(
  nx: number,
  ny: number,
  w: number,
  d: number
): [number, number, number] {
  return [(nx - 0.5) * w, 0, (ny - 0.5) * d];
}

export function TwinScene({
  time,
  showHeatmap,
  selectedPerson,
  liveAvatars,
  liveHeatmap,
  liveMode = false,
}: {
  time: number;
  showHeatmap: boolean;
  selectedPerson?: string | null;
  liveAvatars?: TwinAvatarDelta[];
  liveHeatmap?: HeatmapOutput | null;
  liveMode?: boolean;
}) {
  const activeSession = useActiveSession();
  const ctx = getEventContext();
  const W = ctx.boothSize.width || DEFAULT_W;
  const D = ctx.boothSize.depth || DEFAULT_D;
  const sceneSurfaces = useMemo(
    () =>
      resolveTwinSurfaces(
        !!activeSession.isDemo,
        activeSession.touchpoints,
        ctx.zones
      ),
    [activeSession.isDemo, activeSession.touchpoints, ctx.zones]
  );
  const sceneZones: Zone[] =
    ctx.zones.length > 0
      ? ctx.zones
          .filter((z) => z.polygon?.length)
          .map((z) => ({
            id: z.id,
            name: z.name,
            type: "entry" as Zone["type"],
            polygon: z.polygon!,
            color: z.color ?? "#3e83f7",
          }))
      : zones;

  return (
    <Canvas
      shadows
      camera={{ position: [W * 0.9, W * 0.85, D * 1.4], fov: 38 }}
      gl={{ antialias: true }}
      style={{ background: "transparent" }}
    >
      <color attach="background" args={["#06080b"]} />
      <fog attach="fog" args={["#06080b", 18, 45]} />

      <ambientLight intensity={0.35} />
      <directionalLight
        position={[W, 8, D]}
        intensity={1.1}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
      />
      <pointLight position={[0, 6, 0]} intensity={0.4} color="#3e83f7" />
      <pointLight position={[-W / 2, 4, -D / 2]} intensity={0.3} color="#00d4ff" />

      <Booth showHeatmap={showHeatmap} w={W} d={D} liveHeatmap={liveHeatmap} />
      <Zones zoneList={sceneZones} w={W} d={D} />
      <Surfaces items={sceneSurfaces} w={W} d={D} />
      {liveMode ? (
        <LivePeople avatars={liveAvatars ?? []} />
      ) : (
        <People time={time} selectedPerson={selectedPerson} w={W} d={D} />
      )}
      <Cameras w={W} d={D} />

      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        minDistance={8}
        maxDistance={28}
        minPolarAngle={Math.PI / 8}
        maxPolarAngle={Math.PI / 2.05}
        target={[0, 0.5, 0]}
      />

      <Environment preset="night" />
    </Canvas>
  );
}

function Booth({
  showHeatmap,
  w,
  d,
  liveHeatmap,
}: {
  showHeatmap: boolean;
  w: number;
  d: number;
  liveHeatmap?: HeatmapOutput | null;
}) {
  return (
    <group>
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]}>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color="#0c0f14" metalness={0.4} roughness={0.6} />
      </mesh>

      <Grid
        args={[w, d]}
        cellSize={0.5}
        cellThickness={0.4}
        cellColor="#1c2330"
        sectionSize={2}
        sectionThickness={0.8}
        sectionColor="#243246"
        fadeDistance={30}
        position={[0, 0.001, 0]}
        infiniteGrid={false}
      />

      {showHeatmap && <HeatmapOverlay w={w} d={d} liveHeatmap={liveHeatmap} />}

      <mesh position={[0, 1.4, -d / 2]} castShadow>
        <boxGeometry args={[w, 2.8, 0.06]} />
        <meshStandardMaterial color="#10141a" metalness={0.5} roughness={0.7} />
      </mesh>
      <mesh position={[-w / 2, 1.4, 0]} castShadow>
        <boxGeometry args={[0.06, 2.8, d]} />
        <meshStandardMaterial color="#10141a" metalness={0.5} roughness={0.7} />
      </mesh>
      <mesh position={[w / 2, 1.4, 0]} castShadow>
        <boxGeometry args={[0.06, 2.8, d]} />
        <meshStandardMaterial color="#10141a" metalness={0.5} roughness={0.7} />
      </mesh>

      <mesh position={[-w / 2 + 1, 2.5, 0]}>
        <boxGeometry args={[0.2, 0.05, d]} />
        <meshStandardMaterial
          color="#3e83f7"
          emissive="#3e83f7"
          emissiveIntensity={0.4}
        />
      </mesh>
    </group>
  );
}

function HeatmapOverlay({
  w,
  d,
  liveHeatmap,
}: {
  w: number;
  d: number;
  liveHeatmap?: HeatmapOutput | null;
}) {
  const COLS = liveHeatmap?.width ?? 28;
  const ROWS = liveHeatmap?.height ?? 16;
  const data = useMemo(() => {
    if (liveHeatmap?.grid) {
      const heat = new Float32Array(COLS * ROWS);
      liveHeatmap.grid.forEach((row, y) => {
        row.forEach((v, x) => {
          heat[y * COLS + x] = v;
        });
      });
      return heat;
    }
    const heat = new Float32Array(COLS * ROWS);
    peopleTracks.forEach((p) => {
      p.waypoints.forEach(([x, y, t], i) => {
        if (i === 0) return;
        const dwell = Math.min(60, t - p.waypoints[i - 1][2]);
        const cx = Math.floor(x * (COLS - 1));
        const cy = Math.floor(y * (ROWS - 1));
        heat[cy * COLS + cx] += dwell;
      });
    });
    const max = Math.max(0.0001, ...heat);
    for (let i = 0; i < heat.length; i++) heat[i] /= max;
    return heat;
  }, [liveHeatmap, COLS, ROWS]);

  return (
    <group position={[0, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      {Array.from({ length: ROWS }).flatMap((_, y) =>
        Array.from({ length: COLS }).map((_, x) => {
          const v = data[y * COLS + x];
          if (v < 0.05) return null;
          const wx = (x / (COLS - 1) - 0.5) * w;
          const wy = (y / (ROWS - 1) - 0.5) * d;
          const color =
            v > 0.7
              ? "#ff453a"
              : v > 0.4
                ? "#ffd60a"
                : v > 0.2
                  ? "#bf5af2"
                  : "#3e83f7";
          return (
            <mesh key={`${x}-${y}`} position={[wx, wy, 0]}>
              <circleGeometry args={[0.18 + v * 0.7, 16]} />
              <meshBasicMaterial
                color={color}
                transparent
                opacity={0.12 + v * 0.45}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
              />
            </mesh>
          );
        })
      )}
    </group>
  );
}

function Zones({
  zoneList,
  w,
  d,
}: {
  zoneList: Zone[];
  w: number;
  d: number;
}) {
  return (
    <group>
      {zoneList.map((z) => (
        <ZoneTile key={z.id} zone={z} w={w} d={d} />
      ))}
    </group>
  );
}

function ZoneTile({ zone, w, d }: { zone: Zone; w: number; d: number }) {
  const shape = useMemo(() => {
    const s = new THREE.Shape();
    zone.polygon.forEach(([nx, ny], i) => {
      const x = (nx - 0.5) * w;
      const y = (ny - 0.5) * d;
      if (i === 0) s.moveTo(x, y);
      else s.lineTo(x, y);
    });
    s.closePath();
    return s;
  }, [zone.polygon, w, d]);

  const outlinePoints = useMemo(() => {
    const pts: [number, number, number][] = zone.polygon.map(([nx, ny]) => [
      (nx - 0.5) * w,
      0.02,
      (ny - 0.5) * d,
    ]);
    pts.push(pts[0]);
    return pts;
  }, [zone.polygon, w, d]);

  const [cx, cy] = useMemo(() => {
    const cx = zone.polygon.reduce((s, [x]) => s + x, 0) / zone.polygon.length;
    const cy = zone.polygon.reduce((s, [, y]) => s + y, 0) / zone.polygon.length;
    return [cx, cy];
  }, [zone.polygon]);
  const [lx, , lz] = toWorld(cx, cy, w, d);

  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]}>
        <shapeGeometry args={[shape]} />
        <meshBasicMaterial
          color={zone.color}
          transparent
          opacity={0.09}
          depthWrite={false}
        />
      </mesh>
      <Line
        points={outlinePoints}
        color={zone.color}
        lineWidth={1.5}
        transparent
        opacity={0.85}
      />
      <Text
        position={[lx, 0.05, lz]}
        rotation={[-Math.PI / 2, 0, 0]}
        fontSize={0.22}
        color={zone.color}
        anchorX="center"
        anchorY="middle"
        outlineColor="#000"
        outlineWidth={0.005}
      >
        {zone.name.toUpperCase()}
      </Text>
    </group>
  );
}

function Surfaces({
  items,
  w,
  d,
}: {
  items: TwinSurface[];
  w: number;
  d: number;
}) {
  return (
    <group>
      {items.map((s) => {
        const [x, , z] = toWorld(s.position[0], s.position[1], w, d);
        const color = s.active ? "#00d4ff" : "#5a5a60";
        return (
          <group key={s.id} position={[x, 0, z]}>
            <mesh position={[0, 0.5, 0]} castShadow>
              <boxGeometry args={[0.4, 1, 0.4]} />
              <meshStandardMaterial
                color="#181c22"
                metalness={0.6}
                roughness={0.4}
              />
            </mesh>
            <mesh position={[0, 1.05, 0]}>
              <boxGeometry args={[0.5, 0.1, 0.5]} />
              <meshStandardMaterial
                color={color}
                emissive={color}
                emissiveIntensity={s.active ? 1.2 : 0.2}
              />
            </mesh>
            {s.active && (
              <mesh position={[0, 2, 0]}>
                <cylinderGeometry args={[0.05, 0.4, 1.8, 12, 1, true]} />
                <meshBasicMaterial
                  color={color}
                  transparent
                  opacity={0.06}
                  blending={THREE.AdditiveBlending}
                  side={THREE.DoubleSide}
                  depthWrite={false}
                />
              </mesh>
            )}
            <Text
              position={[0, 1.4, 0]}
              fontSize={0.16}
              color={s.active ? "#00d4ff" : "#5a5a60"}
              anchorY="bottom"
            >
              {s.label}
            </Text>
          </group>
        );
      })}
    </group>
  );
}

function Cameras({ w, d }: { w: number; d: number }) {
  const positions: [number, number, number][] = [
    [-w / 2 + 0.5, 3.2, -d / 2 + 0.5],
    [w / 2 - 0.5, 3.2, d / 2 - 0.5],
  ];
  return (
    <group>
      {positions.map((p, i) => (
        <group key={i} position={p}>
          <mesh>
            <sphereGeometry args={[0.12, 16, 16]} />
            <meshStandardMaterial color="#1a1f28" metalness={0.7} roughness={0.3} />
          </mesh>
          <mesh position={[0, -0.05, 0]}>
            <cylinderGeometry args={[0.04, 0.04, 0.1, 12]} />
            <meshStandardMaterial color="#0a0d12" />
          </mesh>
          <mesh position={[0, -0.08, 0.04]}>
            <sphereGeometry args={[0.012, 8, 8]} />
            <meshStandardMaterial
              color="#ff453a"
              emissive="#ff453a"
              emissiveIntensity={2}
            />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function LivePeople({ avatars }: { avatars: TwinAvatarDelta[] }) {
  return (
    <group>
      {avatars.map((a) => (
        <PersonAvatar
          key={a.personId}
          id={a.label}
          color="#42faa1"
          x={a.x}
          y={a.z}
          focused
          worldCoords
        />
      ))}
    </group>
  );
}

function People({
  time,
  selectedPerson,
  w,
  d,
}: {
  time: number;
  selectedPerson?: string | null;
  w: number;
  d: number;
}) {
  const live = positionsAt(time);
  return (
    <group>
      {live.map((p) => {
        const focused = !selectedPerson || selectedPerson === p.id;
        return (
          <PersonAvatar
            key={p.id}
            id={p.id}
            color={p.color}
            x={p.x}
            y={p.y}
            focused={focused}
            w={w}
            d={d}
          />
        );
      })}
      {selectedPerson && (
        <PersonTrail id={selectedPerson} upTo={time} w={w} d={d} />
      )}
    </group>
  );
}

function PersonAvatar({
  id,
  color,
  x,
  y,
  focused,
  w,
  d,
  worldCoords = false,
}: {
  id: string;
  color: string;
  x: number;
  y: number;
  focused: boolean;
  w?: number;
  d?: number;
  worldCoords?: boolean;
}) {
  const ringRef = useRef<THREE.Mesh>(null);

  useFrame((_, dt) => {
    if (ringRef.current) {
      ringRef.current.rotation.z += dt * 0.6;
      const s = 1 + Math.sin(performance.now() * 0.003) * 0.08;
      ringRef.current.scale.setScalar(s);
    }
  });

  const [wx, , wz] = worldCoords
    ? [x, 0, y]
    : toWorld(x, y, w ?? DEFAULT_W, d ?? DEFAULT_D);

  return (
    <group position={[wx, 0, wz]}>
      <mesh ref={ringRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.015, 0]}>
        <ringGeometry args={[0.18, 0.28, 32]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={focused ? 0.7 : 0.25}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
      <mesh position={[0, 0.55, 0]} castShadow>
        <capsuleGeometry args={[0.16, 0.55, 8, 16]} />
        <meshStandardMaterial
          color={color}
          metalness={0.4}
          roughness={0.35}
          emissive={color}
          emissiveIntensity={focused ? 0.6 : 0.15}
          transparent
          opacity={focused ? 1 : 0.45}
        />
      </mesh>
      <mesh position={[0, 1.05, 0]} castShadow>
        <sphereGeometry args={[0.13, 16, 16]} />
        <meshStandardMaterial
          color={color}
          metalness={0.4}
          roughness={0.3}
          emissive={color}
          emissiveIntensity={focused ? 0.7 : 0.2}
          transparent
          opacity={focused ? 1 : 0.45}
        />
      </mesh>
      {focused && (
        <Text
          position={[0, 1.5, 0]}
          fontSize={0.12}
          color="#ffffff"
          anchorX="center"
        >
          {id}
        </Text>
      )}
    </group>
  );
}

function PersonTrail({
  id,
  upTo,
  w,
  d,
}: {
  id: string;
  upTo: number;
  w: number;
  d: number;
}) {
  const p = peopleTracks.find((pp) => pp.id === id);
  const points = useMemo<[number, number, number][] | null>(() => {
    if (!p) return null;
    const pts: [number, number, number][] = [];
    for (const [x, y, t] of p.waypoints) {
      if (t > upTo) break;
      const [wx, , wz] = toWorld(x, y, w, d);
      pts.push([wx, 0.05, wz]);
    }
    return pts;
  }, [p, upTo, w, d]);

  if (!p || !points || points.length < 2) return null;
  return (
    <Line points={points} color={p.color} lineWidth={2} transparent opacity={0.85} />
  );
}
