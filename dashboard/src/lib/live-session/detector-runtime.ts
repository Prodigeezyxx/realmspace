/**
 * Module-singleton sensor + inference loop.
 * Survives React remounts so leaving /live does not stop the session.
 */
import { getDetectionModel } from "@/lib/live-session/model-cache";
import { spatialDeriver } from "@/lib/live-session/spatial-deriver";
import { liveSessionActions } from "@/lib/live-session/store";
import type { DetectorStats } from "@/lib/live-session/types";
import { CentroidTracker } from "@/lib/tracker";

const MIN_FRAME_MS = 100;
const CLASS_FILTER = ["person"];
const MIN_CONFIDENCE = 0.5;

class DetectorRuntime {
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;
  private model: Awaited<ReturnType<typeof getDetectionModel>> | null = null;
  private readonly tracker = new CentroidTracker({
    classFilter: CLASS_FILTER,
    idPrefix: "P",
  });

  private running = false;
  private starting = false;
  private rafId: number | null = null;
  private sessionStartedAt: number | null = null;
  private lastInferMs = 0;
  private lastFpsTick = 0;
  private fpsFrames = 0;
  private fps = 0;
  private modelMs = 0;
  private totalSeen = 0;
  private onStream: ((stream: MediaStream | null) => void) | null = null;

  attachVideo(el: HTMLVideoElement | null) {
    this.video = el;
    if (el && this.stream) {
      el.srcObject = this.stream;
      void el.play().catch(() => {});
    }
  }

  setStreamListener(fn: (stream: MediaStream | null) => void) {
    this.onStream = fn;
    fn(this.stream);
  }

  isRunning() {
    return this.running;
  }

  keepVideoAlive() {
    if (this.video?.srcObject) void this.video.play().catch(() => {});
  }

  async start() {
    if (this.running || this.starting) return;
    this.starting = true;
    liveSessionActions.setStatus("loading-model");

    try {
      const [model, mediaStream] = await Promise.all([
        getDetectionModel(),
        navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: "user",
            width: { ideal: 640 },
            height: { ideal: 480 },
          },
          audio: false,
        }),
      ]);

      this.model = model;
      this.stream = mediaStream;
      this.onStream?.(mediaStream);

      if (this.video) {
        this.video.srcObject = mediaStream;
        await this.video.play();
      }

      this.tracker.reset();
      spatialDeriver.reset();
      this.sessionStartedAt = Date.now();
      this.totalSeen = 0;
      this.fps = 0;
      this.fpsFrames = 0;
      this.lastFpsTick = performance.now();
      this.lastInferMs = 0;
      this.running = true;
      this.starting = false;

      liveSessionActions.setStatus("running");
      this.loop(performance.now());
    } catch (err) {
      this.starting = false;
      const e = err as Error;
      if (e.name === "NotAllowedError" || /permission/i.test(e.message)) {
        liveSessionActions.setStatus("denied", e.message);
      } else {
        liveSessionActions.setStatus("error", e.message);
      }
      console.error("[DetectorRuntime] start failed:", err);
    }
  }

  stop() {
    this.running = false;
    this.starting = false;

    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }

    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    }

    if (this.video) this.video.srcObject = null;
    this.onStream?.(null);

    this.model = null;
    this.tracker.reset();
    this.sessionStartedAt = null;
    this.totalSeen = 0;
    this.fps = 0;

    liveSessionActions.setStatus("idle");
    liveSessionActions.resetSession();
  }

  private loop(frameTs: number) {
    if (!this.running) return;

    const video = this.video;
    if (!video || video.readyState !== 4) {
      this.rafId = requestAnimationFrame((t) => this.loop(t));
      return;
    }

    if (frameTs - this.lastInferMs < MIN_FRAME_MS) {
      this.rafId = requestAnimationFrame((t) => this.loop(t));
      return;
    }
    this.lastInferMs = frameTs;

    void this.inferFrame(video).finally(() => {
      if (this.running) {
        this.rafId = requestAnimationFrame((t) => this.loop(t));
      }
    });
  }

  private async inferFrame(video: HTMLVideoElement) {
    const model = this.model;
    if (!model) return;

    const t0 = performance.now();
    const raw = await model.detect(video);
    this.modelMs = Math.round(performance.now() - t0);

    const detections = raw
      .filter((d) => d.score >= MIN_CONFIDENCE)
      .filter((d) => CLASS_FILTER.includes(d.class))
      .map((d) => ({
        bbox: d.bbox as [number, number, number, number],
        class: d.class,
        score: d.score,
      }));

    const tracks = this.tracker.update(detections, Date.now());
    const activeTracks = this.tracker.active();
    this.totalSeen = this.tracker.totalAssigned();

    this.fpsFrames += 1;
    const now = performance.now();
    if (now - this.lastFpsTick > 1000) {
      const dt = (now - this.lastFpsTick) / 1000;
      this.fps = Number((this.fpsFrames / dt).toFixed(1));
      this.lastFpsTick = now;
      this.fpsFrames = 0;
    }

    const frameWidth = video.videoWidth || 640;
    const frameHeight = video.videoHeight || 480;

    const counts: Record<string, number> = {};
    activeTracks.forEach((t) => {
      counts[t.class] = (counts[t.class] ?? 0) + 1;
    });

    const stats: DetectorStats = {
      fps: this.fps,
      modelMs: this.modelMs,
      classCounts: counts,
      activeTracks,
      tracks,
      totalSeen: this.totalSeen,
      sessionStartedAt: this.sessionStartedAt,
      frameWidth,
      frameHeight,
    };

    liveSessionActions.onStats(stats);
  }
}

export const detectorRuntime = new DetectorRuntime();

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") detectorRuntime.keepVideoAlive();
  });
}
