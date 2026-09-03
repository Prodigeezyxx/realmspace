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

## [Unreleased] — last updated 2026-09-03

### Added — 2026-09-03 — `[neo4j-track]` A visitor can say yes at the stand

Everything that happens after somebody gives us their details has been built for
months: the record of what they agreed to, the link to where they walked, the
lead, the five CRMs it can be delivered to, the follow-up draft, the audit
trail. There was no way for anybody to actually give them. A consent could only
be put into the system by hand, on the command line, by us.

There is a kiosk now. An operator prints a QR code from the activation's
settings; a visitor points a phone at it and gets one screen — the wording the
operator wrote, a box for their email, and two buttons. Agreeing puts a real
consent into the system, which becomes a real lead a few seconds later. Saying
no records nothing at all: the anonymous measurement was never about them by
name and carries on regardless, which is what the privacy note has always
promised.

**The wording is the operator's, and the version follows it.** Change a word in
the wizard and the version changes on its own, so every consent can be traced to
the exact sentence that was on the screen when somebody read it. A kiosk cannot
be handed out at all until the wording is set — the refusal is at the moment of
minting rather than in front of a visitor at the stand.

**What the kiosk cannot know.** It has no camera, so it cannot say which of the
people in the room agreed. When exactly one person is standing at the plinth the
consent is joined to their path; when two are, or none, the consent is still
recorded and the path is simply not claimed. That is the opposite of the choice
the touchpoint tablets made in August, and deliberately: a tap is a claim about
somebody the device cannot see, but a consent is what the visitor said about
themselves. Throwing it away for want of knowing where they walked would mean a
person handed over their details and heard nothing back.

**It keeps working when the network does not.** The stand's wifi is worst when
the stand is busiest. A consent given during an outage is held on the phone and
sends itself when the network returns, exactly once — and a kiosk *opened*
during an outage still shows the wording it last had, so the plinth by the door
does not go blank because a server is restarting.

**And an erasure reaches it.** The email a visitor types is on the log from the
moment they submit, before anything has processed it — and for a consent nobody
could attribute, that is the only place it ever appears. Asking to be forgotten
now clears that copy too, from the reference on the visitor's own receipt.

*`consent.given` pinned in `event-bus-spec.md` §3 and classified PII in both
halves of the system. `POST /v1/kiosk/{token}` and `…/consent`
(`routers/kiosk.py`) take the tenant, session, surface, tier and copy version off
the token row and the activation, never from the request, so a leaked QR code
cannot claim a tier nobody was shown. Migration 0016's `consent_token` is its own
table rather than a column on `surface_token`, so one printed credential cannot
also post taps. `consumers/kiosk_consent.py` resolves the visitor through
`occupancy.occupants_at` — one definition of who is in a room, shared with the
touch and crowding consumers — and waits on the tracker's cursor rather than
guessing, the shape `consumers/touch.py` uses. The capture it emits derives the
same `event_id` `POST /v1/consent` would, so a replay re-derives one consent
rather than doubling the identification. `consent.given` joins `PII_TYPES` and
`LINKING_TYPES` in `app/erasure.py`, which the share link's redaction and the
retention purge inherit. `/consent` and `lib/consent/useKiosk.ts` in the
dashboard, `KioskPanel` beside the tablet panel on the calibration screen, the
wording on the wizard's privacy step with `copyVersionFor` deriving the version.
Walked against a live stack: a seeded walk-in, a consent at the kiosk,
`consent.given` → `consent.captured` → `identity.resolved` → `handoff.lead` and
an `IDENTIFIED_AS` edge in the graph; a second consent with two people at the
desk recorded and unattributed; a revoked link refused with one sentence; and an
erasure by consent id alone leaving no part of the name in any of the three
events. The outage was walked in the browser, which is where the wording cache
came from — before it, a kiosk opened during an outage showed "Opening…" for
ever.*

### Added — 2026-09-02 — `[neo4j-track]` An operator can write a rule, and change one

The Agents screen had a **New rule** button that did nothing. It had never done
anything: an operator could arm one of the ready-made rules, watch it fire and
delete it, and that was the whole of it. Changing what a rule says — the message
the floor staff see, the number of people it waits for — meant somebody editing
the database by hand.

There is a form now. Every part of a rule is on it: what to watch for, in which
zone, how many people and for how long, what to do about it, and how long to wait
before saying it again. It reads the rule back as a sentence before anything is
armed — *"when 5 spatial.dwell events of at least 30s in z_entry within 60s,
prompt staff: 'Open the second door'"* — and says how often it **would have**
fired against what this activation has already seen, without dispatching
anything. The pencil beside a rule opens the same form with that rule in it.

**And you can describe one in a sentence.** "Tell staff when the Entry Arch is at
capacity" comes back as a document with the fields filled in, which the operator
then reads, changes and arms. It is never armed for them.

**What the composer is not allowed to decide.** It cannot pick a Slack channel, a
webhook address or a screen — those send an activation's data somewhere, and
somewhere is a choice a person makes. Ask for one and you get the hard half, the
condition, plus a line saying the destination is yours to enter. It cannot name a
zone this activation does not have, and it cannot choose a rule's id, because a
composed rule that reused one would quietly replace the rule already armed under
it.

**Two things the walk found that the tests had not.** A composed rule could not
be saved at all — the ready-made shapes never gave the rule a *name*, and the API
requires one, so every one of them came back a 422. The tests had checked the
fields of the document and never put a whole one through the validator that
guards the write. And the browser's own error line turned out to be telling the
truth about something else: a stale backend from an earlier session was still
answering on the port the dashboard is configured for.

*`POST /v1/rules/compose` behind the same `require_rule_author` as the write —
provider, budget and metering as `routers/ask.py` does them, with `compose` added
to `LlmSpender`. The model returns a document validated by the same `RuleIn`
members `PUT /v1/rules` enforces; `schemas.ComposedRule` narrows the action union
to `staff_prompt | log`, so an outward destination is unrepresentable rather than
caught, and the tenant and id are never read from its output. `app/llm/
rule_shapes.py` is the deterministic floor and the "what I can build" list a
refusal carries, scored the way `stub.py` scores a question and refusing on a tie
for the same reason. `llm.base.unfenced` moved out of `routers/ask.py` now that
two paths parse model output. `components/agents/RuleComposer.tsx` switches on
the discriminator, so a member added to the rule spec surfaces here rather than
being unauthorable; `ruleProblems` finally has the caller its docstring has
described since Phase 3, and Arm is disabled off it.*

**Not walked against a real model.** The unit tests cover the model path with a
stubbed transport and `tests/test_compose_live.py` is written and gated on
`OPENROUTER_LIVE_KEY`, unrun. Everything else was walked against a live stack:
composed, armed, four visitors seeded, the prompt on the log inside the budget,
then edited and re-saved under the same id.

### Added — 2026-09-02 — `[neo4j-track]` The room can say when it is full

The session wizard has asked operators for a **capacity** on every zone since the
first version of it, and nothing anywhere read the number. It sat in the database
being displayed back to the person who typed it.

It means something now. When a zone reaches the capacity its operator set, the
system says so once — one message, at the moment it fills, not one per person who
walks in afterwards — and says so again when it drops back below. A rule can be
armed on that, and the floor staff get a prompt on the live screen: *"Entry Arch
is at capacity (3) — slow the queue or open a second point."*

**This was the one thing left on the roadmap that needed nothing from outside.**
Everything else still open waits on a hosting decision, a payment provider or a
key somebody has to buy. This half of the "floor orchestrator" was written down a
phase ago as buildable and measurement rather than prediction, and it is exactly
that: no model guesses how busy the room is about to get.

**A crowding rule could not be written before today**, which is easy to miss.
The only signal about a zone was emitted when somebody *left* it, so "five people
at the entrance" was a thing the system could tell you about ten minutes after it
mattered.

**Three things it refuses, and the refusals are the design.** A zone the operator
gave no capacity produces nothing, at any count — a threshold nobody set is not a
threshold. A room that is already full does not announce itself again on every
arrival, because a prompt raised nine times in a minute teaches the floor to stop
reading the panel. And the count is worked out from when things happened rather
than the order they were written down, so a camera catching up after an outage
does not report an empty room.

**The live screen was showing a zero it had not measured.** Every zone of every
real activation read "0 now" under the words "awaiting data", beside a session
that was running — the demo's curated numbers on one side and a literal zero on
the other. It reads the log now, shows the ratio against the capacity where there
is one, turns the count orange at or over it, and shows a dash rather than a zero
where nothing has been measured at all. "Nobody is here" and "nothing is
measuring" are different answers, which is the distinction this project has been
drawing on the report since Phase 2.

**And one thing the rules engine could not say.** A zone filling and a zone
clearing are the same kind of event, so the first version of the crowding rule
raised *"Entry Arch is at capacity"* at the moment it stopped being true. Rules
can now be narrowed to a field of the event, which also closes the same hole for
group formation — a rule saying "greet the group that just arrived" would have
greeted them again as they dispersed. Nobody had noticed, because nobody had
armed one.

*`consumers/occupancy.py`, its own consumer beside grouping and gaze so a
crowding bug cannot stop dwell being measured, ordered before the rules evaluator
so a crossing and its prompt land in one pass. `spatial.occupancy` pinned in
`event-bus-spec.md` §3 — the one `spatial.*` payload with no `anon_id`, because it
is a statement about a room. The occupant set is recomputed from the log rather
than accumulated, sharing `occupants_at` with `consumers/touch.py` so a tap and
the live panel cannot disagree about who is standing there; a rewound cursor
therefore re-derives the same crossings and the bus dedupes them, verified against
a live stack. `condition.payloadEquals` in ADR-002 (with unknown condition keys
now refused, since a misspelled filter narrows nothing and reads like the rule the
operator wrote), honoured by the browser's dry run as well as the edge.
`lib/live/derive.ts::zoneOccupancy`, `capacityRules` in `agents/presets.ts`, one
preset per zone that has a capacity. Walked live: five arrivals into a
three-person zone gave one `over` at the third, three departures one `cleared`,
one staff prompt, none on the clearing, and six arrivals into a capacity-less zone
gave nothing.*

### Fixed — 2026-08-31 — `[neo4j-track]` The two places on `/live` that disagreed about the same touchpoint

Yesterday's tablet feature split one idea in two: every press, and the presses we
can put a visitor's name against. The client report was taught the difference.
Two other things were not, and one of them showed on the same screen — the live
insight panel said *"the AR Mirror was used once"* beside a tile reading five.

The insight was the wrong one. "Used N times" is a question about the thing on
the stand, not about how identifiable the crowd happened to be, so it now counts
presses like everything else does. A press we could attribute is on the record
twice — once as itself, once as the visit it belongs to — and only the first is
counted, so the tally cannot double.

**Two more found while fixing it.**

The insight timeline could start late. It anchored its first ten-minute window on
the first *visitor*, so anything before that — somebody testing the touchpoint
while the stand is still being set up, which is the ordinary case — fell outside
every window and was never summarised. It anchors on whichever event came first
now.

And the insight was calling the touchpoint by its internal id: *"sf_mirror was
used 3 times"*. The name now travels with the press.

**And a test that could not have caught any of this.** The benchmark on the
report compares a client's activations against each other, and it fetches a list
of what to compare — a list with a comment on it warning that forgetting an entry
makes older activations quietly score worse than today's. There was a test
guarding that list. It compared the list to a copy of itself, so it could only
fail if somebody edited the thing it was guarding. It now asks the report what it
reads and checks nothing is missing, which is what it always claimed to do.

*`digest.SOURCE_TYPES` gains `surface.touched` and skips a `surface.interaction`
carrying a `touch_id` — the rule `computeScorecard` uses, now pinned in
`event-bus-spec.md` §3 for both. `insights._first_event_at` takes the min across
source types rather than the first non-empty one. `routers/touch.py` and
`consumers/touch.py` carry `surface_label`, as spatial events carry `zone_name`.
`SCORECARD_EVENT_TYPES` gains `surface.touched`, and its test is derived: one
event of every `RealmEventType` (a `Record` over the union, so the compiler
demands a sample for each) through `computeScorecard`, and anything that moves
the result must be fetched. Also a docstring on `peakZoneConcurrency`, which
described a per-zone maximum the code has never computed. 3 backend tests, live
walk on a real stack: 5 taps, 3 attributed, and the window's insight reads 4
where the old code read 2.*

### Added — 2026-08-31 — `[neo4j-track]` A tablet at the stand, so "interactions" stops being a dash

One of the four things the client report scores has been blank since it was
built. It counts how many people actually *used* something on the stand — the
mirror, the sampler, the screen — and nothing in the system could tell us,
because measuring it was always going to need hardware in the booth.

It turns out the hardware is a tablet. Prop one next to the thing, open a link on
it, and every press is recorded. No app, no account, no login on the tablet.

**What it can and cannot tell you, because the difference matters.** A tablet has
no camera. It knows its button was pressed; it does not know who pressed it. So
when one person is standing in that part of the booth, the press is credited to
their visit. When two are, it is counted and left unattributed — we will not
guess which of them reached out. On a busy stand that will be most presses, and
the report says both numbers rather than blending them: how many times the thing
was used, and how many of those we can put a visitor against.

The link is withdrawable on its own. A tablet left in a taxi is one click, not a
change that takes the cameras down with it, and a stranger who finds the link can
only add presses to that one touchpoint on that one activation — it reads nothing
at all.

*`surface.interaction` finally has a producer, four phases after its readers.
`POST /v1/touch/{token}` appends `surface.touched` (new type, pinned in
`event-bus-spec.md` §3, no `anon_id` by design); `consumers/touch.py` resolves the
visitor from `spatial.zone_enter`/`zone_exit` occupancy at event time and emits
`surface.interaction` only on exactly one occupant, waiting on the tracker's
cursor rather than reading an empty zone. Credential is a `report_share`-shaped
token (migration 0015) — hashed, shown once, revocable, with tenant/session/
surface off the row and never the request. `/touch` is the tablet page with an
offline queue keyed on an id minted under the finger; `TabletPanel` on the
calibration screen mints and withdraws. `computeScorecard` counts every
`surface.touched` as an interaction and only an attributed one into
`engagedVisitors`. 19 backend tests, 3 scorecard tests, walked end to end against
a live stack.*

### Changed — 2026-08-31 — `[neo4j-track]` The AI never learns who the letter is for

The follow-up drafter writes to a real person, so until today it told the model
that person's name and company. Everything else we send an AI is anonymous —
where people walked, how long they stayed — and this was the one place a visitor's
identity left the building.

It does not any more. The model is handed `[FIRST_NAME]` and `[COMPANY]`, writes
the letter around them, and we put the real person in afterwards on our own
machine. The reviewer reads exactly the letter they would have read before. The
AI company sees an email about somebody who stood at the Product Pod for four
minutes and never finds out who that was.

This was written down as a question for whoever signs the first pilot agreement —
a visitor agreed to be contacted, and whether that covers their name reaching an
AI vendor is somebody else's call. It turned out the fix costs nothing anybody
would have to weigh, so there was nothing left to ask.

One thing had to be handled: a model sometimes invents a placeholder of its own —
`[LAST_NAME]`, `[PRODUCT]` — and a letter arriving in the review queue with one
of those in it looks like a broken mail-merge. Those are thrown away and the
plainer, template-written draft stands instead. A reviewer's attention should go
on what the letter claims about somebody's visit, not on our bugs.

*Open decision 5 in `docs/roadmap.md`, closed. `app/llm/prompts.py` gains
`NAME_TOKEN`, `COMPANY_TOKEN` and `splice_identity()`; `sdr_prompt()` sends the
tokens; `consumers/sdr._with_identity` splices subject and body and refuses both
or neither, falling back to `deterministic_draft` exactly as `_split` does. The
activation's own name is still sent — it is the client's event, not the visitor's
identity. Deliberately not a setting: the "on" position is the thing `privacy.md`
forbids. Six tests in `tests/test_sdr.py`; the "does a real model copy a bracket
token verbatim" assertion is in `tests/test_sdr_live.py` and runs with a key.
`docs/privacy.md`'s AI-calls clause rewritten to match.*

### Added — 2026-08-31 — `[neo4j-track]` The follow-up letter is written by a model, and Phase 5 is done

The last piece waiting on the API key. When somebody agrees to be contacted, the
system drafts a follow-up email naming where they actually spent their time. A
template wrote it before; a model writes it now, and a person still reads every
one before anything is sent.

What it is allowed to say has not changed. It knows where they walked and it does
not know anything they said, so a draft that referred to a conversation, a
promise or a discount would be inventing one — and the check on that is now run
against a real model rather than a stand-in, four times over.

**Watching it work found two things nothing else would have.**

The first: the drafts sometimes came back **empty**. The model thinks before it
answers, that thinking comes out of the same allowance as the answer, and writing
a letter takes far more thought than picking a chart. We measured the same
request six times and the thinking ranged from nothing at all to nearly four
thousand words' worth. There is no allowance that is both tight and safe against
that, so the allowance is now simply generous — we are billed for what is used,
not what is allowed, so this costs nothing on the drafts that do not need it.

The second: a good draft was being **thrown away for being formatted**. The model
was asked for a subject line and sometimes wrote it in bold, and the code only
recognised the unbolded form. The template's version quietly stood in, and the
label on the draft still said so — so the feature looked switched off rather than
broken. Exactly the same thing happened with Ask the Room's answers last week,
and it is fixed the same way.

**One thing to raise rather than fix.** This is the only part of the system that
sends a real person's name to the AI vendor, because it is writing an email to
them. Our privacy page promises those calls receive "only structured event
summaries", and a name is not that. It is answerable either way — and the answer
belongs to whoever signs the pilot agreement, not to us. The same page also still
named the wrong vendor; that is corrected.

#### The detail

- **`tests/test_sdr_live.py`** — four runs against the real key. Asserts
  `basis == "openrouter"`, which is the load-bearing one: it is what
  distinguishes a model's draft from the composed fallback standing in silently.
  Plus no zone the visitor did not enter (checked against a third zone seeded on
  the activation, so the assertion has a wrong answer available), no figure the
  prompt was not given, none of `prompts.SDR`'s named forbidden phrases, under
  120 words, and no `anon_id` in customer-facing text.
- **`REASONING_HEADROOM` 1024 → 8000.** Measured on one prompt, six times:
  DeepSeek 0 / 178 / 311 / 846 / 851 / **3906**, Gemini 617–688. A ceiling rather
  than an estimate. The fourth walk metered 4084 tokens — a draft the old ceiling
  would have truncated to nothing.
- **`consumers/sdr._split`** now takes a subject line however a chat model
  decorates it — `**Subject:**`, `__Subject__:`, `# Subject:`, `Subject -`. Still
  refuses a reply with no subject line at all, and still refuses `Subjective`,
  which is the word a widened pattern would start matching by accident.
- **`docs/privacy.md`** — the AI-call clause named "Claude / GPT-4o"; it is
  OpenRouter routing to DeepSeek and Google, which also changes the answer to the
  pilot agreement's question 7 about jurisdictions. Both corrected, and the
  name-to-vendor question recorded as open decision 5.

### Changed — 2026-08-31 — `[neo4j-track]` The latency test measures the system again, not the machine

Our build went red twice on a test that checks a staff prompt reaches Slack
within three seconds. Both times it measured just over: 3.00s and 3.01s. On a
developer machine the same test measures about 1.1s.

Nothing had got slower. The build machine is about twice as slow as ours at
everything — the whole backend suite takes four minutes there against under two
here — so a test sitting at 1.1s on one machine sits on top of a three-second
line on the other, and falls either side of it at random. A test that fails half
the time is one people learn to re-run without reading, which costs more than the
test was worth.

**So it now counts the steps rather than the seconds.** A prompt reaching Slack
passes through a fixed number of hand-offs between the parts of the system, and
that number is the same on a fast machine and a slow one — we checked, including
with the polling deliberately slowed down. It is also the thing that actually
went wrong the last time this regressed: somebody adds a step, and every
deployment waits one more beat, on any hardware.

The three-second claim has not moved and the measured time is still printed on
every run. What changed is which of the two we let fail a build.

#### The detail

- **Asserted:** productive consumer passes on the causal path (`tracker` →
  `rules` → `dispatch`) between the trigger and the Slack post. Observed 6–8
  across runs, including one with the poll interval tripled; the ceiling is 10,
  which carries that variation and not a new link in the chain.
- **Printed, not asserted:** the wall clock, against `BUDGET_SECONDS = 3.0`, so
  the number `roadmap.md` and `event-bus-spec.md` §4 claim stays visible on
  every run. The remaining time assertion is `PATIENCE_SECONDS`, which only
  catches a chain that has stopped moving.
- **The counter wraps `Consumer.run_once`** rather than living inside any one
  consumer, because what is asserted is a property of the chain and a consumer
  can only report its own passes. A pass that consumed nothing is not a hop —
  that is a poll finding an empty queue, which is what the idle interval is for.
- **The evidence, since this is a weakened assertion and should be justified:**
  CI 3.0106s and 3.0024s; locally 1142/1101/1111ms before the week's changes and
  1112/1076/1138/1130ms after, so nothing this week caused it; instrumented hop
  counts 7/7/7/8 at the normal poll interval and 6 with it tripled.

### Added — 2026-08-31 — `[neo4j-track]` A ceiling on what we spend with the model, and the room now writes its own insights

Two things landed on top of this morning's provider.

**The booth writes its ten-minute summaries itself now.** Every ten minutes it
already worked out what happened on the floor; a template turned that into a
sentence. A model writes the sentence now, and the numbers in it are still the
floor's own — we check that nothing appears in the prose that the window did not
measure, and every claim still opens to the events it rests on.

**And there is a lid on what that costs.** The summary writer runs on a timer,
which is the one thing here that can spend money while nobody is watching, and
the key we were given has no spending limit of its own. So a monthly ceiling can
now be set. Past it nothing breaks: the summary is still written, the question is
still answered, the follow-up is still drafted — each from the measurements,
without the model, and each says so.

**The ceiling is off unless somebody sets it.** No pricing tier states a number
of AI questions, and picking one here would be inventing a figure on a client's
behalf. This is a limit on our own spending, not on what anyone bought.

**One thing we found by looking rather than by it going wrong:** the follow-up
drafter has been calling a model and recording none of the cost. It never showed,
because until yesterday there was no model to bill for it — but a cost figure
that quietly omits one of the three things spending money is a wrong number, not
a missing one. It is counted now.

#### The detail

- **`app/llm/budget.py` + `llm_monthly_token_budget`** — per tenant, per calendar
  month, `None` and unenforced by default. The month is measured on
  `occurred_at`, like every other window here, so a batch replayed late counts
  against the month it was spent in.
- **`cost.spent_tokens`** — the meter's other half. The log is the record and
  there is no usage table: a derived event id means a replayed meter cannot
  inflate the sum, so counting rows *is* counting spend. A purged row drops out,
  which is right — a budget counting spend the client may no longer see would
  enforce against a number nobody can check.
- **`detail.spender`** on all three LLM meters (`ask`, `insight`, `sdr`). "What
  is the timer costing us" is a different question from "what are operators
  asking", and it was unanswerable before. `CostMeteredPayload` in the dashboard
  contract did not declare `detail` at all, so the cost tile was reading a field
  the contract denied existed; declared now.
- **The SDR's missing meter.** `consumers/sdr.py` called the model and metered
  nothing, since the day it was written. Keyed on the contact like the draft
  itself, so a replayed draft is not billed twice.
- **A spent budget is not an error.** All three spenders already fall back to a
  deterministic answer when a provider raises, and this reuses that floor. `/ask`
  reports `basis: deterministic` — the answer must not claim a model wrote it —
  and the insight consumer records the same and keeps its cursor moving, because
  failing there would stall a consumer over a sentence.
- **`tests/test_insights_live.py`** — the insight walk against a real model, run
  by hand with `OPENROUTER_LIVE_KEY`, four times. The no-invented-figures check
  derives what is permitted from the digest rather than a hand-written list: the
  first version failed a correct sentence ("the Entry zone for 60 seconds") that
  was measured and simply not on the list, which is a test of the author's memory
  rather than of the model.

### Added — 2026-08-31 — `[neo4j-track]` The room can be asked questions by a model

Since the beginning, asking the booth a question got you an answer chosen by
matching the words you typed against a list. It was honest about it — every
answer said so — but it could only recognise a phrasing somebody had thought of
in advance, and it refused anything else rather than guess.

A model does that job now. You ask in your own words; it picks which of our
measurements answers you, and writes the sentence.

**What it did not change is where the numbers come from.** The model never sees
the database and never writes a query. It chooses the *name* of a measurement we
wrote and reviewed, we run it, and it turns the rows into a sentence. Every
figure in an answer is still measured, and the answer still says which kind of
thing produced it.

**Two of the three models we were given can actually be used.** The third is
reachable only through a different, batch-style interface where you submit a job
and collect the answer later — no use to somebody watching a spinner. It is
written down by name with the reason, so nobody re-discovers it from an error.

**The key has no spending limit and three of us share it.** The health check
reports what it has spent for that reason, and the feature that would call the
model on a timer is deliberately left off until there is a ceiling.

#### The detail

- **`app/llm/openrouter.py`** — the adapter open decision 2 was waiting for.
  `ALLOWED` is the callable slugs; `BATCH_ONLY` names
  `deepseek/deepseek-v4-pro-0813:batch`, which answers **404 "This model is only
  available through the Batch API"** on `/chat/completions`. Default is
  `deepseek/deepseek-v4-flash-0731`: $0.0000167 a routing call against Gemini's
  $0.00096 for the same routing decision, both measured.
- **Reasoning is mandatory** on both usable models —
  `{"reasoning": {"enabled": false}}` returns 400. The caller's `max_tokens` is
  the budget for the *answer* and `REASONING_HEADROOM` is added on top; a 512
  budget spent thinking comes back `content: null, finish_reason: "length"`,
  which is what the first probe did before the adapter existed.
- **`app/llm/http.py`** — `app/crm/http.py`'s status→`retryable` vocabulary,
  raising `LlmError` instead of `AdapterError`, and the package's one `httpx`
  import so `tests/llm_transport.py` can stub one seam.
- **`python -m app.llm.connect`** — there was no path in the system that stored
  an LLM credential: `PUT /v1/integrations/{provider}` validates against
  `crm.registry` and `upsert_integration` defaults `kind="crm"`. A CLI rather
  than an endpoint, for the reason `app/plans.py` gives about the tier setter,
  and because the destinations surface fanning a `handoff.lead` over
  `kind = 'crm'` is what stops a model provider being offered somebody's
  contact details. `connect check` runs the healthcheck.
- **Two bugs in `POST /v1/ask` that only a real provider could expose.**
  `meter()` was called without its required `occurred_at` — with the
  deterministic provider `tokens` is always 0, so that branch had never run and
  the endpoint 500'd the first time a model reported a count. And the metering
  `cause` was `("ask", entry.name)`, which derives one event id per measurement
  per activation: a client asking the same question ten times was metered once.
  It now carries the question and the moment, with the reason `cost.py` warns
  against a timestamp — and why a request, unlike a replayed event, is not
  something to be idempotent about.
- **Gemini fences its JSON.** It wraps the routing object in ```` ```json ````
  every time; without handling that, `/ask` answered "the AI provider replied
  with something that is not JSON" — a refusal an operator reads as their own
  question being wrong. Unfenced in `routers/ask.py`, the one place that parses
  a model's output, rather than per vendor.
- **`llm_timeout_seconds` (20s)**, separate from `action_timeout_seconds` (2.0s).
  That one is short because `event-bus-spec.md` §4 budgets a **rule dispatch**
  under 3s; `/ask` is not on that budget — it is a person waiting for a sentence,
  and observed answers ranged 7.6s to 46s.
- **`tests/test_openrouter_live.py`** — the one test that calls the vendor, run
  by hand with `OPENROUTER_LIVE_KEY`. It proves the slugs are real, that the
  batch model still cannot be called synchronously, and that `/ask` answers a
  seeded activation with the graph's own figure. Five CRM adapters are still in
  the position this closes for the model provider.

### Added — 2026-08-29 — `[neo4j-track]` Retention now removes something

We sell "30-day data retention" on the entry tier and 90 days above it. Until
today that was a reading rule: past the window a client could no longer see
their own data, and every row stayed exactly where it was. The data outlived the
window they paid for and was simply invisible to them.

It goes now. Past the window, an activation's events lose their contents and its
visitors are deleted from the graph.

**What is left is a skeleton, and we say so rather than calling it deletion.**
Each event keeps its position, its type and its timestamps; everything the data
actually *is* goes. Keeping the husk is what stops the rest of the system
quietly breaking — things that point at those events still point somewhere, and
a rebuild produces the same answer instead of a parallel set of duplicates. A
client told "we deleted it" would find rows still there, so the privacy docs say
skeleton.

**The booth stays; the visitors go.** Zones, cameras and touchpoints are what the
operator set up, not who walked through — and deleting them would make an expired
activation look like one nobody ever configured, a distinction the report goes
out of its way to keep.

**It refuses more readily than it runs.** It will not purge while any part of the
system is still catching up on those events, while somebody's erasure request is
in flight, or while a failed event in the window is waiting for a human — because
each of those turns a deletion into a quiet wrong answer: an activation short of
its own data, a legal right racing a bulk rewrite, or a stuck row nobody can ever
clear. Each refusal explains itself and can be seen before you run anything: a
dry run reports *every* reason it would be blocked, not the first.

**And it cannot be run on a timer.** An admin asks for it and gets a receipt with
their name on it. A job quietly deleting a client's data on Tuesdays is a worse
thing to own than the work of asking.

#### The detail

- **`purged_at`, never `redacted_at`.** That column means somebody exercised
  Article 17 and its counts are what an auditor reads; sharing it would put rows
  nobody asked about into that answer.
- **The replay guard is the half that is easy to miss.** With payloads emptied, a
  rewind to the start of the log would feed consumers empty events and rebuild a
  wrong graph — silently, because most of them skip what they cannot read. A
  watermark makes that rewind refuse. The wrong replay is unwritable rather than
  merely wrong.
- **`erasure.*` and `retention.*` are exempt from purging.** They are the record
  that the purging and the erasing happened.
- **A session is judged on its *latest* event.** A long run that began before the
  window and is still going is not expired.
- **A tier that states no window purges nothing.** The pricing sheet is the only
  authority, the same rule the plan limits already follow — deleting a client's
  data on a limit nobody sold them would be the worst place to start inventing
  one.
- 12 backend tests; all three refusals and the replay guard mutation-checked by
  deleting each rule and watching the test fail. 713 backend and 270 dashboard
  pass; types, lint and build clean.

#### What the live run found

A purged activation rendered on the report as **a quiet day** — a page of zeros,
presented as findings. An emptied log looks exactly like an unattended one, and
that page already separates "nobody came" from "nothing was ever measuring" for
precisely this reason; retention had added a third case and nothing knew about
it.

There is a fourth state now. An expired activation says its window has passed and
that the figures cannot be recomputed, rather than quietly claiming nobody came.

### Added — 2026-08-29 — `[neo4j-track]` You can send a client their report

Until now, sharing a report meant giving somebody an account. That is the right
answer for a stakeholder who will keep coming back, and the wrong one for the
single email that ends an engagement — sent to a person who will never log in
and should not have to.

There is a link now. It opens one activation's report, needs no account, expires
after thirty days by default, and can be taken back at any time.

**What somebody holding the link can see.** That activation's report and nothing
else — not the live view, not the 3D twin, not Ask, not the leads ledger, not
anything that writes. And **no contact details**: every event that can carry a
name goes through the same removal a withdrawal request uses before it leaves the
server. Tested on real data — a lead recorded as "Jordan Reeve" came back with
the name, the email, the company and the job title all gone, and no trace of any
of them anywhere in the response.

**The counts still work, which is the point of removing rather than dropping.**
The client's copy says one lead captured because there was one, and the operator
sees the same number. Deleting those events instead would have made the two
copies quietly disagree, which is worse than either number on its own.

**Losing the link is not a disaster and neither is sending it to the wrong
person.** Revoke it and the page says the link is no longer valid. Make another
one. The link is shown once and stored only as a fingerprint, so nobody —
including us — can read it back out of the database afterwards.

#### The detail

- **An opaque stored token, not a signed one.** The roadmap asked for "signed,
  expiring", and a signature gives both for free — but not revocation, and an
  operator taking a link back is the requirement that decides it. Revoking a
  signed token needs a denylist, a denylist is a table, and once there is a table
  the signature does nothing the table does not. 32 random bytes, kept as a
  sha256, returned exactly once.
- **The token is not a credential.** Making it a third kind of caller would have
  meant every read endpoint accepting it — and `GET /events` takes a session id,
  so a link for one activation would have read any other by asking for it.
  Instead it reaches three routes that resolve it to a tenant and a session
  themselves and take **neither from the request**, so reading a second
  activation is not something that can be written rather than something that has
  to be caught. Handed to an ordinary endpoint as a bearer token it is simply not
  a token: 401 on all three tried.
- **Redaction is anchored to a classification, not a field list.** It reuses the
  erasure job's own `PII_TYPES` and `CONTACT_PII`, so a payload that grows a new
  contact field is covered by a vocabulary somebody else already maintains — and
  the test asserts over the classification, so it fails the day a type joins it
  without this path being considered.
- **Unknown, expired and revoked are one 404 with one message.** Which of the
  three a guess was is the only thing a stranger could act on.
- 11 backend tests, both security properties mutation-checked by deleting the
  rule and watching the test fail. 701 backend and 266 dashboard pass; types,
  lint and build clean.

#### One bug found by opening the page

The first version of the shared reader fetched the events and handed them
straight to the scorecard — and the client's report showed **one visitor where
there were six, with `NaN` for the average dwell**. The two halves of the system
name payload fields differently and there is a translation layer for exactly
that; this new code had walked around it.

It is the same failure the event boundary was rebuilt to prevent three days ago,
in code written by the person who rebuilt it, and nothing but looking at the page
would have caught it — the tests all passed. It is fixed, and pinned by a test
that asserts both the right answer and the wrong one, so the next reader of that
file is not a person.

### Fixed — 2026-08-29 — `[neo4j-track]` A camera that blinks no longer ends the activation

If the camera failed to hand over a frame the moment perception started, the run
stopped there. It printed that the session had ended, having recorded nothing,
and gave no other explanation. On a booth that is a camera which took a moment
to wake and an event that was never measured — and the operator's only clue
would be a report with nothing in it.

The same confusion sat in the main loop. A camera that drops a frame and a video
file that has reached its end look identical in the code — both are "the read
failed" — and both ended the run. For the file that is right; for a live camera
it is not.

**They are now told apart.** A camera is given a few seconds to produce its
first frame before anyone gives up on it, and a few seconds to come back if it
stutters mid-run. A file still stops the moment it runs out, because retrying
there would mean every replay of recorded footage hanging at the end of the
clip. A camera that genuinely never delivers still fails — quickly, at startup,
with its own message and its own exit code, rather than looking like a quiet
event nobody attended.

Runs also now report how many frames they dropped, so one that limped can be
told apart from one that was clean. Before, the only evidence was a frame count
nobody had a baseline for.

#### The detail

- **`perception/capture.py`**, cv2-free so the backend suite can reach it — the
  same split `bus_client.py` and `heading.py` already use for the reason
  `pytest.ini` records. Both functions take anything with a `.read()`, so the
  tests drive them with a fake that fails on demand rather than with a webcam
  nobody can rely on being plugged in.
- `realmspace.py` knew which kind of source it had all along — a numeric
  `--source` is a camera index — and that fact simply was not reaching the
  decision. It is now the `live` flag on every read.
- **Exit code 3**, distinct from 1 (never opened) and 2 (no privacy mask). The
  three failures have different remedies and used to look the same.
- 7 tests, including the one that matters most in the other direction: a file
  source must still stop at its first failed read, and must not be retried.
- **Verified end to end on a real clip**: 20 frames in, `frames: 20`,
  `dropped_frames: 0`, and no pause at the end.

#### Still not verified, for the fourth time

**No camera has yet driven gaze, and the camera leg of the latency is still
inferred.** A fourth capture window ran tonight and recorded **zero people**.
The pipeline itself was working — the loop processed **1,596 frames in 90
seconds, about 18fps**, with the pose model running on every one — so this is
the detector honestly finding nobody in shot, not a fault.

That leaves two claims this project still does not make: that `spatial.gaze`
fires on a real skeleton and stays silent when somebody turns away, and that the
full camera → dashboard chain has been measured in one piece rather than added
up from two halves, one of them five phases old. Everything stays staged for
whenever somebody is actually in front of the lens.

One more thing seen three times tonight and not yet chased: the backend suite
throws an intermittent Postgres `ConnectionError`, on a different test each
time, always passing on a re-run. It has not failed CI. Recorded here so the
fourth occurrence is not the first anybody has heard of it.

### Added — 2026-08-28 — `[neo4j-track]` The booth can tell you what people looked at

Until now the system could say who walked into a zone, how long they stayed,
and who came close and didn't go in. It could not say who **looked**. A stand
that catches the eye from across the room and draws no footsteps produced a
report identical to one nobody noticed — and those are very different problems
for a client to have.

`spatial.gaze` has been in the plans since Phase 1 and had no producer for five
phases, for an honest reason written down at the time: it needs to know which
way somebody's head is turned, and the camera was only reporting boxes around
people. It reports a facing direction now.

**What the camera keeps and what it sends.** The privacy rules already allowed
this and already limited it: a skeleton may be used briefly to work out which
way somebody faces, and a face may never be stored at all. So the skeleton is
used and discarded inside the camera process, and the only things that leave are
two numbers — a direction and how much to trust it. Nothing that could identify
anybody goes onto the permanent record, which matters because that record is
append-only and gets exported.

**What it refuses is most of the work.** A monocular camera gives a facing
direction, not eye tracking; there is no depth, and looking up reads the same as
looking ahead. A detector that answers on every frame would report every head
turn as interest, which is the commonest thing a head does — it would look like
a working feature and mean nothing. So it says nothing at all when the shoulders
aren't both visible, when there is no face or ear to tell front from back, when
those two pieces of evidence disagree with each other, when nothing lies along
the line of sight, or when a look isn't held long enough to be more than a
glance. Each of those has a test, and each test was checked by deliberately
removing the rule and watching it fail.

**Where it shows up.** A new panel on the report: *Looked at, never entered*.
Deliberately not folded into the ROI scorecard — that would change figures
against definitions clients have already agreed for their activation, which is
the one thing the report is not allowed to do.

#### The detail

- **`perception/heading.py`** — split out of `realmspace.py` because that file
  imports cv2 and the backend venv therefore cannot test any of it, which is the
  same reason `bus_client.py` is separate. The shoulder line gives the body axis;
  the head keypoints say which way along it. **The two are cross-checked and a
  disagreement is refused**: the pose model's weakest case is a back view, and
  its mislabelling a shoulder would otherwise put a visitor looking at the
  opposite wall. Default model is now `yolov8n-pose.pt`, which returns boxes and
  keypoints in one pass — running a second model would have doubled the cost on
  the sub-500ms path. A detect-only model still tracks and dwells and simply
  carries no heading.
- **`consumers/gaze.py`** — its own consumer, beside grouping and for the same
  reason: the tracker is the path the Phase 1 latency budget is measured on and
  a gaze bug must not stop dwell being measured. Casts a ray from the person's
  centroid, takes the **nearest** zone it crosses (two stands on one line of
  sight is an ordinary layout), and **excludes the zone they are standing in** —
  a ray from inside a polygon always crosses it on the way out, so without that
  every look would land on the floor the visitor already occupies.
- **Zones, not surfaces.** `data-model.md` specifies
  `LOOKED_AT -> (Object|Surface)` and a Surface in this system carries a zone id
  and no geometry — there is nothing in a frame to aim at. The deviation is
  recorded in that file, and its own example query, written against `(:Object)`
  when the file was, now matches what exists.
- **The same camera filter the tracker applies**, and it matters more here: a
  polygon is normalized within one camera's frame, so a ray cast into another
  camera's zones points at floor that camera never saw. Proven by a pair of
  tests differing in one thing — and the first version of that pair passed with
  the filter deleted, because the other camera's zone was too far away to be hit
  anyway. Moving it nearer is what made the test watch something.
- 29 backend tests and 4 dashboard tests. 683 backend and 263 dashboard pass;
  types, lint and build clean.

#### Not verified, and stated rather than implied

**No camera has ever driven this.** Three capture windows were run tonight and
every one recorded zero people — nobody was in frame. So the heading maths is
proven against hand-built keypoint sets and the consumer against seeded events,
and the pose model's real output has never reached it. That is the one claim
this entry does not make.

One thing those attempts did surface, left alone as out of scope: if the camera
fails to return its very first frame, perception exits immediately with
`"frames": 0` and no other explanation. On a booth that would be a camera that
woke a moment too slowly and a run that ended before it started.

### Fixed — 2026-08-27 — `[neo4j-track]` CI had never once passed

We added CI on 21 August and then never looked at whether it was green. It was
not. **Every run since the day it landed had failed** — ten out of ten — so the
checks meant to catch a broken build have been quietly reporting nothing useful
for six days. Neither failure was a broken build. Both were the tests being
wrong about the machines they run on.

**The dashboard tests ran the computer out of memory.** Saving to the browser's
local storage rewrites the whole stored day every time a single event is added.
The demo data is about 1,200 events written one at a time, so writing it once
rewrites that store 1,200 times, each rewrite bigger than the last. The test
file does it fifteen times over. On our own machines there is enough memory to
absorb that; on the smaller machine CI uses there is not, and the test process
died part way through.

It now writes the batch once. The affected test file went from 3.7 seconds to
under a tenth of a second, the whole dashboard suite from 5 seconds to 2, and it
passes with a memory limit **eight times smaller** than the one it was dying at.

**The same slowness was in the product**, which is the better half of this. The
report pulls a session's history from the backend in pages of 500, and it was
saving them one event at a time in exactly the same way — so opening the report
on a real activation did the same rewriting, hundreds of thousands of times, in
a customer's browser. That path is batched now too.

**The speed test was measuring the machine, not the product.** Phase 1's
acceptance is that a detection reaches the dashboard in under half a second, and
a test asserted it against a stopwatch on a single visitor. CI's machine is
about half the speed of ours and read 810ms, so it failed every time.

Looking properly found the bigger half: on our own machine, with the databases
running, back-to-back readings ranged from **72ms to 838ms**. The test had been
passing or failing on which reading it happened to take. So it now times **five
visitors and takes the median**, and what it fails on is a *regression* ceiling
rather than the product promise — loose enough that ordinary noise never trips
it, tight enough to catch something genuinely slow. The half-second promise is
still checked, deliberately, by asking for it on a quiet machine, and the real
numbers are printed on every run either way.

#### The detail

- **`lib/bus/log.ts` → `appendMany`.** `persist()` serialises the whole
  partition, so N appends is N stringifications of an array growing to N. Same
  dedupe on `eventId`, same seq assignment, same per-event subscriber fan-out as
  `append`; only the write is batched, and it persists before it announces so a
  subscriber that reads the log sees what a reload would. Used by
  `seedDemoSession` and by a new `mirrorPage()` in `remote.ts`, which shares
  `mirror()`'s translation, refusal and cursor rules — a malformed event in a
  backfilled page is refused and quarantined exactly as it is on the socket.
- **The OOM was reproduced before it was fixed**, so the fix is known to be the
  fix: `NODE_OPTIONS=--max-old-space-size=2048 npx vitest run
  src/lib/mock/seed-demo.test.ts` dies with the same
  `FATAL ERROR: Reached heap limit` CI reports. Afterwards it passes at **256MB**.
- **`test_phase1_latency.py`**: `SAMPLES = 5` and the median, plus a
  `BUDGET_SECONDS` that defaults to a 2.0s regression ceiling and reads
  `RS_LATENCY_BUDGET_SECONDS` when somebody wants the 500ms criterion.
  The failure message and the printed line both name **which** ceiling was in
  force, so a red run is never misread as the product criterion being missed.
  Measured 8 consecutive runs after the change: medians 184–393ms, every one
  passing, against 2 failures in 10 before it.
- **`ci.yml` is unchanged.** An earlier version of this fix set a looser ceiling
  there and left 500ms as the local default; the ten-run measurement above
  showed the strict assertion was flaky on a developer machine too, so one
  ceiling everywhere is both simpler and more honest than CI and a laptop
  disagreeing about what passing means.
- Not changed, and stated rather than left to be found: `part.events` still
  grows unbounded in memory while `persist` trims to `MAX_PERSISTED`. It is a
  separate issue, it is not what ran CI out of memory, and changing it would
  change what `readAll` returns.

6 new tests; 259 dashboard and 654 backend pass, types, lint and build clean.

**And then the promise was actually re-checked**, since the figures in
`roadmap.md` had stood unrepeated since Phase 1 while five phases were built on
top of them. Twelve runs against the 500ms criterion, each the median of five
visitors: **246ms median run, 117ms best, 11 of 12 inside the criterion.**

It is inside the promise and slower than Phase 1 recorded (110ms), and the two
reasons are worth writing down rather than leaving as a mystery. The databases
now run **inside a Linux VM** on this machine, where the original measurement
had them installed natively — the VM held 145% CPU throughout, and it is the
databases the test needs, so that is this machine's floor rather than an idle
one. And **seventeen consumers poll the log now** where Phase 1 had two.

The single run that missed had all five of its samples land together at
685–809ms, which is a machine stalling rather than a slow path — jitter moves
samples apart, not as a block. It is recorded rather than dropped, because that
is precisely the thing a gating test must not be judged on.

Not re-checked, and stated: the camera leg. Phase 1's 157ms came from YOLO over
a real clip and no clip was run.

### Fixed — 2026-08-27 — `[neo4j-track]` The two things a first customer hits

The Phase 6 walkthrough fixed five things and wrote two down. These are those
two. Both are on the path somebody walks in their first ten minutes.

**The wizard would not let you continue and would not say why.** Step 3 needs a
space template. The zones are already there — they arrived from the experience
type you picked two screens earlier — so the page looks finished, and the only
sign that anything is wrong is a Continue button that has gone grey. There was
nothing to read and nowhere to look.

It says what it is waiting on now, beside the button you just failed to press:
*"Choose a space template above. The zones below came from the experience type
you picked — the template is what gives them a footprint to sit in."* The
sentence has to mention the zones, because their being there is the whole reason
the refusal looked like a fault.

The other three steps had the same silent refusal for different fields, and now
name whichever one is actually missing — the venue but not the start time, a
name but not the type, and how many touchpoints are still unnamed.

**A brand-new customer's first screen was somebody else's activation.** Sign up,
and you landed on the built-in demo: another client's name, another client's
venue, and a set of figures for an event that never happened. It is labelled,
and it lives in code so the laptop demo works with no backend at all — but it
should not be the first thing a paying customer sees.

The demo stays exactly where it was, in the list and one click away in the
switcher. What changed is what an organisation with nothing of its own is shown:
*"Nothing measured here yet"*, and an invitation to set up its first activation.
Anybody who would rather look around the sample first can, from a link that says
what the sample is.

**The careful part is who does not see that.** "This browser has never seen an
activation" and "this organisation has never run one" are different claims, and
only the server can settle the second. So the screen appears only when the
backend confirms it, and never when the answer could not be read — an operator
on a second laptop, or one whose wifi dropped, must not be told their work does
not exist.

#### The detail

- **`lib/session/wizard-validation.ts`** — `blockedReason(step, draft)` returns
  the sentence, or null. Pure, so it is tested without rendering a five-step
  form. `WizardFooter` takes the reason and **derives** `disabled` from it, so a
  step cannot refuse to advance without one existing. The reason is never hidden
  at any width: one that disappears on a narrow screen is the defect again.
- **`lib/session/useFirstRun.ts`** — three conditions, and the third is the
  careful one: a backend is configured, the local store holds no activation of
  this tenant's own, **and** `fetchSessionList` returns `[]`. That function
  already separated `null` ("could not tell") from `[]` ("has run nothing") for
  the benchmark, which is exactly the distinction needed here — a `null` never
  gates. The decision is a pure `firstRunStatus()`; the hook is plumbing. It
  re-asks when the **verified** tenant lands, since the value starts as a guess
  and a gate deciding before then would decide for the wrong organisation.
- **`components/chrome/FirstRunGate.tsx`** — wraps the app's `<main>`.
  `/sessions/new` is never gated (it is the way out), nor `/sessions` (its own
  empty state and the demo filter already exist) nor `/ops` (a new organisation
  may need to read its plan first). "Still checking" renders the page, because a
  flash of a first-run screen on every load for an established client would be a
  worse lie than the one this replaces.
- **`components/chrome/StatusBar.tsx`** — while the gate holds, the header names
  the seeded activation as *"Sample activation · invented figures, not yours"*
  rather than putting a fictional client and venue above every screen.
- **Not done, deliberately:** making `useActiveSession()` nullable. It is
  eighteen call sites, each needing its own designed empty state. The complaint
  is about the first screen, and the first screen is one component.

#### How it was checked

Walked in a browser against a live stack, as a new customer:

- Signed up **Harbourline** through the login screen. Landed on *"Nothing
  measured here yet"*, not on Lagos Showroom, with the header reading "Sample
  activation". `/sessions` and `/ops` still opened, and the demo was still in the
  list with its DEMO badge.
- Ran the wizard and read the reason at each step: *"Choose an experience type
  and a name for this activation"* → narrowing to *"Choose a name…"* the moment
  a type was picked → *"Fill in the venue"* → the step-3 sentence, above a full
  list of seeded zones → *"One touchpoint has no name"*. Picking a template
  cleared it and Continue lit.
- Launched, and the gate was gone: `/live` and `/report` showed Harbourline's
  own activation with honest zeros.
- Signed back in as the seeded demo tenant, which has activations on the backend
  and none in this browser's store. Nothing changed for it — the case where the
  server knows about work this browser has never seen.

17 new tests; 253 pass, lint clean, production build green.

### Fixed — 2026-08-27 — `[neo4j-track]` The dashboard now refuses a broken event instead of quietly guessing

The last thing the Phase 6 walkthrough wrote down was that the two halves of
this system disagree about what a broken event is, and only one of them says so.

The backend refuses one outright. An event whose payload is missing something it
needs is retried, set aside in the failed-events queue, and shown on `/ops` for
somebody to look at. Nothing is lost and nothing is guessed.

The dashboard did the opposite. Handed the same event, it carried on. A visit
with no duration on it was added to the average anyway — which turned the
average, and every figure computed from it, into the word `NaN`. A visitor with
no id was added to the count of unique visitors as though they were a person.
Neither of those looked like a failure. They looked like a report.

Both halves refuse now, and the browser says what it refused and why.

**What an operator sees.** A new panel on `/ops`, beside the queue that has
always been there: *Events this browser could not read*, grouped by what was
wrong with them and counted. And one line on the report itself, in the same list
that already explains every missing figure — *"5 events could not be read and
are excluded from every figure here (4 × spatial.dwell, 1 × spatial.zone_enter).
They are still on the backend's log."* The number a client is shown is either
computed from readable events or absent with its reason, never averaged with
something nobody could read.

**Two things it deliberately does not do.** It does not throw the event away:
the real one is on the backend's log, which is where anybody investigating
should look. And it does not keep what the event said — only which field was
missing. That list lives in the browser, where an erasure request cannot reach
it, so a broken payload with somebody's name in it must not be copied there.

**One thing it does that is not visible.** A browser that saw the bad run has
those events saved locally, and they would never be re-checked. Those saved
copies are dropped and fetched again from the backend — except where the browser
is holding events it has not managed to send yet, which exist nowhere else and
are left alone. On the machine this was tested on, that guard fired for real:
one activation was holding two unsent events and was kept exactly as it was.

#### The detail

- **`dashboard/src/lib/contracts/validate.ts`** — a required-field table per
  event type and `validatePayload`. Only the fields a reader dereferences
  (`roi/scorecard.ts`, `report/derive.ts`, `live/derive.ts`, `twin/replay.ts`);
  a field nobody reads cannot produce a wrong figure by being absent. Presence,
  non-empty strings, **finite** numbers — `NaN` is the one value that passes
  `typeof x === "number"`, survives arithmetic and takes a whole scorecard with
  it, which is the shape defect 6 of the Phase 6 walk took on a client's report.
  **An unknown type passes**: the taxonomy is additive (`event-bus-spec.md` §3)
  and a browser that predates a producer must not refuse it.
  Drift between the table and the interfaces is a **compile error**, not a
  review item — each list is `satisfies readonly RequiredKeys<Payload>[]`, so
  listing a field the contract marks optional stops the build. Verified by
  making one optional and watching `tsc` refuse it.
- **The two inbound doors**, both of which already funnelled through
  `eventFromWire`: `mirror()` (the live socket and `backfillSession`) and
  `fetchSessionEvents()` (the report's benchmark and the twin, neither of which
  goes through the local log — a check that guarded only the first would have
  left both exactly as wrong as before). Judged **after** translation, on this
  app's dialect, or every snake_case event the real backend sends would be
  refused. **The remote cursor still advances on a refusal**: it records what
  this browser has seen, not what it accepted, and holding it back would
  re-request the same unreadable event forever.
- **`emit()` throws**, in the shape of the consent redline already in that
  function. Every caller is our own code with a fixed payload shape and there is
  no high-rate producer in the browser, so this is a programming error that
  belongs in a test rather than a runtime condition on a floor — and the
  alternative is this app emitting the malformation it has just started
  refusing to read.
- **`lib/bus/quarantine.ts`** — capped at 100 per activation, idempotent on
  `eventId` (a backfill overlaps the socket, so the same bad event arrives more
  than once and must not be counted twice), and storing the envelope and the
  reasons only.
- **`lib/bus/log.ts`** — a format version, and a sweep that drops mirrored
  partitions written before the check existed. The local log is a mirror, so a
  cleared partition costs nothing: `backfillSession` pages it back, this time
  through the validation. The guard is the exception `remote.ts` has always
  named — there is no second outbox, the local log *is* the buffer — so a
  partition whose outbound cursor is behind its head is left alone and
  reconsidered on a later load.

#### How it was checked

- **Against a live stack**, on the backend's own output. Five events posted to
  `POST /v1/events`, all five accepted by the bus; the graph writer parked three
  in `dead_letter` (`KeyError: 'anon_id'`, `KeyError: 'duration'`, and one of
  our own seeding mistakes). Running the browser's real boundary over
  `GET /events`: **before, 3 unique visitors — one of them `undefined` — with
  `NaN` average dwell and `NaN` attention. After, 1 visitor, 92s, 276, $1,000
  per engaged visit.** Every figure finite, and the two unreadable events named.
- **By eye**, in a browser: the `/ops` panel grouping five refusals by reason
  with their counts, clearing one activation's record and watching the header
  count follow it, and the report's own line naming the five.
- **The over-refusal guard.** `bus/contract.test.ts` runs the validator over
  every event in the verbatim capture of real backend output and asserts **zero**
  refusals. That file is the only place the real payloads are written down, and
  being stricter than the producers is the way this fails — it would drop real
  events off a client's report and look exactly like a quiet day.
- 32 new tests; 236 pass, lint clean, production build green.

### Fixed — 2026-08-25 — `[neo4j-track]` We walked in as a new customer, and found seven things

Phase 6 is judged on one sentence: *a third-party operator signs themselves up,
runs an activation, and gets a report — isolated, billed, on-brand.* Nobody had
ever done it. Every tick in the roadmap above is a piece of work vouching for
itself; none of them had walked the joins in between. So we did, in a browser,
against a real stack, as somebody who had never used the product.

**Three of the four hold. Seven things were broken, five of them on the path a
first customer walks.**

**You could not sign yourself up at all.** The account screen showed a single
line of developer instructions and nothing else, so the sign-up the backend had
been ready for since August had no way to be reached.

**Signing up put you back in the demo company on the next page refresh.** Your
new organisation lasted exactly as long as the tab stayed open. Then the app
quietly went back to the built-in demo account and showed you its data instead
of yours.

**Everyone using the same computer shared one list of activations.** A brand-new
customer's very first screen listed another client's events — names, venues,
zone and camera counts, footfall targets. Nothing from the server ever crossed
between them; this was the app's own local list, which had never been separated
per company.

**Adding a second camera deleted the numbers the report divides by.** This is
the worst of the seven. Declaring a camera part-way through an event wiped the
activation's cost, its engagement threshold, its attribution model, the client's
name and the dates — and the report carried on rendering as though nothing had
happened, just without them. Nothing failed, nothing warned; the figures simply
stopped being there.

**The client's name never left the browser.** The wizard's first screen has a
field labelled *Brand / Client*. Whatever you typed there was never sent, so the
client report came out with no client on it.

**A client's report could print the word `NaN`.** One earlier activation whose
data the scorecard could not read was enough to poison the comparison against
their own history: "Average dwell 58s · NaNs · NaN%" next to two real events.
That card's whole design is that a missing figure says so; this one shouted
nonsense instead.

**"Peak in zones" said one when three people were in the room.** It counted
arrivals and departures in the order they were filed rather than the order they
happened — fine for a camera streaming live, wrong for anything that catches up
after a dropout.

All seven are fixed except two we wrote down instead: the wizard's Continue
button greys itself out without saying what is missing, and a new customer still
sees the built-in demo activation on their first screen. Neither stops the
product working; both are somebody's decision rather than a bug.

**What the report now says, and why we believe it.** Nine visitors, three
pass-bys, 44.4% engaged past the threshold the operator set, 58s average dwell,
$4,500 per engaged visit against an $18,000 activation. Every one of those was
checked by hand against the events behind it. The things it cannot know — the
ROI ratio, the touchpoint interactions, the sentiment — are blank and say why,
which is the rule this report has been held to since it stopped inventing
numbers.

**"Billed" is the one clause that does not hold**, and it will not until there
is a payment provider. What exists is the enforcement a bill would be enforcing:
a customer on the entry tier, told in plain words that their plan allows one
camera and which plan allows four. No money moves.

### Added — 2026-08-25 — `[neo4j-track]` The pricing sheet is now a constraint, not a document

Every organisation is on a plan, and the plan actually stops things. Until today
a client on the cheapest tier could run eight cameras, arm forty agents, connect
five CRMs and read back a year of data — the tiers in `gtm.md` existed only on
the pricing page.

**What each tier now allows**, taken word for word from that page: Booth is one
camera and thirty days of readable data; Pavilion is four cameras, ninety days
and two custom agents; Campaign and Partner state no limits and so have none.

**Nothing was invented to fill the gaps, and there are two.** The pricing page
names "2 custom agents" for Pavilion and says nothing about agents on any other
tier — read literally that leaves Booth *uncapped* while Pavilion is capped at
two, which is upside down. And no tier mentions integrations at all, so that
limit never fires. Both are recorded as blanks waiting for a number rather than
guessed at, because a limit we chose ourselves would look exactly like one a
client had agreed to. Filling either in is now a one-line change with a test
that fails to make sure somebody notices.

**Being refused says what to do about it.** A save that would exceed a limit
comes back naming the tier the client is on, what it allows, what they were
trying to do, and which tier would allow it — and it uses a different code from
"your account cannot do this", because one of those is fixed by asking an admin
and the other by talking to us.

**And a limit is never a surprise.** There is a plan panel on the operations
screen showing usage against every ceiling, so nobody discovers a cap halfway
through setting up on the morning of an event. It has no upgrade button: taking
money is the half of this that is still unbuilt, and a button wired to nothing is
the mistake the report's dead "Export PDF" already taught us.

**The retention limit hides data rather than deleting it, and that is stated
plainly.** A client past their window can no longer read those events back, but
the events are still there. Deleting them is a second thing that can destroy a
client's records, it would break the guarantee that re-running the system over
its own history produces the same answers, and it needs the careful ordering the
GDPR erasure job already argues for. So it is written down as unbuilt rather
than half-done.

**Two screens deliberately ignore the window.** The lead pull-down and the
attribution ledger both read every withdrawal in order to strip names from
people who asked to be removed. Cutting them off at thirty days would hide an old
withdrawal and put a name back — the opposite of what a retention limit is for.

**Existing organisations were moved to Pavilion, not Booth.** Everything already
built and demonstrated runs four cameras; putting it all on the entry tier would
have broken the product to enforce a rule nobody had bought yet. New sign-ups get
the entry tier.

**One bug, found by writing the test before the code**, and it is the one a real
operator would hit first: because saving a rule is the same action as editing
one, counting on every save refused somebody editing their *own* second agent for
being a third.

### Changed — 2026-08-24 — `[neo4j-track]` The product finally looks like the brand book

`brand.md` has carried two warnings since it was written: the dashboard shipped
the wrong three typefaces and the wrong primary colour. Both are now what the
book says — Sora for headlines, Inter for reading, IBM Plex Mono for telemetry,
on a blue-black canvas with the brand's own accents.

**The interesting part was deciding what the orange is for.** The book is blunt:
neon orange is *"only for things that demand immediate attention … do not
overuse"*. The obvious move — point the app's main accent at it — would have
painted an alarm on every calm surface in the product. So the main accent became
the book's other colour, the teal it defines for "spatial data viz, flow and
movement", which is what that accent was already doing. Orange went to the two
things the book actually names: the primary call to action, and a staff prompt
telling the floor to go and do something.

**Looking at the result moved that line twice.** Three controls were using the
primary button style to show they were switched *on* — the twin's heatmap
toggle, the session filters, and the "open" button on every session card — and
each turned into a permanent alert the moment primary went orange. Being
selected and demanding attention are different things that had been sharing a
style; they have their own now. The card button was the clearest case: it
appears once per row, so a colour meaning "urgent" on every row means nothing on
any of them.

**On paper, both brand colours are close to unreadable**, and this is the part
that has caught us out before — a client's headline ROI figure once printed at a
contrast ratio of 1.36:1 against white, found by looking at a page rather than
by thinking about one. Measured: the teal is 1.91:1 and the orange 3.10:1, where
the accessibility standard asks for 4.5:1. Both now have darker print versions
that keep the same hue, and a script checks every colour in both the screen and
the print sets rather than anyone eyeballing it. The report was then read in a
forced print view to be sure.

**Nineteen copies of the old colour** were sitting in places a stylesheet cannot
reach — chart gradients, the 3D scene, the default colours new zones are created
with. They are one shared file now, so the next time the palette moves it is two
files rather than nine.

One consequence worth recording: zone colours stay wider than the brand palette
on purpose. Eleven kinds of zone have to be distinguishable at a glance on a
heatmap and the book provides three colours. The single new rule is that none of
them may sit near the orange — which retired the old sponsor colour, since after
the re-token it read as a permanent alert on the floor plan.

The name is also lowercase everywhere now, which the brand book states as its
first rule and the app had never followed.

### Fixed — 2026-08-24 — `[neo4j-track]` A new client's first page load showed them an empty room

Reading the benchmark against a live stack turned up a bug the test suite could
not have caught, because it only appears for an organisation that is not the
development default.

**On a first load, the report said "the session is configured correctly and the
log is genuinely empty" over an activation full of visitors.** A reload fixed
it, which is exactly why nobody had noticed. That sentence is the one the report
exists to keep separate from a broken pipeline — a client would have been told
they had a quiet day.

The cause was an ordering nobody had written down. The browser only learns which
organisation it belongs to when the backend hands it a token, and six places
read that value at the top of an effect, before anything had been awaited. So
they got the default. Every event arriving from the backend then failed the
comparison on the way in, was dropped, and the page read an empty local log.

Two mechanisms fix it, because the six places split into two shapes. Anything
already waiting on the network now waits for the answer as well. Anything
reading the local log subscribes, so it re-runs the moment the real
organisation is known.

**The same mistake, one level down, was making the live feed flicker.** The
socket captured the organisation when it was created and then waited for a
token before connecting — with the value it had captured before the token
existed. The backend refused that pair, correctly, and the operator saw BUS DOWN
on a system that was working. It now uses the verified answer, which is the rule
the token exchange already applied to everything else.

Two neighbours of that were fixed while it was open: a connection whose
subscriber had already gone away would still open, because nothing re-checked
after the wait; and signing out left an in-flight token exchange running, so the
next request could be handed the token the sign-out was meant to discard.

### Changed — 2026-08-24 — `[neo4j-track]` CI gates on lint, because the code stopped failing it

Seven lint errors had been tracked rather than fixed, and the CI job ran
`eslint src || true` because of them — gating on rules the repo violates makes a
permanently-red check, and a permanently-red check gets filtered out of people's
inboxes within a week.

**Two of the seven were real bugs.** The live view computed its session duration
and each tracked subject's time in frame by reading the clock while rendering,
which means both were measured once at first paint and then sat there. An
activation could run for an hour with the duration still showing the second it
started. They now read a shared clock that ticks — one timer for the whole page
rather than one per row on screen.

Three more were the same shape as each other: a component storing something it
could work out, then correcting itself immediately afterwards. The sign-in
provider's "loading" is the clearest — with no Firebase configured there was
never anybody to wait for, so it rendered a loading state and then took it back.
It is now simply derived.

**One is left suppressed, with the reason at the line.** The rule flagged a
clock read inside a button handler, where reading the clock is the correct
thing to do; it cannot tell a handler from a render. Rewriting working code to
satisfy a misreading would leave the next person wondering what the contortion
was for.

3 new browser tests. Dashboard suite 170 passing, backend unchanged at 630.

### Added — 2026-08-24 — `[neo4j-track]` The report can finally say "better than last time"

A client reading their second activation could not tell whether it beat their
first. The report has always carried one benchmark — the industry 3–5:1 band on
the ROI pill — which says how they compare to a category, not to themselves.
`roi-framework.md` has said since the beginning which of the two is worth more:
*"the most useful benchmark is the client's own history"*.

It now shows both. Under the scorecard sits a table of this activation against
the median of their last three, on four figures: unique visitors, engagement
rate, average dwell, and ROI ratio.

**The interesting constraint was where to compute it.** The backend deliberately
does not calculate any ROI figure — the four layers are defined once, in the
browser, and the endpoint that could have re-derived them says in writing why it
does not: two definitions of "engagement rate" and nothing to notice when they
stop agreeing. Doing this the easy way would have quietly created that second
definition, in a place nobody would think to look for one.

So the backend gained a **listing** and no arithmetic: which activations this
client has run, and what each was scored against. The browser then runs the same
scorecard function over each earlier session's log that it runs over the current
one. There is a test asserting those figures are read straight off that
scorecard, and it exists to fail the day somebody decides this would be faster as
a server-side aggregate.

**Reading three activations meant not reading three activations' detections.**
A scorecard looks at seven kinds of event and never at a detection, which is
almost every row in a day's log — hundreds of thousands of them against a few
thousand of everything else. `GET /events` can now be asked for several types at
once, and asking for two returns both rather than neither: a row only has one
type, so treating the list as an "and" would have returned an empty page, which
is indistinguishable from a quiet day.

**Median rather than average**, so one rained-off activation cannot make an
ordinary one look like a triumph. **A missing figure is dropped, not counted as
zero** — an activation whose cost was never entered has no ROI ratio, and
scoring it as nought would invent a failure it never had and flatter everything
beside it. Each row therefore says how many previous activations it actually
rests on, because a visitor median over three and an ROI median over one should
not look equally solid.

**Three things the card says instead of showing a number.** That this is the
client's first activation, when it is — a first is not a decline from anything.
That previous activations exist but measured nothing comparable. And that the
realmspace network median — this client against every other — is deliberately
absent: it would need anonymised aggregates across tenants, and nothing in this
system can read another tenant's data. That is the promise multi-tenancy is
built on, so the honest move is to name the gap on the card rather than quietly
leave a column out.

One thing that came out of building it: the twin's replay and this both needed
"read a whole session without putting it in the local ring buffer", and the twin
already had it. It moved to the shared bus module rather than being written a
second time, which is also how it picked up the type filter for free.

5 new backend tests, 11 in the browser. Backend suite 630 passing, dashboard 166.

### Added — 2026-08-22 — `[neo4j-track]` Two cameras stop being one person

A booth with two cameras was counting its visitors wrong, and nothing in the
product said so. Perception runs one process per camera, and the tracker inside
each one numbers the people it sees from `P-001` up. So both cameras called
their first visitor `P-001` — and everything downstream, which knew a person
only by that name, treated them as the same person.

What that produced was not an error. It was one visitor instead of two, with
their two stays interleaved into one flickering journey between two zones. The
audience came out smaller than the day was and the engagement came out higher
than it was, and both numbers looked exactly like measurements.

**A visitor is now the camera and the track together**, `cam-1/P-001`. Composed
where the events are read rather than where they are written, so the raw id
stays on the log and the perception script — shared with the other track — did
not have to change.

**A zone now belongs to the camera it was drawn in.** This is the half that
namespacing alone would have left broken. A zone is stored as a shape between 0
and 1 across the frame, so "the left third" is a different piece of floor for
every camera. Scored against the wrong camera's zones, a visitor is credited
with a stay somewhere they never stood. An operator assigns them on the
calibration screen, which only offers the choice once a session has two cameras
— with one, there is nothing to answer. A zone left unassigned belongs to every
camera, which is what every zone drawn until now is, so nothing that already
worked changes.

**A detection that cannot be attributed is refused rather than guessed.** If a
session declares two cameras and a detection arrives without saying which one it
came from, it goes to the dead-letter queue on `/ops` naming the cameras it could
have been, and the operator restarts perception with `--camera-id`. On a session
with one camera the same detection is accepted exactly as before — which is what
every event recorded before cameras had ids looks like.

**Two people seen by different cameras are never grouped.** The group detector
works on how close together two people are, and two people standing at the same
fraction of their own camera's width measure as touching. There is no way to
convert between two cameras' coordinates here, so the honest move is to decline
the comparison. It costs something real: a couple who split across two views
stop reading as a group until they are both in one view again. The alternative
is reporting strangers as families, which is the failure the group detector was
built to avoid in the first place.

**The twin was drawing the merge on screen.** The replay grouped its paths by
visitor name, so two cameras' `P-001` rendered as one person teleporting between
two rooms several times a second. Same fix, in the browser.

**What this deliberately does not do.** Somebody who walks out of one camera's
view and into another's is two visitors, counted twice. That is what
`privacy.md` promises — *"no cross-camera re-identification within a session"* —
and it is the side to be wrong on. The old behaviour broke the same promise in
the opposite direction, by accident, by deciding two strangers were one person.
Joining up what happened across cameras is a question about zones, not about
people, and it stays open.

One thing worth recording about the tests: the first version of the
cross-camera grouping test passed before the fix as well as after it. The two
people in it were standing still, and the group detector requires movement — so
the test proved nothing and would have gone on proving nothing after somebody
deleted the check. It now has a positive control beside it: the identical walk
on one camera, which must produce a group. Every other guard here was checked
the same way, by removing it and watching the test fail.

**Two setup mistakes are refused rather than absorbed.** A zone naming a camera
the session does not have is rejected at save time, and so is a save that would
remove a camera some zone still belongs to — the second matters because that
request need not mention zones at all, so the operator would otherwise switch off
part of their booth without being told. Both say which camera and what to do
about it, and the second has a test for the way out of it, so it is a guard and
not a trap.

18 new backend tests, 2 in the browser. Backend suite 625 passing, dashboard 155.

### Changed — 2026-08-22 — `[neo4j-track]` Phase 1 and Phase 2 finally say whether they passed

Every phase from 3 onward carries a verdict on its acceptance criteria — a
measured number, a date, or an honest 🟡 saying what is still missing. Phases 1
and 2 carried nothing at all. Five phases of work were sitting on top of two
whose stated bar nobody had ever confirmed.

Both are now marked, and the checking turned up more than a tick.

**The headline latency number was for a shorter journey than the one being
claimed.** Phase 1 promises under 500ms from detection to dashboard. The figure
in the notes — 58ms — is real, but it measures the WebSocket hop alone: bus to
browser. The journey the promise describes starts at the camera and includes
running the model, posting the event, and two background workers each waking up
on their own schedule. Nobody had timed that, and nothing would have noticed if
it got slower.

Timed now, with the real perception script running YOLO over a clip and a client
listening on the socket: **157ms typical, 191ms worst**. Comfortably inside the
promise, and now measured on the thing the promise is about.

There is also a repeatable test for the part that can be automated, which found
something the criterion never distinguished. The background workers poll quickly
while there is work and back off when the log is quiet — sensibly. So there are
two answers, not one: **110ms during an activation**, when a camera keeps the log
busy, and **218ms for the first visitor after a lull**, who arrives to sleeping
workers. Both are inside budget and both are recorded, because the second is a
real property of the system and it is a real person who experiences it.

One near-miss worth admitting: the first version of that test reported 611ms and
looked like a genuine budget failure. It was not — the test was starting its
stopwatch after the event it meant to time had already happened. A measurement
that fails in the direction you half expect is the one to distrust.

### Changed — 2026-08-22 — `[neo4j-track]` Two things the twin was still making up

Checking Phase 2's promise that "the twin replays a real recorded session" found
the replay itself working — real visitors, real paths, from a real camera run —
and the page around it still furnished with the demo.

**Every client's replay was titled "Pavilion No. 7".** The heading was a fixed
string, so an operator opening their own activation's replay saw the name of our
demo session at the top of it.

**The zone list and the surfaces panel were reading mock data.** Beside a replay
of somebody's real visitors sat the demo's five zones and three hardcoded
interaction counts — 482, 317, 904 — presented exactly as a measurement would
be. These are the same invented figures that were struck off the report and the
live view in Phase 2; they had survived one page over.

Both now read the activation. The surfaces panel shows **no counts at all**, and
says why: nothing emits a surface interaction yet, because real ones need booth
hardware, so any number there would be invented rather than measured. A marked
absence is the honest answer and a plausible figure is not.

The rest of Phase 2 checked out on one recorded session: the four-layer
scorecard rendered from the log, and a **3.2:1 ROI ratio** badged against the
industry 3–5:1 band — with the card stating on its face that the revenue behind
it was supplied by the client rather than measured by us. Ask answered three
questions in **6 to 12 milliseconds** against a five-second promise, every figure
traceable, and labelled as coming from the query catalogue rather than a model,
because there is still no AI provider.

Also corrected: the perception engine's README, which said the dashboard reads
mock data — untrue since Phase 2 — and called the whole thing a stub without
saying which parts. It is still a stub in specific ways worth knowing (one
camera, no re-identification, no head pose, which is why gaze is the last
unbuilt signal) and it is not a stub in others: it writes durably, survives an
outage, and masks the privacy polygon before the model ever sees the frame.

### Added — 2026-08-21 (later) — `[neo4j-track]` Who came with whom, without calling every queue a family

The last blind spot on the list that needed nothing we do not have. No API key,
no hardware — group detection is geometry over a detection stream this repo has
been producing since Phase 1.

**Everything around it was already built and nothing filled the middle.** The
`Group` node has been constrained in the graph since **migration 001**, added
with a comment saying it "is used by GROUP_MEMBER_OF and the 'Groups in Lounge'
query in data-model.md but was never declared". The data model specified its
properties. `spatial.group` was in the event taxonomy and in the browser's
contract. The PRD listed "group formation" as a rules trigger — and the rule
validator accepts it, so an operator could arm a rule on group formation today
and it would sit there forever, armed, unable to fire. And `data-model.md` ships
a worked example query, "Groups in Lounge for 4 min+", which has returned
nothing every time anyone has ever run it.

**The hard part is that proximity is not company.** Three strangers queueing at
a bar stand within a metre of each other for four minutes. A detector built on
"close together for a while" calls that a family, calls every busy zone one
enormous group, and produces a number an operator stops reading by the second
day — the same reason we refused to emit detection-rate drift, where a signal
that fires on the ordinary case is worse than no signal because it looks like
coverage.

So a pair has to pass one of two harder tests.

**They moved together.** Their shared midpoint travelled a real distance while
they stayed side by side. This is walking the floor together rather than
standing in the same spot, and a queue fails it by definition: it accumulates
time without going anywhere.

**Or they arrived and left together.** Into a zone within seconds of each other,
and out of it within seconds of each other. This is what catches the family who
sit at one table for twenty minutes and never move — the case the first test
cannot see — and a queue fails it for exactly the reason it is a queue: people
join it at different times and are served in order. Both halves are needed. A
door admits strangers in clumps, so arriving together on its own proves nothing,
and there is a test that says so.

Groups are then whoever is connected by those pairs, rather than requiring
everybody to pair with everybody. A family of four walks in a loose chain, and
demanding a clique would split them apart the moment one of them stopped to
look at something.

A few smaller decisions worth knowing. A group keeps its identity when somebody
joins — otherwise a family would appear to break up the instant a friend caught
up, and start again as strangers. A group is only placed in a zone when all of
its members are in the same one; straddling a boundary means no zone rather than
a guessed one. And it reads zone arrivals from the tracker's own events instead
of working them out again, so the flicker and dropout handling that took a whole
phase to get right is not quietly second-guessed by a second opinion.

It runs as its own consumer rather than more tracker. The tracker is on the
half-second path from camera to dashboard, and a bug in group detection must not
be able to stop dwell being measured.

**One long-standing mismatch surfaced.** The event spec calls the field
`members` and the browser contract has always called it `memberAnonIds`. Nobody
noticed because nothing had ever sent one of these events. Left alone, a group
would have arrived in the dashboard with no members at all — not an error, just
an empty list where three people should be.

Checked against a live stack rather than only in tests: a couple walking the
lounge and a queue of four at the bar, through the real consumer loops — one
group for the couple, nothing for the queue. Then a trio who stayed five
minutes, after which the query in `data-model.md` returned a row for the first
time since it was written.

### Added — 2026-08-21 — `[neo4j-track]` Anybody can sign up, and the report's buttons finally do something

Three things, and they turned out to be one: the report's Share button needed an
invite endpoint, invites needed organisations, and organisations needed a table
nobody had built.

**Nobody could sign up.** `multi-tenant.md` §4 lists six onboarding steps and
five of them have worked since Phase 2 — the wizard, prefabs, zones,
integrations, consent, run. Step one, "sign up and create an organisation", did
not exist at all. A user existed only if somebody had run a command on the
server. And the login screen has been shipping a **"Create your account" tab**
the whole time: you could make a real Google account, sign in perfectly
correctly, and get `401 unknown user` forever. A Firebase account is only half a
login here — the address still has to map to an organisation and a role, and
nothing wrote that half.

It works now, and goes through **the same identity check as signing in**. That
is the point rather than an economy. This is an endpoint anybody on the internet
can call and it hands out organisations, so the one thing it must never grow is
its own opinion about who somebody is — including refusing outright on a
deployment that has not configured Firebase, exactly as sign-in does.

Two things it will not do, both protecting somebody who is already a customer.
It never files an address into an organisation that already exists by matching
the email domain — you get a new one, and joining somebody else's takes an
invitation. And an address that already has an account is **refused rather than
updated**: one person belongs to one organisation, so "updating" would not add
them anywhere, it would take them out of theirs and leave their sessions, leads
and integrations behind in it.

**The organisation itself had nowhere to live.** Every table has carried a
`tenant_id` since the first migration and none of them ever said which
organisations exist — a tenant was whatever string somebody typed. Fine while
typing was the only way in; not fine when signup has to invent one. The registry
was predicted in the code that needed it, which has said "a real registry is
Phase 6" since Phase 1. That function still reads the event log, deliberately:
the registry answers who exists, the log answers who has work, and consumers
want the second. Otherwise every consumer would poll every organisation that
ever signed up and looked around once, forever.

**The way in for everyone else.** An admin can add somebody to their
organisation, and that is also what "Share with client" on the report now does —
`multi-tenant.md` already defines a Viewer as the client's own stakeholder, so
sharing a report *is* giving them that seat.

It does **not** send an email, and says so. There is no mail server here — the
same absence the follow-up drafter sits behind, where the decision was that it
writes and does not send. The API returns "no email was sent" as a field rather
than a sentence, so a screen cannot claim otherwise by assuming, and the button
tells the operator to send the link themselves. Claiming an invitation went out
when nothing did is the lying button one layer down, and the client finds out.

Two guards. Adding an address that belongs to another organisation is refused
with the reason rather than quietly moving them. And the **last admin cannot be
removed** — an organisation with no admin can never invite anybody, connect an
integration, or honour an erasure request, and there is no way back short of
somebody with database access.

### Added — 2026-08-21 — `[neo4j-track]` The report exports, and what looking at it found

**"Export PDF" and "Share with client" did nothing.** No handler, no print
stylesheet, nothing behind either of them — two primary buttons on the document
the product is sold on. This is the same thing as the invented numbers the report
has been shedding since Phase 2, except worse: a wrong number is discovered
later, a dead button is discovered in front of the client.

Export now prints, through the browser's own print pipeline rather than a PDF
library. The dashboard is a static site with no server to render on, and the
libraries that run in a browser produce a picture of a page — no selectable text,
no real page breaks. "Save as PDF" in the print dialog has always been the thing
that works.

**Three problems only appeared by looking at the printed page**, and none of them
would have been caught by reasoning about the CSS.

The scorecard tiles and the headline ROI figure came out as **black rectangles**.
The theme is redefined for print by flipping the colour variables, which handles
almost everything — but a few surfaces paint with a hard-coded gradient rather
than a variable, and those printed exactly as they look on screen.

A hidden-on-print class worked in the production build and silently **did nothing
in development**, which is where anybody would check it. Everything now uses one
mechanism that was verified in both.

And the brand accent colours are tuned for a near-black background. Measured
against white paper, the mint green comes out at **1.36 to 1** — the accessibility
floor for large text is 3, and for body text 4.5. Four of the six accents failed
even the generous threshold, and the worst of them is what the headline ROI
figure is printed in. Print now uses darker versions of the same hues, all above
4.5. Keeping the colours at all is deliberate: the scorecard means something by
colour, and printing it grey would drop that silently.

### Added — 2026-08-21 — `[neo4j-track]` CI, and the line that made it impossible

There was none. 723 tests — including the ones asserting that a stolen camera key
cannot read the log, that a withdrawal reaches every CRM, and that a privacy mask
is applied before the model sees the frame — ran when somebody remembered.

**One line was the blocker.** The test suite built its database-owner connection
by substituting a developer's own macOS username into the connection string, so
it ran on exactly one machine in the world. There was a second copy of the same
line in another test file, which is how a fix to one of them would have looked
like it worked.

Both now use one setting. Deliberately a **new** setting rather than the existing
admin one, which already means something else and points at the *dev* database:
this connection empties nine tables and bypasses every isolation policy, so
aiming it at real data by reusing a variable name would have been a bad afternoon.
It refuses any URL that is not a test database, for the same reason.

Proven by running the whole suite against a Postgres that is not this laptop's
default — which also turned up a latent flake. One test compares a duration
computed from two different clocks, the application's and the database's, and
asserted an exact five-minute boundary; where the database is in a container they
drift, and it read 299.955 against a required 300. It would have failed in CI
perhaps one run in three, which is the kind of failure that teaches people to
press retry without reading.

**Lint reports and does not gate**, with the reason in the workflow. There are 7
pre-existing lint errors, none from this work. A check that is red from its first
run gets filtered out of everybody's inbox within a week — the same failure the
ops queue was designed to avoid, one level up. Tests, types and build are green
today, so those gate, and a red one means something actually broke.

### Added — 2026-08-19 — `[neo4j-track]` The privacy mask finally masks, and cameras say when they stop seeing

Phase 6's fourth bullet: the calibration UI and CV drift telemetry. Picked next
because it was the only item left in the phase whose event contracts were
already sunk — `drift.detected` and `calibration.updated` were pinned in
`event-bus-spec.md` §3 back in Phase 3 so this producer would not arrive to a
422. Everything else remaining needs a key nobody has or a decision nobody has
made.

**A promise this repo had been making for six months and not keeping.**
`privacy.md` says an operator can draw an opt-out region and that "pixels within
that polygon are masked before any model runs". The session wizard repeats it
during setup, in those words. No code anywhere touched a pixel. Somebody
standing where the operator had marked "do not look" was detected, tracked and
counted like anyone else.

It works now, and the shape is: draw it on the new calibration screen, the edge
box fetches it, and `cv2.fillPoly` blacks it out **before** the frame reaches
YOLO. Not a filter over the results — the model never sees those pixels, so
there is no detection to discard. Checked against a real clip with people on
both halves of the frame: 40 detections in the masked half without the mask,
**0** with it, and the unmasked half unchanged at 60.

**It refuses to start rather than run unmasked.** An edge box that cannot reach
the backend and has no cached mask stops. Same rule as the unset encryption key
in Phase 4 and the unconfigured Firebase verifier last night, and here for a
sharper reason than either: every other failure in this system produces a number
nobody can trust, while this one produces a recording of a person who was told
there would not be one. There is nothing to review afterwards and nothing to
retract. A box that *has* a cached mask carries on through an outage, which is
the case the venue wifi actually produces.

The polygon is deliberately **not** written to the log. `calibration.updated`
records that masking started or stopped, on which camera, and who did it — the
audit trail §3 always said it was — but a booth's sensitive geometry does not
need to be in every replayed and exported copy of the log for that to be useful.

**`camera_id` had nowhere to live.** Both events are keyed on a camera and
nothing in the backend, the graph or perception knew what one was —
`Session.camera_count` was an integer. So `(:Camera)`, graph migration 005,
keyed per session like a zone rather than per tenant like a contact: a physical
camera outlives an activation but a mask does not, and reapplying yesterday's
masked geometry to today's booth would blank the wrong pixels while looking
exactly like a working feature.

**Three of the four calibration kinds are refused, each with its reason.** §3
pins homography, zone map, reader map and privacy mask; only the last has
anything that reads it. Zone geometry already has an endpoint, and a second
writer would give the system two ideas of what the current zones are. Nothing
reads a homography — zones are normalized image coordinates from end to end.
A reader map describes RFID hardware that has no producer. The refusals say
which, the way `/ops` says why it will not retry a tracker dead-letter. A
calibration accepted and never applied is a false line in an audit trail.

### Added — 2026-08-19 — `[neo4j-track]` Drift telemetry, and the metric it refuses to emit

The other half, and one piece of work with the first rather than two.

A camera degrades quietly. The lens fogs, someone hangs a banner, a light moves
— and nothing errors. The confidence drops, tracks fragment, and the activation
simply reports less traffic than there was. `consumers/drift.py` measures two
things per camera against that camera's own first ten minutes of the session,
and says so on `/ops`.

**It does not emit the metric that would have made it useless.** §3 names three:
detection rate, mean confidence, and track length. Detection rate falling means
one of two completely different things — the model got worse, or the room
emptied — and nothing in this system can tell them apart. A booth is empty most
of the time. A detector built on it fires every lunchtime, and within a week
nobody reads the panel, at which point it is worth less than no panel because it
looks like coverage. The two it does emit are per-detection statistics: a quiet
window contributes no samples rather than a low reading. There is a test named
for the refusal, because adding the third metric is the obvious "make it
complete" change and it is precisely the one that breaks it.

**Every event carries both numbers.** Observed and baseline, side by side, on
the event and on the panel — never the severity alone. §3 asks for that
explicitly, and the point is that an operator who can see 0.55 against 0.90 can
disagree with our threshold, while one who can only see "critical" can only
believe it or not.

**A recalibration resets the baseline, and that is what joins the two halves.**
Masking pixels changes what the model sees and therefore how confident it is. An
operator who drew a mask would have set off the alarm — the system reporting
their own correct action as a fault. So a `calibration.updated` starts a new
measurement epoch. Checked live: two windows at a lower post-mask level after a
mask, zero drift events; then a genuine degradation after that, caught.

Built on the insight agent's shape rather than a new one — fixed contiguous
windows on event time, ids derived from the window, and the same `before_seq`
trap that file records — so a replay reproduces the same events and the bus
dedupes them. Verified by rewinding the cursor to zero and running the whole log
again.

**One narrow exception to a security rule, argued rather than slipped in.** Every
read in this system refuses a device credential: "device credentials are
write-only", because a camera key is the one most likely to walk out of a venue.
The edge box has to *read* its mask. There is now exactly one dependency that
admits a device, used by exactly one endpoint, returning a polygon and an
integer. What it hands over is the instruction not to look — refusing it would
not protect a visitor, it would un-mask them.

**What the operator does not get is a camera preview to draw over.** Frames live
in a 60-second buffer in memory on the perception laptop and never leave it, so
there is nothing to show without changing that, which is a decision about the
privacy posture rather than a convenience for one screen. The editor is a
coordinate grid and the shape is confirmed in the perception preview window,
where the masked region is black. Worse to use, and said on the screen instead
of hidden.

**Multi-camera fusion, the bullet's third clause, is split out and not done.**
`perception.detection` now carries an optional `camera_id`, which is the first
thing fusion needed. The rest is a re-key of tracker state: today `anon_id` is
`P-NNN` from one ByteTrack instance keyed on `(session, anon_id)`, so two
cameras collide on `P-001` and two people become one. What fusion can even mean
here is bounded by `privacy.md` — no cross-camera re-identification except by
hand-drawn zone topology — so it is disjoint zone coverage, not re-id. Its own
bake.

### Changed — 2026-08-18 (later still) — `[neo4j-track]` Phase 6 opens: real authentication, and four roles that finally differ

Phase 5 is closed, so this starts Phase 6 with the two things in its first bullet
that made "a third-party operator self-serves signup" unsellable.

**The one open door in the system is shut.** `POST /v1/auth/token` took an email
address and issued that user's token. Its own docstring was candid about it —
"anyone who knows an address can get that user's token" — and it was the only
place that did not verify: every other endpoint checked our own signed token
correctly. It now verifies a Firebase ID token against Google's published keys.

No key was needed for this, which is the reason it could be done now while the AI
provider and Stripe still wait on ones nobody has. A Firebase ID token is an
RS256 JWT signed by Google; verifying one takes the project id — public by
design — and Google's public keys. Nothing to leak.

A deployment that forgets to configure it **refuses**, rather than falling back:
`local` still issues on an email and warns on every one, and every other
environment answers 503. And an unverified email address gets nothing, because
Firebase hands out a working token before an address is proven and `auth_user`
maps an address to a tenant and a role — accept one and signing up as somebody
else's address gets you their organisation.

**Two of the four roles did not exist.** `multi-tenant.md` §3 has specified
Owner/Admin, Operator, Analyst/Marketer and Viewer/Client since Phase 1, and all
four have been in `USER_ROLES` — but only `admin` and `operator` were ever
checked, so `analyst` and `viewer` were both "neither of those" and behaved
identically. A role that changes no behaviour is a label.

The reason they collapsed is that §3 is a **matrix** and the code was a ladder.
An Operator runs activations and does not build agents; an Analyst builds agents
and does not run activations. "reader < operator < admin" cannot say that. It is
a capability map now, and the map reads like the doc's own table.

Read as a deny-list, deliberately, with the two surprising consequences pinned by
tests that name themselves: an **operator cannot query Ask**, and a **viewer
cannot read leads**. Both are one line to reverse, and reversing either now has
to be a decision.

Three things found by doing it. Three test suites had been running against a role
called `"reader"` **that was never in `USER_ROLES`** — the old gate admitted any
human role by name. `caplog` had been returning nothing for the entire suite,
because Alembic's `fileConfig` runs inside a fixture and defaults to disabling
every logger configured before it. And running the verifier on a host with no CA
bundle showed it handing the raw TLS error — URL and all — back to the caller; it
refuses either way, which is right, but the reason belongs in the log rather than
in a reply to whoever is probing.


### Added — 2026-08-18 (later still) — `[neo4j-track]` Phase 5 closed: the insight agent, and the orchestrator explained rather than built

The last two lines of Phase 5. One is built; the other is deliberately not, and
the reasons are now written down instead of being rediscovered.

**The insight agent, to `floats-agent`'s specification, adopted verbatim.** Their
roadmap describes it better than ours did — "periodic bounded graph snapshot →
`insight.generated` with text + supporting event IDs; shown on /live;
click-through opens the underlying events" — so this track takes their wording
rather than inventing a second insight contract, the same move ADR-002 made with
their rule spec.

Their acceptance clause is the demanding half: *insights on /live trace to source
events*. It decided the design. **The digest is computed from the log, not the
graph** — the graph will tell you Product Pod averages 300 seconds and can never
tell you which events say so, because there is no id to carry. An insight built
that way is an assertion a reader takes on trust, which is exactly what the
report's invented `1,287 visitors` was. So every claim carries the ids behind it,
the panel opens them, and a citation that no longer resolves is shown in amber
rather than quietly dropped.

The citations are the *supporting* events, not the window's traffic. Verified
live: an insight claiming 300 seconds at Product Pod opened to the four Product
Pod events whose dwells sum to exactly that, and to nothing else.

**And it walked straight into ADR-002's own trap.** The first version looked up
the previous insight with `before_seq=event.seq`, which can never return one — an
insight is appended at a higher seq than every event it summarises. The lookup
returned None forever, every event recomputed window zero, and the derived id
swallowed the duplicates, so it *looked* like it worked: a two-minute interval
produced exactly one insight for a ten-minute session. ADR-002 records the
identical bug for rule cooldowns. Both fixes are in the code, with the reasoning.

Windows needed both ends moved, too. `read_window` is `(since, until]`, which is
right for the evaluator's sliding window and wrong for fixed contiguous ones: an
exclusive start drops a session's very first event every time, and an inclusive
end counts a boundary event in two windows.

**Three more invented figures gone.** `/live` showed "Visitors who try the Scent
Quiz dwell 2.4× longer", "Bottle Wall captures 86% of gazes", and "Entry Arch is
dropping 38% of visitors within 30s — queue signage unclear" — the last one
asserting a *cause*, which is precisely what the insight prompt now spends a rule
forbidding. We measured where people walked; we did not hear a word they said.

**The floor orchestrator is deferred, and now says why.** It has two unmet
preconditions, not one. Its own stated gate — no pilot has asked — and a
trajectory-prediction model, which a research note on `floats-agent` names as its
input and which this repo neither has nor has chosen. Neither track has built it;
neither has more than six words of specification. Building it would have meant
inventing the spec and then writing the acceptance criteria to test against it.

Phase 5 is otherwise complete. Every item is built or deferred with its reasons
recorded, and the phase stays 🟡 for one reason only: open decision 2.


### Added — 2026-08-18 (later) — `[neo4j-track]` Phase 5: Ask answers off real data, and a follow-up that names where somebody stood

Two of Phase 5's four items, and the one Phase 2 never finished. No AI provider
has been chosen — open decision 2 has stood since 3 August — so everything except
the model itself is built and tested, and a deterministic provider stands where
the model goes. Both items are 🟡 rather than ✅ in the roadmap, and the reason is
written there rather than glossed.

**The model picks a question; it never writes the query.** The roadmap asked for
"constrained Cypher (allow-list, validated)", and the reading where a model
writes Cypher and a validator approves it does not survive contact with the
language. The dangerous failure is not `DETACH DELETE` — that would at least be
obvious. It is correct, valid, keyword-clean Cypher that omits `tenant_id` and
returns a confident answer about every client the deployment has ever hosted.

So the model's whole output is a name and some parameters, the queries are
hand-written and reviewed, and the tenant and session are injected by the caller
and are not parameters any entry declares. That is `docs/adr/003-nl-query-catalogue.md`.

**What Ask replaces returned invented numbers.** Nine regexes, a demo gate, and a
sidebar reading "Claude 3.5 Sonnet — 420 ms · Total 902 ms" describing a call the
system had never made. Deleted, on the same grounds as the report's `1,287
visitors` and `/agents`' `fired: 488`. Every figure now comes from the graph or
the log, and the one latency shown is measured.

**With no key, a deterministic matcher answers and says that it did.** It is not
the mock wearing a hat: it matches a question to a catalogue entry by wording and
then runs the real query, so the figures are measured. The only guess is the
match, and a tie or a miss is a refusal with the list of what it can answer.
Every response carries its basis, because an operator who cannot tell a model's
answer from a keyword match cannot judge either.

**The follow-up drafts and does not send.** There is no email provider, and an
unreviewed model-written email to somebody who agreed to be contacted is not
something to put on a cron. The draft carries `groundedIn` — the exact zones,
surfaces and dwell it was allowed to reference — so a reviewer checks a sentence
against the measurements rather than trusting it. We measured where somebody
walked; we did not hear a word they said.

**And the T2 gate turned out not to exist.** `consent-and-identity.md` has said
since Phase 4 that CRM sync refuses below T2 and that this is "enforced in code
(the bus consumer), not by convention". Nothing read `tier`. T1 is "take my
details" and T2 is agreeing to be contacted — different sentences a visitor picks
between — so every consented lead had been going to every connected CRM
regardless. One shared gate now, used by the delivery and the draft.

Two things found by writing the tests. `followup.drafted` was in the erasure's
PII list but not in its subject matcher, so an erasure would have taken the name
out of the envelope and left a letter reading "Hi Sam" on the log. And the
anonymous handoff carries no consent block, so the new gate refused it — which
looked right and was exactly wrong: consent permits acting on a person, and a
contactless row of zone dwells names nobody.

Verified end to end over HTTP against the real stack: a T2 visitor's draft named
the zone they dwelled longest in and the surface they touched, the T1 visitor
beside them got none, and five questions were checked answer by answer against
the graph they came from.


### Fixed — 2026-08-18 — `[neo4j-track]` seven review findings, four of them about identifiers that are not unique

A review over the Phase 4 commits found seven. All seven were real, and the four
serious ones are the same mistake made four ways: **an identifier treated as
globally unique when it is not.**

**An erasure could reach into somebody else's activation.** `app/erasure.py`
refuses to hold a bare `anon_id`, because `P-012` at two events is two people —
and then took the `dedupe_key`, which is `tenant:email` only when there *is* an
email. Without one it is `tenant:P-012`, not session-scoped, identical for a
stranger with the same track id elsewhere. Erasing ours walked their handoff,
their track, their capture and their contact, redacted a name they had given us
and deleted their Contact. The existing cross-session test missed it because its
second visitor had an email. Keys are now taken only from a handoff that carried
one, which is exactly when the key names a person rather than a track.

**Erasing two people made them one lead.** The redacted key is one value per
tenant, and that was safe while the collapse happened at read time, after the
ledger had grouped rows. The erasure writes it into the log, so two erased
visitors merged onto one row — undercounting leads, keeping one of their contact
ids, and totalling both their outcomes as one person's revenue. The rewritten key
now carries a discriminator, chosen per erasure so that a person's handoffs and
their outcomes still meet.

**Withdrawn people could be exported by name.** The pull API read withdrawals with
`limit=1000` — which *is* the cap — from seq 0, so it got the oldest thousand and
dropped the rest. The dropped ones are the recent ones: the people most likely to
be in the page being exported. And it filtered them by the caller's `sessionId`,
while a Contact id is stable across activations — so somebody who attended two
events and withdrew at the second was un-redacted in the first. Both fixed, and
the session-scoping half was fixed in the ledger too, where it had been since
before this branch and would have printed the name on a CFO-facing page.

**The anonymous-handoff flag delivered to nothing a client had configured.** The
delivery consumer skipped any handoff with no contact before claiming, which kept
several hundred pointless rows a day off `/ops` and also meant an operator who
turned the flag on and connected a Zap hook got silence. Destinations decide now:
a CRM declines — there is no record to create for somebody who was never named —
and a bring-your-own hook accepts, because its receiver is counting reach.

Two smaller ones. Zoho reports a record it cannot find inside an HTTP 200, so the
`404` arm never fired and a replayed withdrawal for an already-deleted lead
retried to exhaustion and parked claiming a failure that had succeeded. And the
OAuth refresh margin used `max(lifetime - margin, margin)`, which for any token
under two minutes cached it *past* its own expiry.


### Added — 2026-08-17 — `[neo4j-track]` Phase 4 finished: four more CRMs, the leads nobody named, and erasure

Phase 4's acceptance has held since 14 August. What was left was the width the
phase promised and had not delivered: the four Tier 1 CRMs after HubSpot, the
remaining bring-your-own destinations, the anonymous handoff `integrations.md`
has allowed since it was written, and the GDPR Article 17 erasure job that
`consent-and-identity.md` §5 names and nothing implemented.

**Four more CRMs, and what each one refuses to do.** Salesforce, Pipedrive, Zoho
and Dynamics. Three decisions had to come first. A credential is not always one
string — three of the four are OAuth2 and need four or five fields — so the
secret stays one opaque column and theirs is a JSON document, checked at `PUT`
rather than at the first lead of a three-day activation. Three of them have no
upsert we can use unaided, so `upsert` is now handed the id `crm_link` already
recorded and the search-then-create `hubspot.py` argues against became the last
resort rather than the only thing deciding. And Zoho refuses records inside an
HTTP 200, in the `data` array where SUCCESS would be, so every call reads the
body — a destination that reported success because the transport succeeded would
put leads on the delivered pile the CRM had thrown away.

Also: Zoho's datacentres are separate accounts and the wrong one authenticates as
a bad token, sending an admin off to rotate a credential that was fine, so the
region is declared and checked. And three of these CRMs will not create a lead
without a surname and a company. The answer is a placeholder that says nobody
gave us one, never "Smith" derived from smith@acme.com — that is a guess wearing
a real name's clothes, and the CRM would never show it as one.

**The visitors nobody named get a handoff too, if you ask for one.** One per
person with no live identification, at session close. It covers somebody who
never consented and somebody who consented and then withdrew, because a
withdrawal returns a person to the anonymous path. It is off unless the operator
turns it on, which is the opposite of every other session setting: a busy day is
several hundred of them and they reach the same destinations a real lead does.

Two things it broke that were worth finding. `totals.leads` counts every row, so
switching this on would have turned the one number a CFO reads off the ledger
from "people who gave us their details" into "people who walked in", without the
label changing. And `/ops` would have filled with a dispatch row per visitor per
connected CRM, each recording that nothing was sent.

**Erasure, and the one column that admits the log is not quite append-only.**
Withdrawal was already doing everything §5 literally asks for. The visitor's
email was still sitting in `consent.captured` and in every handoff built from it,
and an erasure that leaves those is not an erasure. So `POST /v1/erasure` appends
the ordinary withdrawal — the whole existing path runs first, unchanged, because
an erasure taking its own route to the CRMs would be a second implementation of
the thing a person's rights depend on — plus a request for the part it cannot
reach.

It rewrites `payload` and nothing else. No row deleted, no sequence number
reused, so a replay reproduces the same events in the same order with a name
missing from a few of them. That is weaker than "the log never changes" and it is
the guarantee the law leaves us.

It refuses to run until the retraction has actually landed, and that ordering is
a correctness condition. Erasing the Contact first would delete the record the
re-anonymiser reads to build `crm.retract`, so no retraction would ever be
emitted and the copy in the client's CRM would stay there — an erasure reporting
success while leaving the data where it mattered most.

The trap inside it was anon ids. `P-012` at two activations is two different
people, and `privacy.md`'s whole no-cross-session-re-identification claim rests
on that, so a track is held as `(session, anon_id)` and never bare. Erasing one
visitor's events at somebody else's activation would be data loss that looks like
compliance.

**The rest of bring-your-own.** Zapier and Make are destinations in the same
registry the CRMs are in, because a hook needs a per-tenant credential, a claim,
a retry and a row on `/ops` and nothing else. `GET /v1/handoffs` is the pull
half — no inbound port, nothing to sign-verify, cursored on the log's own seq so
a client that stops and comes back gets every lead since with no gap and no
duplicate — with `?format=csv` for the offline clients. There is deliberately no
scheduler: a job runner emailing a file on Tuesdays would be a second place a
client's leads leave the building, with its own retry story and its own way of
failing quietly.

**One bug found by running it rather than by testing it.** An erasure that named
only a consent id left the ledger row showing a missing name beside "not
withdrawn" — the withdrawal matchers look for contact ids and tracks, and a kiosk
receipt has neither. The evidence was already in the row: a dedupe key that no
longer names anybody. Both the ledger and the pull API now read it.

**Left open on purpose:** the T3 enrichment adapter. Optional from the start, and
it needs a provider key that does not exist in this repo — the same block Ask the
Room sits behind.

**Caveat worth stating plainly:** no live account exists for any of the five
CRMs. Each adapter is proven against the API its vendor documents and against
nothing else. The first real portal will find something; what it should not find
is a duplicated lead or a withdrawal that did not go.


### Added — 2026-08-14 (later) — `[neo4j-track]` the lead lands in HubSpot, and a withdrawal takes it back out

Phase 4's acceptance has four clauses. Three have been true since yesterday.
This is the fourth: *"a LeadHandoff lands in HubSpot with spatial_intent fields,
a withdrawal retracts it"*.

**The upsert is HubSpot's own, and that is the whole of the duplication
argument.** `POST /crm/objects/{version}/contacts/batch/upsert` with
`idProperty: "email"` creates or updates in one call. The obvious alternative —
search by email, then create or patch — has a race the trade-show floor would
find within a day: the two handoff stages of one visitor can be in flight
together after a retry, both search, both miss, both create.
`integrations.md` §2 states the requirement and the cost of missing it — "a
create-only implementation will duplicate every lead in the client's CRM" — and
a search-then-create is a create-only implementation with extra steps.

**`crm.retract` finally has the reader `event-bus-spec.md` said it had.** The
re-anonymiser has emitted it since Phase 4 opened and nothing consumed it, so a
visitor who withdrew had their local record redacted while the copy already
pushed to a client's CRM sat there untouched. That is the half of
`consent-and-identity.md` §5 that leaves the building, and it was missing.

Which needed the thing the re-anonymiser's own comment asked for. `destination`
was `"all"` because *"until the attribution consumer records where a handoff
actually went, the honest value is 'everywhere' rather than a named CRM this
deployment may not even use"*. `crm_link` (migration 0008) is that record.

**The link cannot live on the graph Contact, and the reason is the same one the
ledger has.** A withdrawal redacts the Contact — so a note stored there saying
"pushed to HubSpot as 51234" would be erased moments before the retract consumer
needed to read it. Where we sent somebody has to outlive the record of who they
were. And because the link holds the `dedupe_key`, which is `tenant:email`, a
successful retraction redacts it — with `attribution/ledger.py`'s own function,
imported rather than copied, because two redactions are two chances to disagree
about which half of the key names a person and the disagreement shows up as an
email surviving a withdrawal. `external_id` stays: it is HubSpot's identifier
for a record we have just asked HubSpot to remove, and an audit that cannot name
the record cannot check that it went.

**What HubSpot's delete actually does is what the row says it did.** It moves
the contact to a recycling bin the client can restore from for 90 days. That is
a removal, not an erasure, and the dispatch detail says so rather than letting
"retracted" imply more than happened. A true erasure needs account-level GDPR
features this adapter does not assume, and it belongs with Phase 4's erasure job
— where the same question has to be answered for every destination at once
rather than invented here for one.

**Three judgements about failure, each the opposite of the easy version.**

A handoff with no email is **declined, not invented**: HubSpot has nothing to key
it on, a contact built from an `anon_id` is a person no salesperson can ever
contact, and the claim closes as delivered-with-nothing rather than parking a row
on `/ops` that no human action could resolve. A 429 is **raised, not swallowed** —
`base.Consumer` already owns retry and bounded backoff, and a second policy
inside the adapter would disagree with the first. And one CRM failing does not
stop the others: each destination is attempted, then the handler raises, so the
retry reaches only the destinations that did not deliver — their claims refuse
the rest.

**Retraction is retryable from `/ops` where delivery is not**, and it is not
metered. Retryable because the parked event is a person's withdrawal not yet
honoured and that should be one click rather than a cursor rewind — and because
a repeated retraction is the one outbound call whose second attempt is harmless,
since it finds the record already gone. Unmetered because `crm_delivery` meters a
push as work a client is buying, and a withdrawal is not: putting it on the cost
tile would mean a client's unit economics get worse the more withdrawals they
honour.

**`/ops` grew a third kind, and the screen had to be told.** `kind` was `rule` or
`handoff`; a retraction is neither, and the panel would have rendered it as a
rule and then announced that the rule had been deleted — about something that
never was one. It is now its own pill, with the copy an operator needs: this is
the most urgent row on that screen, and the only one whose deadline is set
outside this company.

Seventeen new backend tests (331 total) and one new dashboard test (141).

### Added — 2026-08-14 — `[neo4j-track]` the thing the CRM adapters were blocked on

`roadmap.md` has carried the same parenthesis against the CRM adapters for two
weeks — *"blocked on a per-tenant credential store, which nothing in the repo has
yet"* — and `routers/outcomes.py` names it in its own docstring as the reason a
finance team's numbers are typed in by an operator instead of read from HubSpot.
`multi-tenant.md` §2 has asked for the store since Phase 1, in one sentence:
"each tenant's CRM/enrichment credentials are stored encrypted, per-tenant, never
shared." This is that sentence, and each clause of it is a test.

**An unset encryption key refuses to store the credential rather than storing it
in the clear.** The same decision `actions/webhook.py` takes about its signing
secret, with more at stake: a deployment that quietly downgraded here would put a
client's CRM token in a database column, and nobody would find out until the
database did. There is no plaintext column for it to fall back to and no shipped
default key — a default here would be a published key protecting somebody else's
CRM.

**The ciphertext is bound to the tenant and provider that own it.** AES-GCM
rather than Fernet for one reason: it takes associated data, so `tenant:provider`
is authenticated alongside the secret and a row copied sideways — a bad restore,
a careless fixture, an admin moving rows between tenants — fails to decrypt
instead of handing tenant A's token to tenant B's delivery consumer. Row-level
security stops a *query* crossing tenants; it cannot stop a row written into the
wrong one, and this does.

**The secret goes in and never comes back out.** No endpoint returns a stored
credential, and that is not a gap for a later "reveal" button to fill. What a UI
actually needs is the answer to *is this the key I pasted?*, which is
`secretHint` — the last four characters, and nothing when the secret is short
enough that four would be most of it. Decryption happens in exactly one function
(`crm.adapter_for`), which is what makes "the plaintext never leaves this call"
checkable rather than a convention.

**Admin, not operator, and the line is worth a new dependency.** `require_admin`
joins `require_reader` and `require_operator`. `multi-tenant.md` §RBAC puts
integrations with Admin, and it holds up: an operator arms a rule that posts to a
room and the blast radius is a Slack message, while a credential writes into the
client's system of record and outlives the activation.

**A provider with no adapter is refused.** The event taxonomy is open by design —
a rule may name a trigger whose producer has not shipped — and providers are the
exact opposite: a credential stored for something nothing dispatches is a live
token sitting in our database achieving nothing, under an admin who believes
their CRM is connected. Adding a CRM is a module and a line in
`app/crm/__init__.py`'s registry, the same shape `app/actions` uses.

**Two smaller judgements.** Re-storing a credential clears the previous
healthcheck rather than carrying it forward, because a new token has not been
tested and inheriting a verdict — green or red — is a claim about a credential
nobody has tried. And revoking keeps the row *and* the ciphertext: `DELETE` is
the verb, revocation the effect, because a withdrawal arriving the day after an
admin disconnects HubSpot still has to authenticate to HubSpot to retract the
contact.

`integrations.md` §3's interface is now real (`app/crm/base.py`) with three
things the doc left open decided: `authenticate` is construction rather than a
method you can forget to call, an `upsert` that finds nothing to key on returns
`None` instead of raising — an anonymous handoff is ordinary, not a failure for a
human to resolve — and every real failure is raised, because `base.Consumer`
already owns retry, backoff and dead-lettering and a second retry policy inside
an adapter would disagree with the first.

**Not built, and named rather than implied.** There is no dashboard screen for
pasting a token. The field-mapping editor `integrations.md` §3 asks for is a
screen of its own, and shipping the half that only holds a secret invites the
mapping half to be improvised later. API-only for now.

Seventeen new backend tests (314 total).

### Added — 2026-08-13 (later still) — `[neo4j-track]` the ledger a CFO asks for, and the outcome nothing had defined

Phase 4's acceptance ends "…and the attribution ledger reconciles booth-touch →
outcome". Two things stood in the way, and only one of them was known.

**Nothing in this repo defined an outcome.** `data-model.md`'s node list stopped
at `Frame`. There was no `Deal`, no `Outcome`, no `outcome.` namespace. Every
attribution claim the docs make — the whole of `roi-framework.md`'s Layer 4, the
ROI ratio, the 3:1–5:1 benchmark — rested on a thing that did not exist, and the
CRM adapters would have had to invent it in passing.

So: `(:Outcome)` in graph migration 004, `outcome.recorded` pinned in
`event-bus-spec.md` §3 and classified PII, and `POST /v1/outcomes` for an
operator to record one. Keyed per tenant rather than per session, the same
asymmetry `Contact` has and for the same reason: an `anon_id` is never reused
across sessions so a `Person` is session-scoped, but a deal belongs to the client
and not to the activation it started at. Keying it per session would say a deal
touched by two activations was two deals, which is exactly the double-count
`roi-framework.md` §3 exists to prevent. `dedupe_key` is indexed rather than
unique, because one lead can legitimately produce an opportunity, then a close,
then a renewal, and a unique index would refuse the second while silently keeping
the first.

`closed_at` is required for `won` and `lost` and refused politely without one:
the attribution window is measured against it, and an undated close cannot be
judged inside or outside one. `open` needs none — an opportunity still in play is
most of a B2B pipeline at any moment, and forcing it to won/lost would make the
ledger claim resolutions that have not happened. The endpoint is operator-only,
unlike consent capture: a kiosk on a device credential needs to record a yes, but
nothing on the floor has business declaring that a deal closed.

**The second obstacle: `revenue_influenced` was still typed in by a human.**
`upsert_session`'s own docstring said so — "influenced revenue comes from CRM
attribution, which is Phase 4". This is the change that makes that sentence true.
The ROI ratio is now computed from recorded outcomes falling inside the agreed
window, and the client's stated figure is shown *beside* it rather than replaced
by it, because where the two disagree that difference is the most interesting
number on the page.

**The ledger is built from the log, not the graph, and that is the whole design.**
An auditor is asking what was known and when. The graph is current state: a
withdrawal deletes the `IDENTIFIED_AS` edge and redacts the Contact, so a ledger
built from it could not show that the touch ever happened — the row would simply
be missing, which is the one thing an audit trail must never do. The log keeps
each `handoff.lead` exactly as it went out, consent snapshot included.

Which creates the obligation that shapes the rest of it. The log is append-only,
so a withdrawn visitor's name is in it forever — and the ledger therefore
**redacts at read time**. The row survives with its touch, its timestamps, the
consent basis that was in force and the fact of the retraction; the name and
email go, and so does the half of the `dedupe_key` that carries the email, since
leaving that whole would undo the redaction one column to the left. The answer to
"did you stop using their data" is that row being there and being empty of them.

**Two judgements, stated rather than assumed.** The window is inclusive at its
boundary — a deal closing on the 90th day of a 90-day window is inside it, and
anything else means the window a client agreed to is silently a day shorter than
the number they signed. And an outcome outside the window stays on the ledger,
marked, excluded from the total: `roi-framework.md` §5's "ages out of active
attribution but stays in the audit ledger", verbatim.

**And one refusal.** `influenced`, `first_touch` and `last_touch` all reduce to
the same binary decision here, because a booth is the only touch realmspace
observes — we cannot know whether it was first, last or seventh in a journey we
do not see. `linear` and `time_decay` ask for a *share* across that journey, and
a share computed over one known touch is 100% with arithmetic painted on it. So
those two produce no attributed value and say why. It is the only place in this
repo that declines to produce a number a client explicitly asked for, and it
belongs in the same family as the report's blanks: `roi-framework.md` §3 says we
never inflate, and this is what that costs.

An outcome naming a lead the activation never produced is kept and flagged rather
than dropped. That is the discrepancy an audit is looking for, and tidying it
away would hide it.

**The one-pager and the ledger are one screen** (`/ledger`), because they are the
same artifact at two zoom levels: cost in, ROI out, versus benchmark, with the
attribution model stated next to the number it produced — then the evidence
underneath, exportable as CSV. The ROI ratio and the benchmark verdict were
inline inside `computeScorecard`; they are now exported from it and shared, so
the report and the one-pager cannot disagree about the figure a CFO reads first.
The CSV is fetched with a bearer token rather than linked to, since an `<a href>`
cannot carry one and a ledger endpoint accepting a token in the query string
would put PII-bearing credentials into every proxy log between here and the
backend.

**`/ops` was never in the navigation.** It has been unreachable since the HITL
queue shipped — a review queue nobody can navigate to is a review queue nobody
reads, which is the exact failure that screen was built to avoid. Added
alongside `/ledger`.

**Two things found and not fixed.** `/report`'s "Export PDF" and "Share with
client" buttons have no `onClick` — decorative, the same class of thing as the
invented figures removed in Phase 2. And a test in this batch seeded graph nodes
under `t_floats`, which `conftest.py`'s fixtures only wipe for `t_test*`; the
stray node was removed from the dev instance and the test re-scoped, but nothing
stops the next one doing it — the guard lives in a docstring rather than in the
fixtures.

Twenty-five new backend tests (297 total) and six new dashboard tests (140).

### Added — 2026-08-13 (later) — `[neo4j-track]` a consented visitor becomes a lead, and the lead leaves the building

The morning's work produced a Contact linked to a spatial path and nothing that
did anything with it. This is the attribution consumer that turns that into a
lead, and a destination that receives one.

**A handoff goes out twice, and the pairing is the whole design.** A trade-show
lead is worth most while the visitor is still on the floor, and a handoff sent at
that moment carries an incomplete path — they have not finished walking it.
Sending only at `session.ended` gives a complete lead that arrives after everyone
has gone home. So both: one on `identity.resolved` with the path so far, one at
session end with the whole of it.

What makes that safe is two things pulling in opposite directions. The pair carry
**the same `dedupe_key`**, which is every adapter's upsert key, so the final
handoff updates the lead the early one created rather than adding a second. They
carry **different `event_id`s**, derived with the stage in the key, because the
bus dedupes on `event_id` and an id derived from the contact alone would make the
second handoff disappear. Both failure modes are silent: identical ids lose the
complete path, random ids duplicate every lead in the client's CRM.

**A withdrawn contact produces nothing, including on a replay.** Both triggers
read the live `IDENTIFIED_AS` edge rather than the log, and the re-anonymiser
deletes that edge — so somebody who changed their mind is simply absent, the same
property the identity consumer has for re-reading a capture. The `session.ended`
fan-out is driven off the graph for exactly this reason; replaying the session's
`identity.resolved` events instead would rebuild handoffs for people who had
since been re-anonymised.

**`integrations.md` §2 asked for two numbers and did not say where either comes
from.** Both were filled in, and both are now pinned in `event-bus-spec.md` §3
with the reasoning rather than left as a shape.

`attention_score` was illustrated in the doc as `0.82`, which reads as a 0–1
ratio. `roi-framework.md` §2 defines dwell-weighted attention as
`Σ(dwell × weight)` — a quantity in seconds — and there is no denominator
anywhere in the framework that turns one into the other. Normalising it here
would have meant inventing that denominator, so the value is the definition and
`attention_basis` names the unit. The doc's example is corrected rather than the
number bent to fit it.

`lead_score` had no model anywhere in the repo. It is now a stated formula:
three ratios of what the visitor did to what the activation *offered* — dwell
against the session's engagement threshold, funnel depth against the deepest
configured zone, surfaces used against surfaces present — weighted 0.5/0.3/0.2,
with dwell dominant because it has the least inference in it. Every denominator
is the operator's own configuration rather than a constant in the file, which
matters: a booth with one product pod and a booth with six must not be scored
against the same idea of "engaged". A component nobody configured is **dropped
and the rest renormalised**, not scored zero — that would punish a visitor for a
setting their host never filled in. With nothing computable the score is `null`,
never `0`, because a CRM sorting by score would otherwise rank "we could not
tell" alongside "not interested". `lead_score_basis: "spatial/v1"` ships beside
it for the reason `spatial.tagged` carries `method`: a score in a CRM outlives
the formula that made it, so the version is never redefined in place.

`activation_cost_share` is null at the early stage. Its denominator is how many
leads the activation produced, which is not knowable while the doors are open —
a share against a partial count changes every time somebody else scans a badge.

`attribution_window_days` joins the session config, a closed 30/60/90 rather than
a free integer, defaulting to 90. A window widened after the fact to capture a
deal that closed late is exactly the argument `roi-framework.md` §3 rules out,
and an arbitrary 47 is a number somebody chose to make a ratio work.

**The lead is delivered signed, and idempotently.** `consumers/handoff_delivery.py`
POSTs to `settings.handoff_webhook_url` using `sign()` imported from
`app/actions/webhook.py` — that file's docstring asked for precisely this ("Phase
4's adapter should use `sign` from here rather than growing a second one"), and
two implementations of an HMAC scheme are two chances to disagree about which
bytes are covered, which a receiver cannot debug.

Idempotency is the Phase 3 claim rather than the receiver's problem. The webhook
action argues that a receiver can dedupe on `X-Realmspace-Event-Id`, and it can —
but that is a fallback. A POST that times out after the receiver processed it is
ambiguous from this side, and the honest resolution is the one `rule_dispatch`
already implements. Migration 0006 widened that table with a `kind` column rather
than growing a second table with the same three states and a second copy of the
reasoning: it always existed for its UNIQUE constraint on *a cause and an
outbound act*, and a rule firing was only ever the first kind of cause. The claim
works without `kind`; what it buys is that an operator on `/ops` looking at a
stranded row whose `rule_id` column holds a session id is told which of the two
they are looking at, because a stuck Slack post and a stuck lead are different
urgencies.

**No destination configured is not a failure.** Handoffs are still built and
still on the log, and a destination added next week reads them from seq 0.
Parking them instead would fill `/ops` with leads nobody asked to deliver — the
opposite of `slack_webhook_url`'s unset behaviour, and deliberately, because a
rule action names Slack explicitly while nothing has asked for this one.

**Not built, and named rather than half-done.** Anonymous handoffs, which
`integrations.md` §2 allows, need a different trigger — every person, not every
consent — and a different consent story. The CRM adapters are blocked on a
per-tenant credential store nothing in the repo has yet, which is why the webhook
went first: §5 calls it "the contract, raw … also how *we* dogfood new adapters
before writing them". And `Person.attention_score` is still never written by
anything; the handoff computes attention from the edges instead, and fixing the
stale property has a backfill question of its own.

Twenty-six new backend tests (272 total) and one new dashboard test (134).

### Added — 2026-08-13 — `[neo4j-track]` two things Phase 3 claimed but had not proved, and the beginning of consent

Phase 3's boxes were all ticked yesterday. Two of the things they claimed were
not actually true, and both are the kind that stay invisible until a client hits
them. Closing those, then opening Phase 4 with the part everything else in it
depends on.

**The `< 3s` had never been measured.** The acceptance test calls `run_once()`
three times in process order, with no poll interval anywhere, and asserts nothing
about time — so the answer it gives is always "fast", including in a build where
the real deployment takes twenty seconds. `tests/test_phase3_latency.py` runs the
consumers through their real `run_forever()` loop at the configured intervals,
ingests over `POST /v1/events`, and starts the clock at the ingest response of
the event that makes the rule true. **~1.1s**, against a local stack — consistent
with the three poll hops between the tracker, the evaluator and the dispatcher.
What that covers is the number of intervals a firing waits through, which is
the part that would regress silently; a real webhook's network and a smaller edge
box are on top of it, and the roadmap now says so rather than implying the number
is the whole story.

One thing worth recording because it contradicts a comment in the code:
`run.py`'s consumer *order* matters only to `--once`. `main.py` starts them with
`asyncio.create_task` and they poll independently, so registration order does not
serialise the live path at all.

**A dispatch nobody could answer for had no way to be answered.**
`claim_dispatch` refuses a row sitting at `claimed` and says why — a process died
between the claim and the outbound call, and whether Slack got the message is
genuinely unknowable from this side, so it "needs a human rather than a guess."
There was no way to ask one. `get_dispatch`'s docstring said it was "for `/ops`"
and no route read it, so a crash mid-call blocked its
`(fired_event_id, action_type)` pair permanently. It was the one state in the
whole Phase 3 chain with no exit.

`GET /v1/dispatches/stranded` and a verdict endpoint now record what a person
went and found: **it arrived** closes the row and keeps the claim taken, so a
replay still cannot post twice; **it never arrived** releases it, because
`claim_dispatch` takes a `failed` row back. Neither re-sends. The dispatcher's
cursor is long past that firing, so a redelivery needs the firing replayed, which
is a cursor rewind and deliberately an operator action rather than a button — the
same reasoning that makes `DispatchConsumer.retryable` False. The panel on `/ops`
sits in its own section with no retry control at all, because the easy version of
this feature is a retry button and a retry button is exactly the double-post the
`rule_dispatch` table exists to prevent.

`resolved_by` is a new column rather than a line in `detail`, and the distinction
it keeps is worth the migration: a `delivered` the dispatcher wrote is a 200 from
Slack, and a `delivered` an operator wrote is a person saying they saw the
message. Both are legitimate. They are not the same evidence.

There is also a staleness cutoff, and it is not a tuning knob. Every dispatch is
`claimed` for the duration of its outbound call — that is the whole idempotency
mechanism — so listing them all would put each healthy Slack post on the
operator's screen as a problem to solve, and a queue that is mostly false alarms
is a queue that stops being read.

**Phase 4 opens with consent, which is the gate everything after it needs.**
`POST /v1/consent` records a consent exactly as it was given — tier, basis, and
the versioned copy — and appends `consent.captured`. It writes nothing else: no
Contact, no graph, no identity. Asking for permission and acting on it are
separate decisions, and putting them in one function means the record of what
somebody agreed to is written by the same code that benefits from the answer. It
also makes the endpoint honest about failure: a kiosk can still record a yes when
the graph is down.

`consentId` comes from the capture surface, generated before the copy is shown,
which is the same argument `event-bus-spec.md` §2 already makes about `event_id`.
A kiosk on conference wifi will resend after a timeout, and two consent records
for one conversation differing only in id is precisely the state that makes "what
did they actually see?" unanswerable.

`copy_version` is required for the same reason, and it is the load-bearing field
rather than the tier: a tier says what somebody was asked for; the copy version
says what they read before agreeing.

**The identity consumer draws the link, and the gate is one layer below it.**
`consumers/identity.py` turns a capture into `(:ConsentEvent)`,
`(Person)-[:IDENTIFIED_AS]->(:Contact)` and `identity.resolved`.
`consent-and-identity.md` §3 says the edge "cannot be created unless a
non-withdrawn ConsentEvent of the required tier exists in the same transaction …
enforced in code (the bus consumer), not by convention." It is enforced inside
`graph_repo.identify`'s single Cypher statement instead — a check in the consumer
is a check a second caller can skip, and "the consumer remembers to look" is the
convention the doc is trying to replace.

The property that arrangement exists for is this: **the log is append-only, so a
capture that was later withdrawn is on it forever, and replaying it must not put
the link back.** A cursor rewind, a crash, or an operator replaying a session
would otherwise silently re-identify somebody who asked not to be, and every
guarantee in `privacy.md` would be decorative. That test is the one worth reading
in `tests/test_identity.py`.

A refusal is logged and consumed, not raised: it is a decision, and parking it
would ask an operator to un-refuse a correct one. A *missing Person* raises,
because that is usually the graph writer being one poll behind and is worth
retrying — the two failures look identical through `identify`'s return value,
which is why `person_exists` exists to tell them apart.

Contact ids are derived, never random. Where an email is given it is the key, so
the same visitor consenting at two activations is one Contact and attribution has
something continuous to attach to. Where none is given the consent id is the key,
so two anonymous captures stay two people — merging people we cannot identify
would be inventing a fact, and §2's redlines put cross-activation
re-identification behind T3 rather than behind a guess.

**Withdrawal is a separate consumer, and most of its design is about what it must
not touch.** `consumers/reanonymise.py` drops the `IDENTIFIED_AS` edge, redacts
the Contact to a tombstone, stamps the ConsentEvent `withdrawn_at`, and emits
`crm.retract`.

The Person, its zone edges, its dwells and every figure derived from them stay
exactly as they were. That data was never consent-gated — the anonymous path runs
with no consent at all — and deleting it would silently rewrite reports already
delivered about a person those reports never named. The ConsentEvent survives
too, stamped rather than deleted, because it is the evidence that permission was
given and then withdrawn: a deployment that erased it would have nothing to
answer with if the withdrawal itself were ever disputed.

What goes is the link and the PII, which is all consent ever granted. Setting a
property to NULL in Neo4j removes it outright rather than storing a null, which
is a stronger erasure than the `SET` reads like.

`crm.retract` goes on the bus rather than at a CRM, for the same reason
`rule.fired` does not post to Slack itself: a slow third-party API has no
business inside the path that makes a withdrawal true locally. No adapter reads
it yet, and it is emitted anyway — a withdrawal that happened before the adapters
ship still has to be retractable by them, and an adapter starting from seq 0 will
find it.

Graph migration 003 adds `Contact` and `ConsentEvent`, both keyed per tenant
rather than per session. The asymmetry with `Person` is the point: an `anon_id`
is never reused across sessions, so the same one at another activation is a
different human — but a Contact is the same person at every activation they
attend, which is the only reason attribution has anything to attach to. There is
deliberately no unique index on `Contact.email`: a person may legitimately
consent under one address twice, and a constraint would refuse to record consent
somebody actually gave.

Forty-one new backend tests (246 total) and seven new dashboard tests (133).

### Added — 2026-08-12 — `[neo4j-track]` the booth can act now: rules fire, and something happens in the room

Yesterday's entry said this track was leaving the evaluator alone until the
bake-off compared the two backends, because `floats-agent` shipped one on
2026-08-10 and building a second is a week spent on work half of which gets
thrown away. That was the right call with the information available and it is
wrong now, for two reasons that only became visible while writing ADR-002:

- **The shipped evaluator is the version the ADR says is wrong.** All four of
  its corrections — bus-derived window, event-time cooldown, derived firing id,
  `none` judged at a boundary — describe things `rules.py` does differently.
  Building the corrected one is not a duplicate; it is the thing the ADR was
  written to specify, and whichever track wins inherits it.
- **None of it touches the graph.** Rules live in the bus's SQL database by
  ADR-002's own reasoning, so this survives either bake-off outcome intact.

So Phase 3's remaining boxes are ticked. **"When 5 people dwell at the entrance
for 30 seconds → ping Slack" now fires, in the room, from real detections.**

**A rule is a row, and `/v1/rules` is how an operator writes one.** A `rules`
table under the same forced row-level security as the log, plus `rule_dispatch`,
which exists for its UNIQUE constraint and nothing else. The validator is
deliberately asymmetric: `triggerType` is a namespace prefix check, so a rule can
name `intent.scored` the day that producer lands; `condition.type` and
`action.type` are closed unions, because an action nothing dispatches is a rule
that looks armed on screen and does nothing in the room.

**The evaluator is a bus consumer** (`consumers/rules.py`), subclassing the same
base as the tracker — so cursor, exponential backoff and dead-lettering are the
ones `/ops` already surfaces, rather than a second set. It emits `rule.fired` and
stops there; carrying out the action is a separate consumer, which keeps a slow
Slack API off the path with the `< 3s` budget on it.

Two bugs the tests found, both of which would have shipped quietly:

- **Cooldown ordered on the firing's own `seq` never applies at all.** A firing
  is appended *after* the event that caused it, so it always holds the higher
  seq — every dwell in a burst looks for an earlier firing, finds it sitting
  ahead in the log, and concludes the rule has never fired. A crowd of five
  produced five Slack posts. It has to order on the `triggerSeq` the payload
  carries, which puts a firing where its cause is.
- **A threshold that counts events fires a five-person rule on one person.**
  `spatial.dwell` is emitted per stay, so a visitor leaving and returning five
  times is five events. It counts distinct `anon_id`s, and says which it did.

**The actions are real.** `consumers/dispatch.py` reads `rule.fired` and runs one
handler from `app/actions/`: Slack, a signed webhook, a screen swap, a staff
prompt, or a deliberate no-op. Idempotency is a `rule_dispatch` row claimed
*before* the call goes out, because Slack is not a database we can put an
`ON CONFLICT` on — so the decision moves to one that is. The claim reads three
prior states differently: `delivered` refuses, which is the replay case; `failed`
is retaken, because nothing was delivered and there is nothing to duplicate; and
`claimed` refuses, because a process that died mid-call leaves it genuinely
unknown whether the message arrived, and that wants a human rather than a guess.

Screen swaps and staff prompts go **on the bus**, not straight at the WebSocket
hub. A tablet that reconnects ten seconds later catches up from its cursor; a
prompt pushed at the sockets that happened to be open is gone. The `/live` panel
that shows them expires its own entries after five minutes, which is the one
number here that is a judgement rather than a derivation: a prompt still on
screen twenty minutes later is not information, it is furniture, and it teaches
the floor staff to stop reading the panel.

**The cost tile has a reading.** Every dispatched action meters an `action_unit`,
so `cost.metered` finally has a caller and the tile stops saying "no meter yet".
The reading is **1 action**, not a price — a default cost per action would be a
number invented on a client's behalf, which `roi-framework.md` rules out for
revenue and this refuses for spend. Actions are counted in a unit that is not a
currency, so they sit on their own line and are never added to dollars.

**The browser stopped owning rules**, which was ADR-002's last unbuilt section.
There were *three* rule vocabularies: the eight `AgentDefinition`s, the composer
screen's own `Trigger = "dwell" | "count" | "gaze" | …` with hand-written
condition strings, and the thing that actually decides whether a booth acts. The
definitions now compile to the document; three of them turn out not to be rules
at all — they are jobs this browser runs over its own state — and say so instead
of being padded into the shape. That mismatch is the finding rather than a gap:
a list mixing "ping ops when the entrance is crowded" with "regenerate the
heatmap" reads as one kind of thing and is two, which is most of why the
split-brain was hard to see.

`/agents` reads real rules from the backend and real firings from `rule.fired`.
`fired: 488`, "718 fires today" and "240ms avg latency" are gone, on the same
grounds the report's and the live tile's invented figures were. The browser runs
a **dry run** — the same document, this session's events, a count of how often it
would have fired — and dispatches nothing. A `none` rule reports that it cannot
be previewed here rather than showing zero, because this log is a partial mirror
and an apparent silence may just be an event that has not arrived.

**One integration bug worth recording, because a green suite hid it.** The
evaluator read `zoneId` and `anonId`; the tracker writes `zone_id` and `anon_id`,
which is what `event-bus-spec.md` §3 pins and what is actually in the log. A rule
was therefore armed, correct, watching the right events — and silently matching
none of them. No error, no dead letter, nothing to see. Every unit test on either
side had been written in that side's own dialect and agreed with itself. It took
`tests/test_phase3_acceptance.py`, which runs perception → tracker → evaluator →
dispatcher as one chain, to catch it, and that test now stands as the acceptance
criterion rather than a paragraph in a doc.

Fifty new backend tests (205 total) and twenty-six new dashboard tests (126). The
preview's cases are deliberate twins of the evaluator's — same rule, same events,
same expected firings — so the two runners of one document cannot drift without
both suites failing.

### Added — 2026-08-11 (later) — `[neo4j-track]` Phase 3 starts with the two pieces nobody has to build twice

Phase 3 is "turn signals into action". Its centre is a rules engine, and the
postgres-track shipped one on 2026-08-10. Building a second is a week spent on
work the bake-off will throw half of away, so this track took the two Phase 3
items that are *store-agnostic* — they touch contracts and telemetry, not the
graph — and left the evaluator alone until the tracks are compared.

**A rule is now a document, not code — `docs/adr/002-rule-spec.md`.** The spec
adopts the shape the other track already ships, field for field, so the two do
not grow two rule languages: trigger type, an optional zone, a condition
(`threshold` / `any` / `none`), an action (Slack, webhook, screen swap, staff
prompt, log), enabled, cooldown. What the ADR adds is the reasoning and four
corrections that turn a working evaluator into a replayable one:

- the sliding window is read from the bus rather than held in a dictionary that
  a restart empties and a replay cannot reproduce;
- cooldown compares event time, not `datetime.now()` — the same rule the
  tracker's dropout sweep follows, and for the same reason: a replay must
  produce what the original run produced;
- `rule.fired` carries a **derived** event id, and each dispatch is idempotent
  on that id, so a retry after a timeout cannot post to Slack twice;
- a `none` condition is judged at a boundary (the next event past the window, or
  `session.ended`), because asking "did nothing happen?" at the moment something
  did is close to a contradiction.

It also ends the split-brain the roadmap names: the dashboard's eight agent
definitions, which have their own trigger vocabulary, become presets that
compile to the same document, and the browser runtime becomes the dry-run an
operator sees before saving. Nothing in the browser decides what fires.

**Cost telemetry, end to end but honest about being empty.** There is now one
way to say "this spent money" (`backend/app/cost.py`): it takes the *cause* of
the spend and derives the event id from it, rather than accepting an id. That
matters more here than anywhere else the same rule applies — a duplicated cost
survives every replay and lands in the direction that overstates what a client's
activation cost. The reader (`lib/roi/cost.ts`) groups by kind and unit and
**only totals money from amounts already denominated in a currency**; tokens are
shown as tokens, and two currencies produce no single total rather than an
invented exchange rate. The `/live` tile shows metered spend, cost per engaged
visitor, and a line per kind.

It reads "no meter yet", because nothing on this track spends money: Ask has no
provider key and the dispatchers are unbuilt. That is deliberately not "$0.00",
which would be a claim that the session was free — the same distinction the
report makes about revenue it cannot see. The first readings will come from
Ask's LLM calls and from rule actions.

Small supporting change: `Scorecard.engagement` now exposes `engagedVisitors`.
It was already computed and only reachable by multiplying the rounded rate back
out by the unique count, which is the denominator every per-engaged-visitor cost
divides by.

Twelve new tests — the meter deduping a re-metered spend, distinguishing two
dispatches of one rule, refusing a unitless or negative reading; the reader
keeping units apart, refusing a two-currency total, and not dividing by zero
visitors. 155 backend tests, 100 dashboard.

### Added — 2026-08-11 — `[neo4j-track]` the bus now accepts the events Phase 3 and beyond will send, before anything sends them

Six event types that no part of the system produces yet — a badge read, a badge
matched to a tracked person, an intent score, a camera-drift alarm, a
recalibration, and a CRM retraction — are now part of the contract. Nothing
emits them today. That is the point: they are registered ahead of the code so
the phase that finally writes a producer does not have to change the contract to
ship it.

Four of them would have been rejected outright until now. The bus checks an
event's *namespace* against a known list, so `intent.scored`, `drift.detected`,
`calibration.updated` and `crm.retract` were 422s — and a namespace rejection is
the worst kind to meet first, because the error is about a taxonomy the author
of a new producer has no reason to suspect. This is the same failure the
`rfid.` namespace was added ahead of time to avoid, applied to the rest of the
list.

- **The four missing namespaces are registered** (`backend/app/schemas.py`),
  each with a line saying which phase's producer will use it. The check stays a
  prefix test rather than an allow-list of full type names, so the taxonomy
  remains additive — a new `spatial.*` type still needs no code change.

- **The payloads are pinned in `docs/event-bus-spec.md` §3**, with the reasoning
  that will otherwise be lost by the time someone writes the producer:
  `spatial.tagged` is a *correlation, not an identity* (a badge near a track, a
  guess, hence a confidence and a method — the link to a person stays behind the
  consent gate); `intent.scored` stores the band and the model version, because
  thresholds are per-tenant and a replay through a newer scorer must be
  distinguishable from changed data; `drift.detected` carries both the observed
  value and the baseline so it states its comparison instead of asserting a
  verdict; `calibration.updated` takes the same random-`event_id` exception as
  `session.zones_updated`, since two recalibrations of one camera are two facts
  and a derived id would silently discard the second. `intent.scored` is marked
  **provisional** — nothing pins the scoring model yet.

- **`crm.retract` is classified as PII** in the browser contract
  (`dashboard/src/lib/contracts/events.ts`). It carries a `contact_id` and it
  exists because consent was withdrawn, which makes it tempting to file as an
  ops event; the identifier is in the payload either way, so `isPiiEventType`
  now gates it and it stays off the anonymised cloud-sync path. The other four
  are anonymous. `rfid.read`, in the type union since Week 1 but never given a
  payload interface, has one now instead of falling into the untyped arm.

Also corrected: the spec still carried a note asking for `rfid.read` to be
mirrored into the browser union. It was mirrored some time ago.

Eight new tests — each of the six types accepted by the validator, all six
posting 201 over the real endpoint, and a misspelled namespace still refused
with the taxonomy doc named in the error. 150 backend tests, 93 dashboard.

### Fixed — 2026-08-07 — `[neo4j-track]` "the bus is unreachable" was a backend that was up and had already saved the session

Hit while launching a session from the wizard. The wizard said the backend had
not accepted it and the cameras had no zones to measure against. The backend was
running, had accepted the write, and had stored the zones.

- **A zone saved before it was drawn could not be read back.** The config
  endpoint asks for undrawn zones on purpose — an operator names a zone before
  drawing it, and the wizard has to show the half-created one rather than
  pretend it is gone. But an undrawn zone came back as `polygon: []`, and
  `ZoneConfig` rejects a polygon with no points, rightly: a shape with no points
  contains nobody. So the *response* raised after the write had landed. The two
  features had contradicted each other since the endpoint was written.
  *Absent geometry now reads back as `null`, which is what it is, and which the
  dashboard's own type already expected. `[]` is a claim about a shape.*

- **The 500 lost its CORS headers, which is why it read as "unreachable".**
  Starlette's ServerErrorMiddleware sits outside every middleware the app adds,
  CORS included, so its 500 arrives without `access-control-allow-origin`. The
  browser blocks it and the fetch rejects — from JavaScript that is
  indistinguishable from nothing listening on the port. *A middleware registered
  before CORS (so CORS wraps it) now turns an unhandled exception into a plain
  500 the browser can read. An `@app.exception_handler(Exception)` does not
  work here: Starlette routes the catch-all to ServerErrorMiddleware too, and it
  still answers from outside CORS.*

Worth keeping in view: a zone with no polygon still measures nothing. The
tracker skips it, correctly. Publishing now succeeds and the report will show
that zone with no dwell against it, which is the honest outcome — but a zone
meant to measure has to be drawn.

Two new tests: a session with one drawn and one undrawn zone round-trips, and an
unhandled error answers with CORS headers. 142 backend tests, 93 dashboard.

### Fixed — 2026-08-07 — `[neo4j-track]` a zone belongs to one session, and the graph now says so

Found while checking a demo stack: a session reported its zones published and
then had none. It had been robbed by the session published after it.

- **Zone identity had no session in it.** The key was `(tenant_id, id)`, so a
  zone id was tenant-global. Publish a second session that reuses a zone id —
  `z_left`, `zone_entry`, anything out of a template or a fixture — and the
  MERGE did not create a zone. It found the *first* session's node, overwrote
  its `session_id`, and moved it. The first session's zones vanished from its
  own report, and its `ENTERED` and `DWELLED_IN` edges went with the node into
  an activation they had nothing to do with. Nothing logged, and the publish
  response echoes zones read straight back from the graph, so it looked correct
  at the moment it broke. *Zone is now keyed `(tenant_id, session_id, id)`, the
  key `Person` has had since the start — data-model.md already says anon_id is
  "session-scoped, never re-used across sessions", and zones are no different.*

- **Surface had the same key and more to lose.** It carries `trigger_count`, so
  two sessions sharing a surface id shared one counter: a sponsor's usage figure
  for one activation quietly included taps from another. *Re-keyed the same way.*

Graph migration `002` does the re-key; `001` is untouched because it has already
run on databases that exist. Every zone and surface lookup is now scoped to the
session as well — they all had the session id to hand and were simply not using
it.

Not reachable from the wizard today, which mints random zone ids, which is why
it had gone unnoticed. It is reachable from anything with fixed ids: the
perception defaults, shared fixtures, the demo session's `zone_entry` if it were
ever published, and both tracks' test data.

Verified by breaking it: three new tests — two sessions keeping their own zones,
dwell staying with the session that recorded it, and trigger counts not pooling
— all three fail against the old key. 140 backend tests, 93 dashboard.

### Fixed — 2026-08-07 — `[neo4j-track]` `docker compose up` boots again on a machine that has never run it

Found by doing the thing the README promises: a one-command boot before a demo.
The stack never became healthy. The app container sat at `==> waiting for
postgres` indefinitely while Postgres itself was fine and reporting healthy.

Two faults, both introduced by the row-level-security work, and both invisible
because the wait loop discards the error it is retrying on.

- **The boot waited for something only the next step could create.** The wait
  connected as `realmspace_app` — the non-superuser role that makes RLS apply —
  but that role is *created by migration 0003*, which runs after the wait. On
  any database that predates the RLS migration, including an empty one, the
  role does not exist yet, so the loop waited for its own next step forever.
  *The wait now connects as the owner, via the existing `migration_url`, which
  is the credential the image already has at boot.*

- **A passwordless role cannot cross a container network.** Migration 0003
  creates the role without a password on purpose, and says why: locally the app
  reaches Postgres over a unix socket under trust auth, and a password in git is
  not a password. Compose has no socket — it crosses to `postgres:5432`, where
  the image's default `scram-sha-256` refuses a passwordless role.
  *`entrypoint.sh` now sets the password from `APP_DB_PASSWORD` after the
  migration that creates the role, through `set_config` + `format(%L)` because
  `ALTER ROLE` takes no bind parameters. The migration is untouched: the
  password belongs to the deployment, not to the schema.*

The entrypoint also now connects once as the app role before serving. The
failure this fixes reported a clean start and then 500'd on the first request,
which is the wrong place to find out that a credential is wrong.

Verified on genuinely empty volumes, in a separate compose project so the dev
stack was not disturbed: all three Alembic migrations and the graph schema
apply, `/health` returns both stores `ok` and all three consumers `running`.

### Fixed — 2026-08-05 — `[neo4j-track]` the demo session now shows what the product can actually do

Prompted by comparing a live demo against the other track's. Theirs looked
better — and the reason was not that theirs is more capable. Ours was computing
honestly from the seeded day and then had nothing to divide by.

- **The demo was a session nobody had configured.** No funnel order, so the
  report showed "needs a funnel order" where the journey should be. Every zone
  weighted the same, so dwell-weighted attention — the metric the whole ROI
  framework is built around — was just plain dwell wearing a different name. No
  activation cost, so cost per engaged visit was blank. No revenue, so the ROI
  ratio was blank. All of that read as a product that cannot do those things.
  *The demo now carries the same parameters the wizard asks a real operator for.
  It reports **140 visitors, a funnel narrowing 134 → 82 → 38 → 12, weighted
  attention 2.4× raw dwell, £152 per engaged visit and a 4.0:1 return rated
  "strong"** — every one of them computed from the seeded events.*

- **Nothing was invented to get there.** Weights, funnel order and cost are
  configuration. Influenced revenue is the client's own figure, and the report
  prints "supplied by the client, not measured by realmspace" beside it, which
  is what makes it safe to show at all.

- **Qualified leads deliberately left blank**, so cost per qualified lead divides
  by the ~43 consent captures actually in the seeded day. Supplying a number
  would have overwritten real data with a guess.

- **Affinity still shows nothing**, and still says "Affinity needs a survey.
  Nothing here is inferred from behaviour." In a demo that line is worth more
  than three filled boxes.

- **A real bug, not demo dressing.** The report only read touchpoint names from
  the *backend* config, so any session configured locally — which is every
  session before it is published — listed raw ids like `srf_mirror` instead of
  "AR Mirror". Zones already fell back to the local session; surfaces were
  missed.

- **And a bug in the seed:** it emitted interactions against invented surface ids
  that matched none of the demo's actual touchpoints, so nothing could ever have
  linked an interaction to the thing that produced it.

- **The exit zone has no funnel position on purpose.** No seeded visitor walks
  through it, so a step there would sit at a permanent 0% and read as a broken
  funnel rather than an honest one.

- **93 dashboard tests (was 90).** **Verified by breaking it:** flattening the
  zone weights, removing the funnel order, dropping the client revenue, or
  restoring the mismatched surface ids each fails its own test.

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
