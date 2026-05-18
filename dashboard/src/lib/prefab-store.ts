const LS_KEY = "realmspace.prefab.v1";

export function getActivePrefabId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LS_KEY);
}

export function setActivePrefabId(id: string) {
  if (typeof window === "undefined") return;
  localStorage.setItem(LS_KEY, id);
}
