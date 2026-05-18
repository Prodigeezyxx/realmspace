/**
 * Shared COCO-SSD loader — preload once on app boot so Live start is faster.
 */

export type ModelHandle = {
  detect: (video: HTMLVideoElement) => Promise<
    Array<{ bbox: [number, number, number, number]; class: string; score: number }>
  >;
};

let modelPromise: Promise<ModelHandle> | null = null;
let loadStage = "idle";

const stageListeners = new Set<(stage: string) => void>();

function setStage(stage: string) {
  loadStage = stage;
  stageListeners.forEach((l) => l(stage));
}

export function getModelLoadStage() {
  return loadStage;
}

export function subscribeModelLoadStage(listener: () => void) {
  stageListeners.add(listener);
  return () => stageListeners.delete(listener);
}

export function preloadDetectionModel(): Promise<ModelHandle> {
  if (modelPromise) return modelPromise;

  modelPromise = (async () => {
    setStage("Loading TensorFlow.js…");
    const tf = await import("@tensorflow/tfjs");
    await import("@tensorflow/tfjs-backend-webgl");
    await tf.setBackend("webgl");
    await tf.ready();

    setStage("Downloading COCO-SSD weights (~28 MB)…");
    const cocoSsd = await import("@tensorflow-models/coco-ssd");
    const model = (await cocoSsd.load({
      base: "mobilenet_v2",
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
