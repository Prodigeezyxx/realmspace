"use client";

/**
 * realmspace — the calibration step.
 *
 * `privacy.md` §"Sensitive zones": *"the operator can draw an 'opt-out' zone on
 * the calibration step. Pixels within that polygon are masked before any model
 * runs."* The doc has said that since Phase 0, the session wizard repeats it to
 * the operator during setup — *"All sensitive zones masked at pixel level"* —
 * and until Phase 6 no code anywhere touched a pixel. This screen is the half
 * an operator uses; `perception/mask.py` is the half that does it.
 *
 * Calibrates the **active session**, rather than taking one in the path. Not a
 * shortcut: this app builds with `output: export`, so a dynamic segment would
 * need `generateStaticParams` and there is no build-time list of sessions to
 * give it. Every other session-scoped screen here resolves the same way.
 *
 * Three things this screen refuses to fake, each of them stated on it:
 *
 * - **There is no camera preview.** Explained in `MaskEditor`. The operator is
 *   drawing against remembered geometry, and confirming against the perception
 *   preview window rather than against this page.
 * - **A mask only takes effect on an edge box that can reach the backend.**
 *   Saving here bumps a revision; a running camera picks it up on its next
 *   poll, and one that is offline keeps the mask it last fetched.
 * - **Only the privacy mask is offered.** `event-bus-spec.md` §3 pins four
 *   calibration kinds and the other three have no consumer — the backend
 *   refuses them with the reason, and a screen that offered them anyway would
 *   be collecting settings nothing reads.
 */

import { AlertTriangle, Camera, Check, Info, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { MaskEditor } from "@/components/sessions/MaskEditor";
import { KioskPanel } from "@/components/sessions/KioskPanel";
import { TabletPanel } from "@/components/sessions/TabletPanel";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import {
  useCalibration,
  type CameraConfig,
  type Point,
  type ZoneConfig,
} from "@/lib/ops/useCalibration";
import { useActiveSession } from "@/lib/session/store";

export default function CalibrationPage() {
  const session = useActiveSession();
  const { status, cameras, zones, detail, refresh, declare, assignZone, setMask } =
    useCalibration(session.id);
  const [adding, setAdding] = useState(false);
  const [newId, setNewId] = useState("cam-1");
  const [error, setError] = useState<string | null>(null);

  async function add() {
    setAdding(true);
    setError(await declare(newId.trim(), newId.trim()));
    setAdding(false);
  }

  return (
    <div className="max-w-[900px] mx-auto p-6 md:p-10 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="info" className="mb-2">
            <Camera size={11} />
            {session.name}
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">Calibration</h1>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            Draw an opt-out region for each camera. Pixels inside it are filled
            black on the edge box <em>before</em> the model sees the frame, so
            nobody standing there is ever detected, tracked or counted — this is
            not a filter applied to the results afterwards.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} />}
          onClick={() => void refresh()}
        >
          Refresh
        </Button>
      </div>

      <Panel
        title={
          <span className="flex items-center gap-2">
            <Info size={14} />
            No camera preview here
          </span>
        }
      >
        <p className="text-sm text-text-secondary leading-relaxed">
          The grid below is the camera&rsquo;s frame in proportion, not a picture
          of it. Frames stay in a 60-second buffer in memory on the perception
          laptop and never leave it, so there is nothing to show you here without
          changing that — which is a decision about the privacy posture, not a
          convenience for this screen. Check the shape landed where you meant it
          in the perception preview window, where the masked region is black.
        </p>
      </Panel>

      {status === "loading" && <EmptyState variant="page" title="Reading the session…" />}

      {(status === "offline" || status === "error") && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title={status === "offline" ? "No backend configured" : "Could not read the session"}
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" && (
        <>
          {!cameras.length && (
            <EmptyState
              variant="page"
              icon={<Camera size={20} />}
              title="No cameras declared"
              hint="A camera has to exist before it can be calibrated — otherwise a mask would sit here for a camera_id nothing ever asks about. Add the id you start perception with."
            />
          )}

          {cameras.map((camera) => (
            <CameraPanel
              key={camera.id}
              camera={camera}
              onSave={(polygon, note) => setMask(camera.id, polygon, note)}
            />
          ))}

          {cameras.length > 1 && (
            <ZoneOwnership cameras={cameras} zones={zones} onAssign={assignZone} />
          )}

          <Panel title="Add a camera">
            <div className="space-y-3">
              <p className="text-sm text-text-secondary leading-relaxed">
                Use exactly the id you pass to{" "}
                <code className="text-xs">perception/realmspace.py --camera-id</code>.
                They have to match: the edge box asks for its mask by that name,
                and a mismatch means it asks about a camera that does not exist
                and refuses to start rather than running unmasked.
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  type="text"
                  value={newId}
                  onChange={(e) => setNewId(e.target.value)}
                  placeholder="cam-1"
                  className="text-sm bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2 placeholder:text-text-muted"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<Plus size={14} />}
                  disabled={adding || !newId.trim()}
                  onClick={() => void add()}
                >
                  {adding ? "Adding…" : "Add camera"}
                </Button>
              </div>
              {error && (
                <p className="text-xs text-accent-red leading-relaxed">{error}</p>
              )}
            </div>
          </Panel>
        </>
      )}

      {/* The other half of setting a stand up: which cameras see it, and which
          touchpoints report being used. Both are per-activation configuration
          an operator does once, standing in the room. */}
      <TabletPanel sessionId={session.id} touchpoints={session.touchpoints} />
      <KioskPanel sessionId={session.id} touchpoints={session.touchpoints} />
    </div>
  );
}

/**
 * Which camera's frame each zone was drawn in.
 *
 * **Only shown once a session has two cameras**, because with one there is no
 * question to answer and a control that only ever has one right answer invites
 * an operator to get it wrong. This is the same call the calibration screen
 * already makes about the three calibration kinds nothing consumes.
 */
function ZoneOwnership({
  cameras,
  zones,
  onAssign,
}: {
  cameras: CameraConfig[];
  zones: ZoneConfig[];
  onAssign: (zoneId: string, cameraId: string | null) => Promise<string | null>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function assign(zoneId: string, value: string) {
    setBusy(zoneId);
    setError(await onAssign(zoneId, value === "" ? null : value));
    setBusy(null);
  }

  return (
    <Panel title="Which camera sees each zone">
      <div className="space-y-4">
        <p className="text-sm text-text-secondary leading-relaxed">
          A zone polygon is drawn in one camera&rsquo;s frame, and the same
          coordinates are a different piece of floor in another. Left on{" "}
          <em>every camera</em>, a zone counts anybody standing in that corner of
          any frame &mdash; a visitor credited with a stay somewhere they never
          stood. Set it wherever two cameras point at different parts of the
          booth; leave it alone where two cameras cover one stand from either
          side.
        </p>

        {!zones.length && (
          <p className="text-sm text-text-muted">
            This session has no zones yet. They come from the session wizard.
          </p>
        )}

        {zones.map((zone) => (
          <div
            key={zone.id}
            className="flex items-center justify-between gap-3 flex-wrap border-b border-border-hairline last:border-0 pb-3 last:pb-0"
          >
            <div>
              <p className="text-sm text-text-primary">{zone.name ?? zone.id}</p>
              <p className="text-xs text-text-muted">{zone.id}</p>
            </div>
            <select
              value={zone.cameraId ?? ""}
              disabled={busy === zone.id}
              onChange={(e) => void assign(zone.id, e.target.value)}
              className="text-sm bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2"
            >
              <option value="">Every camera</option>
              {cameras.map((camera) => (
                <option key={camera.id} value={camera.id}>
                  {camera.label || camera.id}
                </option>
              ))}
            </select>
          </div>
        ))}

        {error && <p className="text-xs text-accent-red leading-relaxed">{error}</p>}
      </div>
    </Panel>
  );
}

function CameraPanel({
  camera,
  onSave,
}: {
  camera: CameraConfig;
  onSave: (polygon: Point[] | null, note: string) => Promise<string | null>;
}) {
  const [polygon, setPolygon] = useState<Point[]>(camera.privacyMask ?? []);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);

  async function save(next: Point[] | null) {
    setBusy(true);
    const error = await onSave(next, note);
    setBusy(false);
    setOutcome(
      error ??
        (next
          ? "Saved. A running camera picks it up within about ten seconds."
          : "Cleared. A running camera stops masking within about ten seconds.")
    );
    if (!error) setNote("");
  }

  const masking = (camera.privacyMask?.length ?? 0) > 0;

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Pill variant={masking ? "success" : "neutral"}>
            {masking ? <Check size={11} /> : <AlertTriangle size={11} />}
            {masking ? "Masking" : "No mask"}
          </Pill>
          {camera.label}
        </span>
      }
      subtitle={`${camera.id} · revision ${camera.maskRevision}`}
    >
      <div className="space-y-4">
        <MaskEditor polygon={polygon} onChange={setPolygon} />

        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why — e.g. “payment terminal behind the desk”"
          className="w-full text-sm bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2 placeholder:text-text-muted"
        />

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="primary"
            size="sm"
            icon={<Check size={14} />}
            disabled={busy || polygon.length < 3}
            onClick={() => void save(polygon)}
          >
            {busy ? "Saving…" : "Save mask"}
          </Button>
          {masking && (
            <Button
              variant="ghost"
              size="sm"
              icon={<Trash2 size={14} />}
              disabled={busy}
              onClick={() => {
                setPolygon([]);
                void save(null);
              }}
            >
              Remove mask
            </Button>
          )}
        </div>

        {outcome && (
          <p className="text-xs text-text-secondary leading-relaxed">{outcome}</p>
        )}

        <p className="text-xs text-text-muted leading-relaxed">
          Saving also resets this camera&rsquo;s drift baseline. Masking changes
          what the model sees, so the readings from before the change are not a
          fair comparison — measuring against them would report your own
          correction as a fault.
        </p>
      </div>
    </Panel>
  );
}
