# Privacy posture

This document is the version we send to a client's legal / compliance team
before deployment. It is also linked from the public landing page so it
arrives in the prospect's inbox unprompted.

## Plain English

RealmSpace turns a webcam pointed at your activation into a structured
graph of anonymous behaviour. It does not, and cannot, recognise faces.
It does not, and cannot, identify the same person across two sessions or
two cameras. It runs entirely on a laptop you can hold in your hand. Raw
video is deleted within minutes. Only the structured graph survives.

That's not a feature we added to be polite. It's how the system is
*architecturally* unable to do otherwise.

## What we capture

| Captured | Stored | Used for |
|---|---|---|
| Bounding boxes around people | ✅ briefly (≤60s ring buffer) | Tracking IDs, dwell calculation |
| Pose keypoints (skeleton) | ✅ briefly | Gaze vector, sitting/standing detection |
| Face mesh | ❌ never persisted | Discarded after gaze vector is computed |
| Face embeddings | ❌ never computed | Out of scope by architecture |
| Clothing color / appearance | ✅ as a short VLM-generated phrase | Helping the dashboard say "person in red coat" |
| Raw frames | ✅ briefly (≤60s ring buffer), then deleted | Live dashboard preview only |
| Audio | ❌ never recorded | We don't process audio |

## What we never do

- **No facial recognition.** No FaceNet, no ArcFace, no biometric vectors.
- **No cross-session re-identification.** When the session ends, the
  person IDs are destroyed and cannot be regenerated.
- **No cross-camera re-identification within a session, except by
  hand-drawn zone topology.** A person who walks behind a wall and reappears
  is treated as a new ID.
- **No cloud upload of video frames.** AI reasoning calls (Claude / GPT-4o)
  receive only structured event summaries — never images.
- **No customer-identifiable storage.** We don't know who the people in your
  booth are. Neither do you. Both are by design.

## Where data lives

| Data | Location |
|---|---|
| Live camera frames | RAM on the perception laptop. 60-second ring buffer. Auto-discarded. |
| Detection events | Local Postgres + Neo4j on the perception laptop. |
| Aggregated metrics | Local Postgres. Optionally synced to the client's Supabase mirror after the activation, with all `Person` nodes already anonymised. |
| Client-facing dashboard | Served from the laptop on the venue's LAN. Optional cloud read-replica for stakeholders not on site. |
| Reports | Generated as PDFs at session close; emailed and/or uploaded to client's preferred storage. |

**Nothing about a visitor ever leaves the laptop unless the client explicitly
configures cloud sync.**

## Sensitive zones

For deployments with privacy-sensitive surfaces (e.g. behind a desk, near
a payment terminal, near a fitting room), the operator can draw an
"opt-out" zone on the calibration step. Pixels within that polygon are
masked before any model runs.

## Compliance posture

- **GDPR (EU)** — visitors are unidentified data subjects throughout; no
  personal data is collected as defined by GDPR Article 4(1). DPIA template
  available on request.
- **CCPA (US, California)** — same posture. No "personal information" as
  defined by CCPA §1798.140 is collected.
- **UK DPA 2018** — equivalent to GDPR; same posture.
- **NIS2 / DORA / sector-specific** — out of scope for typical activations;
  on-prem deployment available for regulated industries.

## What signage we recommend

Per most jurisdictions you should display a visible sign at the entry
informing visitors that anonymous spatial analytics are in use, that no
faces are stored, and that no identification is performed. A pre-approved
A5 sign template is included in the deployment kit.

## What clients should ask us before signing

We answer all of these in writing as part of the pilot agreement:

1. Where exactly does the data live during and after the activation?
2. Who has access to the laptop and the dashboard?
3. What happens when the session ends?
4. What's purged automatically and what's retained?
5. Can we view the raw event stream ourselves?
6. What's the worst-case data breach scenario?
7. What jurisdictions do you operate in for the AI reasoning calls?

If you're a buyer reading this and your team needs a different answer to
any of the above, contact us — we'll customise the deployment to your
compliance posture.
