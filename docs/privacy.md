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
| Pose keypoints (skeleton) | ✅ briefly, **and never on the bus** | Gaze vector — see below |
| Face mesh | ❌ never persisted | Discarded after gaze vector is computed |
| Face embeddings | ❌ never computed | Out of scope by architecture |
| Clothing color / appearance | ✅ as a short VLM-generated phrase | Helping the dashboard say "person in red coat" |
| Raw frames | ✅ briefly (≤60s ring buffer), then deleted | Live dashboard preview only |
| Audio | ❌ never recorded | We don't process audio |

**Where the skeleton stops.** Pose keypoints are consumed inside perception's
frame loop and are never posted. What reaches the bus is two numbers — a facing
direction and a confidence (`perception/heading.py`) — and the consumer that
reads them never sees a joint. This is not tidiness: the event log is
append-only and exported, so anything written to it is permanent, and a skeleton
is a great deal more identifying than a bounding box. The same rule this table
already applies to face mesh, one level up.

**A shared report link.** An operator can send a client a URL that opens one
activation's report with no account (`routers/share.py`). It carries no contact
details: every event whose type is in the erasure job's own `PII_TYPES` is passed
through the same `redact` a withdrawal uses, so names, emails, companies and
titles are gone before the response leaves the server. The events themselves
survive, so the counts a client sees match the operator's. The link expires, can
be revoked, and reaches nothing but that one report — not the live feed, the
twin, Ask, the ledger or any write path.

**Retention removes things now.** Each tier sells a window (`gtm.md`), and past
it an activation's events have their payloads emptied and its visitors deleted
from the graph. What remains in the log is a skeleton — type and timestamps, no
contents — kept so the rest of the system stays truthful: derived ids resolve,
parked events still point somewhere, and a replay cannot silently produce a
different answer. **A purged row still exists**, and that is said here rather
than described as deletion, because the alternative is a promise the database
contradicts.

## What we never do

- **No facial recognition.** No FaceNet, no ArcFace, no biometric vectors.
- **No cross-session re-identification.** When the session ends, the
  person IDs are destroyed and cannot be regenerated.
- **No cross-camera re-identification within a session, except by
  hand-drawn zone topology.** A person who walks behind a wall and reappears
  is treated as a new ID.

  **Enforced since 2026-08-21, not merely intended.** Every visitor is keyed on
  `camera_id/anon_id`, so somebody who leaves one camera's view and enters
  another's is two visitors — two `(:Person)` nodes, two dwell series, counted
  twice in reach. That is the cost of this promise and it is the honest side to
  err on: the alternative is a system that quietly decides two strangers are the
  same person. Before this, two cameras' first visitors were merged into one by
  accident, which broke the promise in the *other* direction while looking like
  nothing was wrong.

  Grouping declines to pair people seen by different cameras for the same
  reason, and zones belong to the camera whose frame they were drawn in.
- **No cloud upload of video frames.** AI reasoning calls receive only
  structured event summaries — never images.

  **The vendor changed on 2026-08-31 and this line has not been renegotiated
  with anybody.** It named "Claude / GPT-4o"; the deployment now calls
  **OpenRouter**, which routes to DeepSeek and Google models. Question 7 below —
  *what jurisdictions do you operate in for the AI reasoning calls* — therefore
  has a materially different answer than it did last week, and any pilot
  agreement already signed against the old one needs re-reading rather than
  reinterpreting.

  **All three AI paths now send a summary and nothing else.** Ask the Room and
  the ten-minute insight send measurements only, and the insight digest is
  anonymous by construction. The follow-up drafter (`consumers/sdr.py`) used to
  send a consented visitor's **name and company**, because it is writing an email
  to them — not an image, so it did not break the sentence above, but "structured
  event summary" does not describe a person's identity either.

  **Answered *no*, 2026-08-31**, rather than left for whoever signs the pilot
  agreement. It was put to them as a question because a T2 consent covers
  contacting that person and it is not obvious whether it covers their name
  reaching a model vendor — and the fix turned out to cost nothing that anybody
  would have to weigh. The prompt carries the literal placeholders
  `[FIRST_NAME]` and `[COMPANY]`, and `app/llm/prompts.splice_identity` puts the
  real person in on our side of the wire. The vendor sees the shape of the email
  and where somebody walked; it never sees who they are. A reviewer sees exactly
  the letter they would have seen either way.

  A draft that comes back with a placeholder we cannot fill — the model inventing
  `[LAST_NAME]` or `[PRODUCT]` — is discarded in favour of the composed draft
  rather than sent on to a reviewer looking like a broken mail-merge.

  This narrows what leaves the building; it does not narrow question 7. The
  measurements still go to OpenRouter, and where they are processed is still the
  thing to answer.
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

**True in code since 2026-08-19, and stated plainly because it was not before.**
This paragraph has been in this document since Phase 0 and the session wizard
repeats it to the operator during setup — *"All sensitive zones masked at pixel
level"* — while no code anywhere touched a pixel. It does now: an operator draws
the polygon on `/sessions/calibration`, `perception/mask.py` fetches it, and
`realmspace.py` fills it black **before** the frame reaches YOLO, so a person
standing there is never detected, tracked or counted. Verified against a real
clip: 40 detections in the masked region without the mask, 0 with it, and the
unmasked region unchanged.

Three properties of that path worth knowing:

- **It fails closed.** An edge box that cannot fetch a mask and has none cached
  refuses to start rather than running unmasked. Every other failure in this
  system produces a number nobody can trust; this one would produce a recording
  of somebody who was promised there would not be one, and there is nothing to
  review afterwards and nothing to retract.
- **It survives an outage.** The last fetched mask is cached on the box, so a
  laptop that boots during a wifi drop still masks.
- **Every change is on the log.** `calibration.updated` records when masking
  started or stopped, on which camera, and who changed it — but **not the
  polygon**, because the log is replayed and exported and a booth's sensitive
  geometry does not need to be in every copy of it.

What the operator does **not** get is a camera preview to draw over. Frames
never leave the perception laptop's 60-second buffer, so the editor is a
coordinate grid and the shape is confirmed in the perception preview window
instead. That is a usability cost taken deliberately in favour of the row in the
table above.

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
