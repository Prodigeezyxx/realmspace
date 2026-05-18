/**
 * Lazy-loaded COCO-SSD — only loads when user starts a live session.
 * Uses lite_mobilenet_v2 for lower RAM than mobilenet_v2.
 */

export type ModelHandle = {
  detect: (video: HTMLVideoElement) => Promise<
    Array<{ bbox: [number, number, number, number]; class: string; score: number }>
  >;
};

let modelPromise: Promise<ModelHandle> | null = null;
let loadStage = "idle";

const stageListeners = new Set<() => void>();

function setStage(stage: string) {
  loadStage = stage;
  stageListeners.forEach((l) => l());
}

export function getModelLoadStage() {
  return loadStage;
}

export function subscribeModelLoadStage(listener: () => void) {
  stageListeners.add(listener);
  return () => stageListeners.delete(listener);
}

/** Load only when live session starts — avoids ~300–600 MB on initial page load */
export function preloadDetectionModel(): Promise<ModelHandle> {
  if (modelPromise) return modelPromise;

  modelPromise = (async () => {
    setStage("Loading object detection model…");
    const tf = await import("@tensorflow/tfjs");
    await import("@tensorflow/tfjs-backend-webgl");
    await tf.setBackend("webgl");
    await tf.ready();

    setStage("Loading object detection model…");
    const cocoSsd = await import("@tensorflow-models/coco-ssd");
    const model = (await cocoSsd.load({
      base: "lite_mobilenet_v2",
    })) as unknown as ModelHandle;

    setStage("ready");
    return model;
  })().catch((err) => {
    modelPromise = null;
    setStage("error");
    throw err;
  });

  return modelPromise;
}

export function getDetectionModel(): Promise<ModelHandle> {
  return preloadDetectionModel();
}
