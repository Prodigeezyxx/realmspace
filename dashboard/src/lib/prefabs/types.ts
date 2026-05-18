export interface PrefabZone {
  id: string;
  label: string;
  polygon: [number, number][];
  color?: string;
}

export interface PrefabWall {
  from: [number, number];
  to: [number, number];
  height?: number;
}

export interface Prefab {
  id: string;
  name: string;
  description: string;
  thumbnail: string;
  boothSize: { width: number; depth: number };
  zones: PrefabZone[];
  walls: PrefabWall[];
}
