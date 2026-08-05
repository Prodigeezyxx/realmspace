# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Each entry explains what changed **in plain words** first, then the technical detail.
Sections are dated (with time, local timezone +0100) so you can see when things landed.

**Two tracks are running in parallel.** Phase 1's backend is being built twice on
purpose, so the two approaches can be compared before one is adopted:

- `[postgres-track]` — `floats-agent`. Graph as relational node/edge tables in the
  same SQL database as the bus, SQLite by default. Per `docs/adr/001-graph-store.md`.
- `[neo4j-track]` — `antoniorobles/phase1`. Graph in Neo4j, per `data-model.md`,
  `PRD.md` and `privacy.md`, which all name it.

Entries from 2026-07-28 onward carry a track tag. Earlier entries predate the
split and belong to neither.

## [Unreleased] — last updated 2026-08-04 (overnight)

### Added — 2026-08-04 (overnight) — `[neo4j-track]` you can now see what failed, and fix it

First item from the POD Phase 2 table (2.7 and 2.10). Worth noting that table is
mostly what `roadmap.md` calls **Phase 3** — the naming collides with the "Prove
ROI" phase this branch has been building.

- **Nobody could see a failed event.** When a consumer gave up on something, it
  was set aside in a table that has existed since the first migration — and
  there was no way to look at it without opening a database. A queue nobody can
  read is not a safety net.
  *New review screen at `/ops`, plus `GET /v1/dead-letters`, `POST …/retry` and
  `POST …/resolve`.*

- **Retry is offered only where it would actually be correct**, and where it is
  not, the screen says why rather than hiding the button. The graph writer can
  be retried: it depends on nothing but the event. The tracker cannot — it works
  out where each person is from everything that came before, so replaying one
  event on its own would produce a confident wrong answer, which is worse than
  the original failure. The live feed cannot either, for a different reason: it
  pushes to whoever is watching *now*, and re-sending an old event to a live
  screen fixes nothing.

- **A retry that fails again is recorded, not swallowed.** The entry stays open
  with its attempt count raised and the newest error attached. Otherwise an
  operator presses the same button over and over with nothing to show for it.

- **Dismiss exists alongside retry**, because the tracker's failures cannot be
  retried from this screen at all — and a queue that fills with entries nobody
  can clear is a queue people stop opening.

- **The same failure no longer appears three times.** An event can be set aside
  more than once — after a replay, a restart, a rewound cursor — and each one
  used to add another row. They now collapse onto one entry with the attempts
  added up and the most recent error kept. **A failure that recurs *after* a
  human signed it off still gets its own new entry**, because that is genuinely
  new information.

- **Retries now back off.** Each attempt waits twice as long as the last, up to
  a ceiling. The ceiling matters more than the growth: the tracker sits on the
  real-time path with a half-second budget, and an unbounded doubling would hold
  a whole batch behind one slow failure — exactly when the room is busiest.

- **The chaos test the acceptance criterion asks for.** Three events, a consumer
  that fails on all of them: each is set aside, the cursor moves past it, and
  the later events are still processed. That is the property the whole design
  exists for — one bad event must not stop the activation being measured, with
  nothing in the data to say when it stopped.

- **126 backend tests (was 112).** **Verified end to end against a running
  backend:** a deliberately malformed event failed three times, appeared in the
  queue with its payload and `KeyError: 'anon_id'`, was retried (failed again,
  recorded), then dismissed. **And verified by breaking it:** flattening the
  backoff, restoring duplicate rows, or letting any consumer be retried each
  fails its own test.

- **The nav has not been touched.** `/ops` is reachable by URL; adding it to the
  sidebar is a change to an existing surface, so the one-line diff is offered
  rather than applied.

- **Two things the other track had and we did not, now ported.** Producers can
  send many events in one request instead of one request each — which is what an
  RFID reader or a camera catching up after an outage actually needs. And a
  consumer can be told to catch up *now* rather than on its next poll, which is
  the natural thing to press after clearing something from the review queue.
  *`POST /events/batch` (capped at 500, **all or nothing** so a mixed-tenant
  batch cannot half-apply) and `POST /v1/consumers/{name}/drain`. Both differ
  from the other track's on purpose: ours are authenticated and take the tenant
  from the credential rather than a query parameter, and drain runs the **live**
  consumer — draining a freshly built one would advance the real cursor while
  emitting whatever an empty state implies, a quieter version of the bug the
  retry rules above exist to prevent.*
  *137 backend tests (was 126); breaking the all-or-nothing rule or the
  live-instance lookup each fails its own test.*

### Added — 2026-08-04 (late) — `[neo4j-track]` the twin replays a session that actually happened

- **A recorded session could never be replayed.** The 3D twin had a scrubber,
  play/pause and speed controls — and played five hand-drawn paths through a
  fictional ten minutes. The one thing it existed to do, replay a real
  activation, was blocked by a check that only ever let the demo through.
  *Positions now come from the session's own event log. `lib/twin/replay.ts`;
  the `!isDemo && !detectorRunning` gate replaced with "does this session have
  any events", and the 620-second constant replaced with the session's real
  length.*

- **Two kinds of path, and the twin does not pretend they are the same.** Where
  a camera actually saw somebody, the avatar walks where they walked. Where the
  system only knows *which zone* they were in, the avatar stands at the middle
  of that zone — and is marked as such, with a note saying the line between two
  zones is not a route anyone took. Drawing a guessed path as though it were
  traced would be the twin's version of the invented ROI figure the report just
  had removed: convincing, and impossible for the viewer to check.

- **The replay does not go through the browser's event store**, which only holds
  the last 5,000 events and silently discards the rest. A busy session fills
  that in minutes, and the discarding would have quietly broken the report too —
  the earliest visits would simply have stopped existing. The twin asks the
  server for the session directly and holds it in memory while the page is open.

- **The visitor list shows who is in the room at that moment**, rather than five
  fictional people with hand-written totals. A real activation has hundreds, and
  the useful question while scrubbing a timeline is who is there now.

- **90 dashboard tests (was 77).** Includes the real captured backend session
  replaying as measured paths. **Verified by breaking it:** making the zone
  fallback win over real detections, dropping the measured/inferred distinction,
  or restoring the hardcoded duration each fails its own test.

- **Expect a visual change on the demo.** It goes from five smooth paths to
  around 140 people stepping between zone centres, because the demo's events are
  zone-level. It is denser and less pretty, and it is what the data supports.

- **Not done:** the twin's booth model still comes from the demo's floor plan
  rather than the active session's own zones, so a real activation replays its
  people onto the wrong room. Called out rather than left to be discovered.

### Added — 2026-08-04 (evening) — `[neo4j-track]` the live screen finally shows the real activation

- **The live screen was never connected to the system.** Its numbers came from
  the camera in *your browser tab*, or from a mock file. So an activation
  running the way the whole backend was built for — a real camera on the floor
  feeding the server — showed nothing at all on the live view. The data had been
  arriving for days; nothing was reading it.
  *`/live` now reads the durable event log first, the in-browser detector second,
  and an honest empty third. The bus wins because it is the record of the whole
  activation across every camera; the detector is one tab's view of one webcam.*

- **An ROI panel that updates while the doors are open.** The four layers —
  reach, engagement, affinity, pipeline — computed live, so an operator can see
  a broken funnel on day one instead of reading about it in the report on day
  three. It runs **the same computation the report does**, which is the point: a
  tile that could show a number the report would not would have people
  optimising against a figure their client never sees.

- **Three more invented numbers gone.** "+2 in last 5m", "+18 last hour",
  "+12% wk" were comparisons against a previous period nobody has recorded —
  the same defect as the report's "+18% vs Yday". Replaced with what is
  actually known: how long since the last event arrived, how many events are
  behind the figure, and target-versus-actual when a target was set.

- **The demo now happens today instead of in May.** Pinned to a date months in
  the past, "people now" was permanently zero and the live view had nothing true
  to show. The demo day slides forward each hour, and a handful of visitors are
  deliberately left mid-visit so the room is not empty.
  *`seedDemoSession(tenantId, now)`; stable for any given hour, which outlasts
  any demo. **A trap worth recording:** the event ids had no timestamp in them,
  so re-seeding for a new hour produced byte-identical ids, the log deduped
  every one, and the timestamps silently did not move — the demo would have
  looked shifted in the source and been completely unchanged on screen. There is
  now a test that fails if the anchor leaves the id.*

- **The live page cannot be brought to its knees by a busy room.** Recomputing
  on every event would mean hundreds of recomputations a second with a camera
  running — worst exactly when the room is fullest. Coalesced to one a second,
  which is finer than anyone can read.

- **77 dashboard tests (was 63).** **Verified by breaking it:** removing the exit
  handling, or putting the seed's ids back the way they were, each fails its own
  test.

- **Not done, deliberately:** the live event feed still lists the browser
  tracker's events rather than the server's spatial ones. It will look
  inconsistent beside a wired KPI strip, and it is a known omission rather than
  an oversight.

### Added — 2026-08-04 (afternoon) — `[neo4j-track]` the system can now see people choosing *not* to engage

- **The report can finally say who walked past.** Someone who came right up to a
  stand, had a look, and moved on without going in was invisible: as far as the
  system was concerned they were never there. That is the one number that can
  make an activation look worse than the client hoped, which is exactly why it
  makes the rest of the report believable.
  *`spatial.passby` — the tracker records how close each person ever came to
  each zone and, when their track ends, reports the ones they came within reach
  of and never entered. `tracker_passby_radius`, 0.08 of the frame, matching the
  other track so a visitor is a pass-by on both or on neither.*

- **Going in cancels it.** Somebody who circles a stand twice and then walks in
  engaged with it — counting them as a skip as well would make one person both
  the success and the failure, and every zone's skip rate would be inflated by
  its own visitors. And someone pacing outside is one person who declined, not
  twelve.

- **Whoever is still in the room when the doors shut is now counted.** Ending a
  session in the dashboard tells the system, and everyone still being tracked
  gets closed out — final visit lengths and pass-bys included. Before this the
  tail of every activation simply vanished, and the people still there at the
  end are the engaged ones, so the loss ran in the worst possible direction.
  *`session.ended` now emitted by the End session button and handled by the
  tracker. A known limit, documented rather than papered over: a session that
  just stops, with no end signal and no further events, still leaves its last
  tracks open — the alternative is a wall-clock timer that would make replays
  differ from the original run.*

- **Touchpoints are real to the system now.** The AR mirrors, scent stations and
  RFID walls configured in the wizard are stored, and an interaction reported
  against one is recorded against the person who used it, with a running count
  per touchpoint. **Nothing is producing those readings yet** — that needs booth
  hardware — so the figure stays a marked zero rather than a number, and the
  report says which kind of zero it is.
  *`(:Surface)` nodes written from wizard touchpoints;
  `(Person)-[:INTERACTED_WITH]->(Surface)` in the graph writer. The count only
  moves when the relationship is newly created, so re-running the pipeline
  cannot quietly hand a sponsor a better number.*

- **A bug the pass-by work exposed:** the sweep that closes out quiet tracks
  skipped anyone who was not inside a zone — which is precisely a pass-by, since
  not going in is the whole signal. It excluded the exact case it now exists for.

- **112 backend tests (was 100), 63 dashboard (was 61).** **Verified by breaking
  it:** disabling pass-by, the session flush, the sweep guard, the touchpoint
  publish or the replay-safe counter each fails its own test. The captured
  backend fixture was re-recorded through the full chain — two visitors, one who
  used the mirror and one who hovered 0.033 away and declined it — and the hand
  count matched the pipeline exactly.

### Fixed — 2026-08-04 — `[neo4j-track]` the attention map is back on the report

- **The heatmap panel should not have been removed.** It was taken off the
  report during the "computed, not asserted" work because it draws from mocked
  positions — but it is a feature that predates this phase, and removing one
  nobody asked to remove is its own kind of defect. It is back, in its original
  place and layout, and the component itself is untouched: same behaviour, same
  empty state on a session with no recorded positions.
  *`viz/Heatmap` restored to `/report`, unmodified. `/live` always kept its own.*

- **The sparklines on the headline figures are back too, drawn from real data
  this time.** They used to be hardcoded arrays. Visitors, average dwell and
  leads captured now each plot their own hourly series from the session's log,
  with quiet hours kept as zeros — skipping them would flatten a lull and make
  a dead afternoon look like a steady one.
  *`hourlyAvgDwell` and `hourlyLeads` in `lib/report/derive.ts`, tested. 61
  dashboard tests (was 58).*

- **Two things deliberately not restored**, because both would mean putting
  invented numbers back on a client's report: the "+18% vs Yday" comparison
  chips, which need a previous session nobody has recorded yet, and the sponsor
  exposure panel's seconds-of-attention figures, which need gaze or
  surface-level dwell that no producer emits. The exposure panel is still there,
  reporting the touchpoint interactions that *are* measured.

### Fixed — 2026-08-03 (night) — `[neo4j-track]` the tracker now knows what counts as a visit

Ported from the `postgres-track`'s browser-side deriver, which had solved this
and we had not. Two tracks, one definition of a visit — the bake-off should turn
on the graph store, not on which one counts better.

- **Somebody standing in a doorway was being counted as dozens of visitors.**
  A person on the edge of a zone crosses in and out at camera frame rate, and
  every wobble was recorded as a complete arrival, stay and departure. One
  loiterer inflated the headcount, dragged the average visit length down, and
  left nothing in the data to suggest anything was wrong. A zone change now has
  to hold for a moment before it is believed.
  *`tracker_zone_confirm_seconds`, default 0.6s. Timestamps use the real
  crossing, not the moment of confirmation, so the wait is not charged to the
  visitor — otherwise every stay in every report would be short by 0.6s,
  systematically, always understating the client's results.*

- **Anyone who walked out of shot while inside a zone vanished from the report
  entirely.** No departure, no stay recorded — the visit was simply dropped. The
  visits likeliest to end that way are the long ones at the far end of a booth,
  so the system was quietly under-reporting exactly the engagement a client is
  paying to prove. Their visit is now closed at the last moment they were seen,
  and marked as such so nothing treats a cut-short stay as a complete one.
  *`tracker_dropout_seconds`, default 20s; `reason: "dropout"` on the exit and
  dwell. In the verification capture, two of eight stays ended this way — before
  this, two of eight stays did not exist.*

- **Clipping the corner of a zone no longer counts as time spent in it.**
  *`tracker_min_dwell_seconds`, default 1.0s. The exit still fires; only the
  stay is dropped.*

- **A separate bug the new tests caught: anyone leaving every zone at once was
  never recorded as leaving.** Walking out of all zones looked identical to
  "nothing has changed", so their last visit stayed open forever.
  *The pending-change check confused "no change pending" with "changing to no
  zone" — both were `None`.*

- **The tests now feed the shape a camera actually produces.** One detection per
  zone used to be enough; it is not, and that is the point rather than an
  inconvenience — a single frame is not evidence of a visit. Every tracker test
  now supplies detections across a stay.
  *Also caught a weak test: the first flicker test passed with the confirm
  window switched off, because single alternating frames never confirm either
  way. Rewritten with paired frames, which is the weakest pattern that does.*

- **100 backend tests (was 95).** **Verified by breaking it:** switching off all
  three rules fails three tests, one per rule. The dashboard's captured backend
  fixture was re-recorded against the new tracker and its hand count redone —
  eight stays, 60.6s average, three entry crossings.

### Changed — 2026-08-03 (late) — `[neo4j-track]` the report now says only what the data supports

- **The report was showing numbers nobody measured.** It rendered for the demo
  session only, and its figures were typed in by hand months ago — 1,287
  visitors, a 4.2× return, four recommendations about a day that never happened.
  Worse, the ROI scorecard used invented economics *even on a real session*, so
  the headline return was fiction no matter what the cameras saw. All of it is
  gone. Every figure on the page now traces to an event in the log or to a
  setting the operator chose.
  *`DEMO`, the hardcoded `activationCost: 12000` / `revenueInfluenced: 50400`,
  and the `catch {}` that turned any error into demo numbers are deleted from
  `RoiScorecard.tsx`. `/report` has one code path for every session.*

- **Blank now means something.** When a number cannot be computed the report
  says so and says why — "no zone is marked as the entry", "set an activation
  cost to compute this" — instead of printing a zero. A client reading a 0 has
  been told nobody came; that is a different claim from "we weren't measuring",
  and running them together is how a broken camera gets reported as a quiet day.
  *Three distinct empty states: never configured, configured but no activity,
  and per-figure "not measurable yet" markers.*

- **Two metrics were defined wrongly and have been corrected.** Footfall counted
  anyone walking into any zone, so a visitor crossing the room counted as extra
  people arriving; it now counts crossings of the entry zone specifically, as
  the ROI framework defines it. And "peak concurrency" only ever knew how many
  people were standing inside a drawn zone — it is now named for that.
  *`reach.entries` (null when no entry zone is configured) and
  `reach.peakZoneConcurrency` in `lib/roi/scorecard.ts`.*

- **The ROI ratio stays blank until someone supplies the revenue.** realmspace
  measures behaviour; it cannot see money until the CRM work lands. So revenue
  and qualified leads are now optional figures the client provides, marked as
  theirs everywhere they appear. Left empty, the ratio reads "—" rather than
  being estimated. Cost per engaged visit *is* fully measured and leads the card
  instead.
  *`Session.revenue_influenced` / `qualified_leads` on the graph node and in the
  wizard; `roi-framework.md` §3 — "we never inflate".*

- **The demo still works, and now it works the same way the product does.**
  Rather than keep a second, fake report for the pitch, the demo session is
  seeded with a day's worth of synthetic visitors. The report then computes them
  with exactly the same code as a live activation. The events are invented and
  labelled as such; no *number* is.
  *`lib/mock/seed-demo.ts` — deterministic (seeded PRNG), identical on every
  run, and pinned to this browser so 140 invented visitors can never reach the
  real append-only log.*

- **"Generate from session data" now does.** The button read mock files and
  random numbers, producing a different summary each press, none of them about
  the session on screen.
  *`ReportGenerator` feeds the real scorecard to the existing template skill.*

- **The operator can add their own read of the day**, kept visually separate
  from every computed figure — a human judgement and a measurement should not
  look like the same kind of claim.

- **58 dashboard tests (was 28), 95 backend.** Including a second captured
  backend response built so the answers can be worked out on paper: three
  visitors, one of whom leaves and comes back, hand-counted to 3 people, 3
  entry crossings, 140s average dwell — and the pipeline reproduces all of it.
  **Verified by breaking it:** restoring the old footfall definition and the old
  revenue handling fails six tests.

### Added — 2026-08-03 (evening) — `[neo4j-track]` the dashboard is plugged into the backend

- **The dashboard had never been connected to this backend.** It looked finished
  — live screen, twin, report — but every number on it came from the browser's
  own memory or from mock files. Set one environment variable and it is now the
  real thing: what the cameras see goes to the server, and what the server works
  out comes back to the screen. Leave the variable out and it behaves exactly as
  before, which is what the laptop demo needs.
  *`NEXT_PUBLIC_BUS_URL`; new `dashboard/src/lib/bus/remote.ts` — WebSocket in,
  `POST /events` out, mirrored into the durable local log every screen already
  reads.*

- **Finishing the session wizard now actually sets the activation up.** Before,
  the zones an operator drew were saved in the browser and nowhere else — so the
  cameras had nothing to measure against and produced nothing at all, silently.
  The wizard also now asks the three questions the ROI report depends on: how
  long counts as engaged, what the activation cost, and which attribution model
  was agreed.
  *`lib/session/publish.ts` → `POST /v1/sessions` on launch, with per-zone ROI
  weight in the zone editor. **If the publish fails the wizard says so and stays
  put** rather than navigating away looking successful.*

- **Losing the connection is now visible.** A dashboard whose feed has quietly
  dropped looks exactly like a quiet booth — the numbers just stop moving, and an
  operator will report a dead afternoon as a slow one. There is a pill in the
  status bar that shows the connection, including when it is fine.
  *`components/chrome/BusPill.tsx`; also counts events waiting to be sent.*

- **Nothing is lost if the network drops mid-activation.** Events the dashboard
  produces are written down locally first and sent afterwards, oldest first, from
  where it left off. Same property the camera script already had, for the same
  reason: an event is given its identity when it happens, not when it is sent, so
  a resend after a timeout is recognised rather than counted twice.
  *`flushOutbound` walks a cursor over the existing durable log — no second
  outbox. Verified: an outage mid-flush keeps the cursor on the last event that
  landed, and the rest go in order on reconnect.*

- **A mismatch that would have quietly corrupted every report, found and fixed.**
  The backend labels the fields inside an event one way (`duration`, `anon_id`)
  and the dashboard reads them another (`durationSec`, `anonId`). Nothing would
  have errored. The report would simply have shown plausible, wrong numbers —
  the one thing `roi-framework.md` says we must never ship.
  *New `lib/bus/wire.ts` translates at the boundary, both directions. The spec's
  snake_case is shared with the Python producers and the other track, so this
  side bends. There is a test that feeds the untranslated payload in and asserts
  it produces `NaN` and a phantom visitor, so the danger stays documented.*

- **28 dashboard tests, against real backend output.** The fixture they run on is
  a verbatim capture of `GET /events` from a running backend after a real
  configure → detect → track cycle — not a handwritten guess at what it sends.
  The day a payload key is renamed on the Python side, this fails instead of a
  report losing a layer three weeks later. **Verified by breaking it:** removing
  the translation fails eight tests, including both idempotency ones.
  *First test runner in the dashboard (vitest + jsdom); `npm test`.*

### Added — 2026-08-03 — `[neo4j-track]` you can now set an activation up before it runs

- **Nothing could tell the system where the zones were.** The code that draws a
  zone in the database existed, but the only thing that ever called it was the
  test suite. So on a real machine the tracker asked which zones it should be
  watching, was told "none", and quietly did nothing — no zone entries, no dwell,
  no numbers, and no error either. Everything Phase 2 measures sits downstream of
  that, so it had to be closed before any of it could start.
  *`POST /v1/sessions` — the first production caller of `graph.repository`'s
  `upsert_session` / `upsert_zone`. Registered in `app/main.py`; new
  `app/routers/sessions.py`.*

- **The numbers a report divides by are now decided before the doors open, not
  after.** How long counts as "engaged", how much each zone is worth, what the
  activation cost, and which attribution model was agreed — all set at setup and
  stored with the session. A number picked after the results are in is a number
  picked to make the results look good, and this is the wedge we sell on.
  *`Session.engaged_threshold_seconds` / `activation_cost` / `currency` /
  `attribution_model` and `Zone.weight` / `funnel_order`, per `roi-framework.md`
  §5. Defaults deliberately match `dashboard/src/lib/roi/scorecard.ts` (60s,
  weight 1.0) so the same session scores the same on both sides.*

- **Editing a zone mid-activation now takes effect straight away.** It used to
  take up to half a minute, during which people's time was credited to the shape
  the zone used to be.
  *New `session.zones_updated` event; the tracker subscribes and drops its
  polygon cache. This is what the TODO in `tracker.zones_for` asked for. Sent
  through the bus rather than called directly, because the tracker can run in its
  own process — where a direct call reaches nothing. The 30s TTL stays as a
  backstop for zones changed by something that doesn't emit the event.*

- **A zone the operator deletes actually disappears.** Left behind, it would have
  kept collecting time against a shape nobody could see, and shown up in the
  client's report.
  *`prune_zones` — re-posting the zone set removes what isn't in it.*

- **The camera cannot change how it is scored.** A device key is refused here,
  and so is a read-only viewer; only an admin or operator can configure an
  activation. The camera key is the credential most likely to leave a venue in
  somebody's pocket.
  *New `require_operator` dependency in `app/auth/principal.py`.*

- **Two mistakes that would have been invisible are now refused at the door.**
  A zone drawn in pixels instead of normalized coordinates, and a "polygon" with
  two points. Both are accepted silently by the database and then match nobody,
  forever — the report just shows less traffic than there was.
  *422 with the reason, in `schemas.ZoneConfig`. Duplicate zone ids too: MERGE
  would have collapsed them and lost one of the operator's zones.*

- **15 new tests, 94 passing.** The one that matters runs the real path —
  configure through the API, feed a detection, get a spatial event — because
  every existing test seeded zones by hand, which is precisely what hid the
  problem. **Verified by breaking it:** removing the cache-invalidation
  subscription fails the redraw test, as it should.

### Added — 2026-08-01 (midday) — `[neo4j-track]` the database now enforces tenant separation itself

- **Until now, one client's data stayed separate from another's because the code
  always remembered to keep it that way.** That held — but it was one forgotten
  line away from not holding, and nothing would have said so. The database now
  refuses on its own: a connection that hasn't declared which client it is
  working for sees **nothing at all**, and one working for client A cannot reach
  client B's data even by asking for it explicitly in raw SQL.
  *Postgres row-level security on `event_log`, `consumer_cursor` and
  `dead_letter`, forced so it applies to the table owner too. This is what
  `multi-tenant.md` §2 asked for by name — "enforced at the DB layer, not just
  the app".*

- **The application now connects with fewer privileges than it had.** It can
  read and write data and nothing else — it cannot create or alter tables, and
  crucially it is not a superuser, because a superuser ignores these rules
  entirely. Migrations still run with full rights, as a separate connection.
  *Verified before designing any of it: with a superuser, enabling the policies
  changed nothing at all.*

- **Failure notes get scoped too.** The queue of events the system couldn't
  process stores a full error trace, which can quote the original event — so it
  was a small leak between clients. It now records which client it belongs to,
  which the human review screen planned for a later phase needs regardless.

- **The existing 68 tests were re-run under the new restrictions**, which is the
  real check: anything in the codebase quietly relying on unrestricted access
  breaks here. Three places did, all now fixed — the tracker and the live feed
  each open their own database connection and had to declare a client, and one
  test helper was reading another client's progress.

- **Verified by trying to break it**, not by reading the policies: granting the
  application the bypass privilege fails seven tests; removing the "applies to
  owners too" setting fails one written specifically because no ordinary test
  can detect that.

- **A trap worth naming.** The client is recorded per *transaction*, not per
  connection. Connections are reused, so a per-connection setting would let one
  request inherit the previous request's client and be served their data — a
  leak created by the fix for leaks. There is a test that fails if that ever
  changes.

- **This covers the event log, not the graph.** Neo4j's free edition has no
  equivalent feature, so that half stays enforced by application code. Closing
  it needs a paid licence or a different store, and that decision is recorded
  rather than quietly deferred.


### Added — 2026-08-01 (morning) — `[neo4j-track]` one command and you have a backend

- **You can now run the whole backend without installing anything.** Two lines
  in a config file and `docker compose up` gives you the event log, the graph
  and the API, with the database schemas already applied. **Cold start to
  everything-ready: 13 seconds**, measured, against a 60-second target in the
  product spec. Previously this took installing two databases and a Java
  runtime, setting a password by hand, creating databases, building a Python
  environment and running two separate migration tools.
  *`Dockerfile`, `docker-compose.yml`, `docker/entrypoint.sh`. The entrypoint
  waits for both databases, applies the Postgres migrations and the graph
  schema, then serves — and is safe to re-run, so a restart is not a special
  case.*

- **It does not fight the setup you already have.** The databases are published
  on unusual ports on purpose, so a machine that already runs Postgres and Neo4j
  the manual way can run both at once. The alternative — "stop your services
  first" — turns a one-command boot into three and breaks a working setup every
  time.

- **It refuses to start without its secrets rather than inventing them.** No
  default signing key ships in the file; compose stops with a message telling
  you which value is missing. A default secret is a token anyone can forge.

- **Qdrant is deliberately left out**, though the spec lists it. Nothing uses it
  yet — it is for a search feature that does not exist — and a service every
  boot has to wait on makes the speed target harder for no benefit. Noted in the
  spec next to the line it deviates from, rather than left as a silent
  disagreement.

- **The health endpoint lied to Docker.** It reported "degraded" in the body
  while still returning a success status code — and Docker decides purely on the
  code. So a container with a dead database or a crashed background worker
  advertised itself as healthy, hiding exactly the silent failure that endpoint
  exists to surface. It now returns 503 when degraded; confirmed by killing the
  graph database under a running stack and watching Docker mark the container
  unhealthy, which it previously never did.

- **Three things only surfaced by running it**, which is why it was run: the Neo4j
  image treats every `NEO4J_*` variable as a configuration setting, so passing
  the password in the obvious way made it refuse to boot with a confusing error;
  and an error message containing a colon quietly broke the file's syntax. Both
  are the sort of thing that reads fine and fails for whoever tries it next.


### Added — 2026-08-01 (early) — `[neo4j-track]` Phase 1 complete: the camera feeds the system

- **A real webcam now produces real rows.** Point the perception script at the
  backend and every person it sees becomes an event in the permanent log, then a
  node in the graph — the loop the whole phase has been building toward. Run it
  with `--bus-url` and a device key; leave them off and it behaves exactly as it
  did before, printing to the terminal.
  *`perception/bus_client.py` wired into `realmspace.py`. Detections carry the
  frame dimensions the tracker needs to place someone in a zone.*

- **Unplugging the network costs nothing.** If the backend goes away mid-session
  the script keeps running and writes events to a local file instead. When the
  connection comes back they are sent oldest-first, and the log recognises them
  as things it already has rather than recording them twice. Tested by actually
  doing it: 11 events posted across an outage produced 11 rows, all distinct, in
  order.
  *The event's id is assigned when it happens, not when it is sent — that one
  detail is what makes a reconnect idempotent instead of duplicating everything
  buffered during the drop. Guarded by a test that fails if the id is minted
  later.*

- **A flaky connection can't scramble the queue.** Replay stops at the first
  failure and leaves the rest in order rather than draining past it, and a
  half-written line from a hard power cut is skipped rather than jamming the
  buffer forever.

- **Built on POD 1's work rather than around it.** The offline client was ported
  from the other track, which had already got the hard parts right, then given
  authentication and the missing frame fields. `realmspace.py` is a file both
  tracks share — it should not fork.
  *Our backend also learned the other track's `/v1/events` path and accepts
  either `anon_id` or `person_id`, so one script and one URL work against either
  backend. The comparison should turn on the graph store, not on whose spelling
  the camera happened to use.*

- **This closes Phase 1.** Event bus, graph store, consumers, live WebSocket,
  perception with offline replay, and authentication are all in.


### Added — 2026-07-31 (overnight) — `[neo4j-track]` the backend stopped trusting whoever asks

- **Until tonight, anything that could reach the backend could read any client's
  data.** The tenant was simply whatever the caller typed in the URL — so on a
  venue's wifi, someone could have pulled a client's entire event log, written
  fake events into it, or watched their live feed, by guessing a name. That is
  now closed: every request must carry a credential, and **which client's data
  you get is decided by that credential, not by what you asked for.**
  *The `tenant_id` query parameter is deleted rather than validated. A check you
  have to remember to write is a check somebody eventually forgets; a parameter
  that does not exist cannot be forged. `backend/app/auth/`.*

- **Cameras and people log in differently, because they have to.** A person gets
  a token by signing in. A camera, an RFID reader or a kiosk gets a key, because
  none of them can type a password — and those keys can only *write* events,
  never read them back. A key taped inside a booth kit is the credential most
  likely to walk out of a venue, so it carries as little as possible.
  *`Authorization: Bearer <jwt>` for humans, `X-API-Key` for devices. Both
  verified locally with no network call — `event-bus-spec.md` §1 requires the
  edge box to keep working when the conference wifi does not, and checking a
  Firebase token needs Google's servers.*

- **A producer pointed at the wrong client is told immediately.** Sending an
  event for a tenant your credential does not cover is refused outright rather
  than quietly re-filed under the right one — the second would be discovered
  weeks later as a hole in a report.

- **Verified by trying to break it**, not by reading the code: unauthenticated
  requests, forged signatures, expired tokens, revoked keys, a token signed with
  the wrong secret, and a deleted user's still-valid token are each rejected,
  with a test apiece. Live latency re-measured with signature checking in the
  path — **60ms typical**, unchanged against the 500ms target.

- **Still to do, and worth being plain about:** the login endpoint currently
  believes the email address it is handed, which is fine on a laptop and is not
  authentication. Wiring it to the Firebase sign-in the dashboard already has is
  the next step. And database-level isolation (Postgres row security) is not on
  yet — today the guarantee is enforced by the application.

### Added — 2026-07-31 (late) — `[neo4j-track]` live events reach the browser

- **The dashboard can now watch what the backend sees, as it happens.** Open a
  connection and every event for that activation arrives the moment it lands —
  a person entering the entrance, a dwell finishing, anything. Measured at
  **58ms typical and 175ms worst case** from the event arriving to it appearing
  at the far end, against a 500ms target. Measured against a real running
  server, not estimated.
  *`WS /v1/ws/{tenant_id}/{session_id}` (`backend/app/routers/live.py`), an
  in-process fan-out hub (`app/hub.py`), and a `broadcast` consumer that reads
  the log and pushes — so socket delivery is a bus consumer like everything
  else, not a hook wired into the write path.*

- **Reconnecting after a wifi drop picks up exactly where it left off.** The
  client says which event it last saw; it gets what came after, and nothing it
  already had. On a conference network that is the difference between a clean
  reconnect and a browser re-processing the whole session.
  *`?since_seq=N` is a real cursor into `event_log`, the same discipline the
  consumers use, extended to the last hop. The other track replays from zero
  capped at 200 on every connect — worth comparing.*

- **The socket speaks the shape the dashboard already expects.** The event
  contract the browser declares as canonical uses `eventId`/`tenantId` and
  millisecond timestamps; the backend had been emitting `event_id` and date
  strings. Fixed now, while nothing depends on it, so POD 3 writes one
  integration rather than a translation layer per backend.
  *`EventOut` gains a camelCase alias generator and ms-epoch serialisers, per
  `dashboard/src/lib/contracts/events.ts`. Producers may still POST snake_case.*

- **A closed tab can't take the live view down for the rest of the booth.**
  Dead connections are dropped from the fan-out rather than raising.

- **Unblocked the RFID reader work before it started.** `rfid.read` was in no
  contract anywhere, and the event validator would have rejected it — whoever
  picked up that task would have hit a flat refusal from the API with nothing
  explaining why.
  *Added to `event-bus-spec.md` §3 and to the accepted namespaces. Flagged that
  `contracts/events.ts` still needs the matching type.*

- **Still not authenticated.** The tenant comes from the URL, so anyone who can
  reach the process can watch any activation's live feed by guessing an id.
  Same as the other track today. Localhost only until the auth item lands.

### Added — 2026-07-31 (early) — `[neo4j-track]` the two halves finally meet

- **The system now turns "a camera saw a person" into "someone visited the
  Entrance and stayed 50 seconds."** Until now the backend had a log nothing read
  and a graph nothing wrote. Two background workers close that gap: one watches
  the log and works out zone entries, exits and dwell times; the other turns those
  into the actual graph of who-went-where. Post a detection to the server and a
  moment later the graph has a person, a zone, and the edges between them.
  *`backend/app/consumers/`: `tracker.py` derives `spatial.zone_enter` /
  `zone_exit` / `dwell` and appends them back onto the bus as an ordinary
  producer; `graph_writer.py` projects those into `Person`/`Zone` nodes and
  `ENTERED`/`LEFT`/`DWELLED_IN` edges. Both run as asyncio tasks off the FastAPI
  lifespan and are reported by `GET /health`.*

- **Re-running the whole pipeline changes nothing — proven, not assumed.** If the
  server crashes halfway through, or someone deliberately rewinds it to replay a
  session, it reprocesses everything and produces not one duplicate event, person
  or edge. That property is what makes the "replayable" claim in the bus spec real
  rather than aspirational, and it is what stops a single dwell being counted
  twice in an ROI report.
  *Derived event ids (`consumers/ids.py`) make re-emission a no-op against the
  log's `UNIQUE(event_id)`; `Consumer.on_replay` clears derived in-memory state on
  a cursor rewind. Both verified by deliberately breaking them and confirming the
  guarding tests fail.*

- **A bad event can no longer jam the queue.** An event the workers cannot process
  is retried, then set aside in a "dead letter" list with the reason, and the
  queue moves on. Previously one malformed message would have blocked everything
  behind it.
  *`consumers/base.py` holds the single poll → handle → advance → dead-letter
  loop; failures land in `dead_letter` with the traceback and the cursor advances
  past them (HITL review screen is Phase 3).*

- **The edge and the browser now agree on what "inside a zone" means.** The zone
  geometry was ported from the dashboard rather than rewritten, so a person can't
  be in the Lounge on screen and the Atrium in the report.
  *`consumers/zones.py` is a line-for-line port of
  `dashboard/src/skills/zone-detect.ts`; the 30s dwell threshold matches
  `agents/definitions/dwell.ts`.*

### Fixed — 2026-07-31 (early) — `[neo4j-track]` review pass over Phase 1

- **Redrawing a zone mid-session now actually takes effect.** The tracker cached
  zone shapes the first time it saw them and never looked again, so an operator
  moving a boundary would have kept getting dwell attributed to the old one, with
  nothing to indicate anything was wrong.
  *30s TTL on the zone cache in `consumers/tracker.py`; `forget_zones()` existed
  but was never called.*

- **Four other bugs found before they could bite:** memory that grew for every
  visitor and never shrank; a database write per camera frame, which would have
  been the first thing to miss the sub-500ms target; event types accepted with no
  checking, so a mistyped one would be stored forever and silently never
  processed; and an error endpoint that told anyone asking which internal
  component had failed.
  *Bounded tracker state with oldest-first eviction; one commit per batch instead
  of per event; `type` validated against the `event-bus-spec.md` §3 namespaces;
  `/health` logs detail and returns `"error"`. Each has a test that fails against
  the old code.*

- **The status docs told the truth again.** `architecture.md` was named for the
  whole system but documented only the dashboard; the README and PRD both still
  said the graph store and consumers were unbuilt.

### Added — 2026-07-30 (early) — `[neo4j-track]` the graph store

- **The product's actual subject matter now has somewhere to live.** Sessions,
  people, zones and the relationships between them — who entered what, who
  lingered where — now have a real store with rules the database enforces, rather
  than being implied by a list of events.
  *Neo4j, schema and constraints transcribed from `data-model.md` into
  `backend/app/graph/schema.py`. Eight node types constrained; `Frame` has no id
  to key on, which is now flagged in the doc.*

- **One tenant's data cannot leak into another's, by construction.** Every key in
  the graph starts with the tenant, and every single query the system can make is
  required to name one — there is no way to write a query that reads across
  tenants, because all the query code lives in one place that demands it.
  *All Cypher confined to `backend/app/graph/repository.py`, `tenant_id` a
  required argument on every function.*

- **Known limit, stated rather than buried:** the free edition of Neo4j cannot
  enforce that separation itself — no row-level security, and it will happily
  store a record with no tenant on it. Application code is the only thing
  enforcing it today. Fine while a kit runs one client's activation at a time; a
  real gap once tenants share a server.
  *Verified against the running instance (`51N27: property existence constraint is
  not supported in community edition`). Recorded in `data-model.md` →
  "Store decision".*

- **Database structure is versioned, not typed in by hand.** Anyone cloning the
  repo runs one command and gets an identical graph schema; running it twice does
  nothing.
  *`app/graph/migrations.py` — Alembic's idea in ~60 lines, since Neo4j has no
  equivalent; applied versions recorded as `(:_SchemaVersion)` nodes.*

### Added — 2026-07-28 (night) — `[neo4j-track]` the durable event log

- **The system stopped forgetting.** Everything that happens is written down in
  order, permanently, and can be read back or replayed later. Close the process,
  restart it, and the record is still there.
  *`backend/` — FastAPI + Postgres, `event_log` schema transcribed by hand from
  `event-bus-spec.md` §2 so it provably matches the spec. `POST /events`,
  `GET /events?tenant_id=…&since_seq=…`.*

- **A camera that loses its connection can safely resend.** If a message times out
  the sender genuinely cannot tell whether it arrived, so it sends again — and the
  log recognises it as the same event and does nothing, rather than recording the
  same person twice.
  *Idempotent on a producer-assigned `event_id` via `ON CONFLICT DO NOTHING`;
  duplicates return the original `seq` with `200` rather than `201`.*

- **Proven against a real database, not a stand-in.** The tests run against actual
  Postgres, because the thing being tested is whether Postgres behaves as the spec
  claims — a mock would only replay our own assumptions.

### Known gaps in `[neo4j-track]` as of 2026-07-31

Stated plainly so the two tracks are compared honestly, not on impressions:

- ~~No authentication~~ — landed 2026-07-31 (overnight). Application-enforced;
  database-level row security is still to come.
- **Perception is not wired in.** `realmspace.py` still prints to stdout; no
  offline buffer yet.
- ~~No WebSocket bridge~~ — landed 2026-07-31 (late). The dashboard still has to
  be pointed at it (POD 3's job).
- **No gaze, group or pass-by events** — gaze needs pose data perception doesn't
  emit, pass-by is Phase 2.

<!-- entries below this line are the postgres-track and the pre-split work -->


### Added — 2026-07-29 (night) — Phase 2 linchpin: the spatial-event deriver

- **The system now knows when someone actually visits a zone, not just that a
  camera saw a person somewhere.** Until tonight, nothing turned raw camera
  detections into "entered the display," "stayed for 40 seconds," "left." That
  translation — the thing the whole ROI report depends on — now runs live in
  the browser next to the existing tracker, using the same zone maps drawn
  during onboarding. It emits directly onto the durable bus, so the graph and
  (soon) the scorecard see real zone activity the moment a session runs.
  *New `dashboard/src/lib/live-session/spatial-deriver.ts`; wired into
  `lib/live-session/store.ts` (`ingestStats` + `resetSession`) and
  `detector-runtime.ts`; emits `spatial.zone_enter`/`zone_exit`/`dwell`/
  `passby` via the existing `emit()` path. `backend/app/graph_writer.py`
  already projects enter/exit/dwell into graph edges — no backend change
  needed.*
- **Built-in noise filtering, so a flaky camera frame doesn't invent a fake
  visit.** A zone change only counts once it holds for 600ms (kills boundary
  flicker), visits under a second are dropped as noise, and if someone
  disappears mid-visit the exit is flagged `"dropout"` so later reporting can
  discount it. Ending a session closes out anyone still "inside" a zone so
  the last few visits of the night aren't silently lost.
  *Confirm-window state machine + `flushAll("session_end")`; timestamps for
  dwell duration use the real crossing time, not the delayed confirm time, so
  durations stay accurate despite the anti-flicker delay.*
- **A one-frame camera hiccup no longer looks like someone leaving.** The
  detector already tolerates brief missed frames; the new zone logic now sees
  that same tolerant track list instead of only the strictly-confirmed one, so
  it doesn't misread a hiccup as a dropout.
  *`DetectorStats.tracks` added alongside `activeTracks`
  (`lib/live-session/types.ts`), threaded through both detector runtimes
  (`detector-runtime.ts`, `components/viz/WebcamDetector.tsx`).*
- **Checked by hand before trusting it**: 34 scripted scenarios (entry
  confirmation, dwell accuracy, flicker suppression, dropout flagging, passby
  radius, zone switching, sub-second-dwell rejection, session-end flush,
  unconfirmed-track filtering) run against the real deriver source in an
  isolated harness — all pass.

### Fixed — 2026-07-29 (night) — dashboard lint/type hygiene

- Cleared every `eslint` error in the dashboard (7 → 0): three components were
  reading `Date.now()`/`new Date()` directly during render or calling
  `setState` synchronously inside an effect body, both of which React's newer
  purity rules correctly flag as unstable. Fixed with the proper idiom in each
  case — a ticking clock seeded once and only ever updated from an interval
  callback (`StatusBar.tsx`, `live/page.tsx`), an initial-state computed from
  the subscription target instead of set inside the effect (`AuthProvider.tsx`),
  and a `useSyncExternalStore`-based hydration hook replacing a `mounted`
  state+effect pair (`TrafficChart.tsx`, new `hooks/useHydrated.ts`). One
  remaining flag (`PrefabSelector.tsx`) is a false positive — the call is
  inside an `onClick` handler, never render — and is suppressed with a
  one-line, reasoned `eslint-disable`.
  *Also: a stale `useMemo` dependency masking direct state reads
  (`agents/page.tsx`), two unused imports, one `let`→`const`, and a missing
  `useMemo` dependency pair in the twin's zone geometry (`TwinScene.tsx`).*

### Added — 2026-07-27 (night)

- **The research brain is now part of the repo.** A reusable "analyst" skill
  turns dumps of links and papers into dated, written deep-dives stored in
  `docs/research/` — each one checked against what we've actually built, not
  just summarised. The first one analysed a CHI '26 academic paper plus ~40
  links and found: strong independent validation of the whole product idea, a
  big competitor (Cvent) moving toward our benchmark story, and proof that
  "we don't store images" is no longer a unique claim.
  *`~/.agents/skills/realmspace-analyst/SKILL.md` (global skill);
  `docs/research/2026-07-27-chi26-digital-twins-and-cv-dump.md`.*

### Changed — 2026-07-27 (night) — next sprint prepped from research findings

- **Privacy now reads like a regulator wrote it.** The privacy doc explicitly
  cites the UK ICO's biometric-recognition guidance and explains, in
  compliance vocabulary, why realmspace sits outside that strict regime —
  plus visitors can now opt out even *after* their visit.
  *`docs/privacy.md`: ICO section + post-visit opt-out.*
- **The competitor map got four new rows and a sharper moat.** Added the
  privacy-safe occupancy category (XY Sense, Cisco Spaces), event-platform
  incumbents (Cvent — flagged claiming our cross-event benchmark moat),
  lead-capture apps (Popl — integration target), and loyalty wallets
  (Delphize — monitor). Our #2 moat is reworded from "privacy" to the
  consent-gated identity bridge, since no-images is now table stakes.
  *`docs/competitive-landscape.md`.*
- **Phase 2 absorbed the academic lessons.** Two new task lines: session-hygiene
  heuristics (the CHI paper threw away 71% of raw sessions — ours won't poison
  the metrics) and per-touchpoint-type metric normalisation (comparing unlike
  exhibits fairly — the problem academia couldn't solve). The ROI framework
  now also speaks the museum buyer's vocabulary: *attracting power* and
  *holding time*.
  *`docs/roadmap.md` P2; `docs/roi-framework.md` Layers 1–2.*
- **Museums are now a named sales target.** The 50-email validation sprint
  reserves 8–10 slots for museum/cultural-institution leads, and the demo
  Loom script cites the CHI '26 paper as independent proof.
  *`docs/gtm.md`; `docs/README.md` research index; `AGENTS.md` handoff updated.*

### Changed — 2026-07-27 (late evening)

- **The plan grew teeth.** We merged a detailed 6-week team plan (the "pod
  roadmap") into our own roadmap without changing ours at its core: proving ROI
  to clients still comes second, right after the spine. Everything the pod plan
  added — RFID tag reading, a rules engine that pings Slack in under 3 seconds,
  consent capture flows, CRM sync, calibration tools, and quality targets — is
  now slotted into the right phase with notes on what depends on what. We also
  caught three things the pod plan forgot: nothing in the system yet turns raw
  camera detections into zone enter/dwell events (now the first task of Phase
  2), retried actions could post to Slack twice (dispatchers now dedupe), and
  six event types needed later are now registered up front so nobody has to
  rework the schema mid-build.
  *`docs/roadmap.md` rewritten: pod-week refs (W1–W6) annotated per phase;
  spatial-event deriver added as P2 linchpin; contract additions
  (`rfid.read`, `spatial.tagged`, `intent.scored`, `drift.detected`,
  `calibration.updated`, `crm.retract`) queued for P3; ADR-002 (rule spec as
  JSON, edge fires / browser previews) + auth unification + NLQ-templates-vs-
  Cypher added to open decisions; standing SLO table added; AGENTS.md updated.*

### Added — 2026-07-27 (evening)

- **The camera script no longer loses data when WiFi drops.** If the backend is
  unreachable, the Python perception script writes events to a small file on disk.
  When the backend comes back, it sends the saved events first — oldest first —
  then carries on live. Verified with an automated test: kill the bus, buffer 2
  events, restart, all 3 arrive in the right order and appear in the graph.
  *Offline buffer + ordered replay in `perception/realmspace.py` (`--buffer-file`,
  default `.bus-buffer.jsonl`); replay happens before each new post.*

- **The live dashboard numbers are now real even after you close the tab.** The
  "Unique today", session length, and traffic chart on `/live` used to fall back to
  fake numbers (or zeros) when the camera wasn't running. Now they're computed from
  the permanent event record — the same record the backend keeps — so a recorded
  session still shows its true numbers, and they survive a page reload.
  *New `useSessionBusStats` hook derives KPIs from the durable local log (which
  mirrors the remote bus over WebSocket) and polls `/v1/graph/{tenant}/{session}`
  so "unique visitors" is the graph's own Person-node count. Wired into the four
  KPI tiles + traffic panel on `/live`.*

### Changed — 2026-07-27 (evening)

- **Phase 1 is done.** The spine — camera → event → stored → graph → dashboard —
  works end to end on the laptop with zero cloud. Postgres is a deploy-time choice
  now, not a build task.
  *All Phase 1 roadmap checkboxes ticked in `docs/roadmap.md`.*

### Added — 2026-07-27 (earlier)

- **The system now has a memory.** Before, the dashboard forgot everything when you
  closed the tab. Now there's a small server that writes down every event (person
  entered, person left, dwell) in order, forever, and can replay them later.
  *ADR 001 — graph store decision: relational node/edge tables in the same SQL DB as
  the event bus (SQLite locally / Postgres when available). Neo4j/AGE deferred until
  Ask needs native Cypher (`docs/adr/001-graph-store.md`).*

- **A real backend exists now.** It's a small FastAPI service that receives events,
  stores them, builds the graph of who-dwelled-where, and streams live updates to the
  dashboard over a WebSocket. It also knows who you are (basic roles: admin, operator,
  analyst, viewer).
  *Edge API (`backend/`): append-only event bus, graph writer consumer, graph snapshot
  API, WebSocket fan-out, and RBAC skeleton (`GET /v1/auth/resolve`).*

- **It works without the cloud.** The whole thing runs on the laptop with a simple
  built-in database file — nothing to install beyond Python. Postgres is ready for
  when we want it (one `docker compose up` away).
  *SQLite default store for zero-ops local/edge runs; Postgres schema +
  `docker-compose.yml` included.*

- **The Python camera script can talk to the backend.** Run it with `--bus-url` and
  every person it sees gets posted to the server as a proper event, instead of just
  printing to the terminal.
  *Perception → bus: `realmspace.py --bus-url http://127.0.0.1:8000` posts
  `session.*` and throttled `perception.detection` RealmEvents.*

- **The dashboard can talk to the backend too.** Set one environment variable and the
  dashboard sends its events to the server, listens for the server's events, and shows
  a little pill in the status bar so you know the connection is live.
  *Dashboard remote bus bridge: `NEXT_PUBLIC_BUS_URL` enables dual-write from
  `emit()`, WebSocket replay/mirror into the local durable log, and a StatusBar bus
  pill.*

- **Live camera detections are now real events.** When the browser tracks a person,
  that detection is written to the permanent log — locally, and to the server when
  it's configured.
  *Live detector emits `perception.detection` onto the durable bus (local + remote
  when configured).*

### Changed — 2026-07-27

- **Phase 1 of the roadmap moved forward** — the spine of the product (bus + graph +
  live connection) is mostly in place.
  *Roadmap Phase 1 checkbox updates in `docs/roadmap.md`.*

## [0.1.0] — 2026-07-22, 11:00 +0100 (prior prototype)

- **The demo dashboard.** Six screens (landing, live camera tracking, 3D twin, ask,
  agents, report) — the live camera tracking was real, everything else used fake data
  that matches the shape of real data.
  *Next.js dashboard, client-side durable event log + contracts + multi-tenant
  context + ROI scorecard, Phase-0 YOLO perception stub (stdout JSON).*
