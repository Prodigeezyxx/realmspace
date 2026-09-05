# RealmSpace Dashboard

Next.js experiential intelligence console — live sensor, digital twin, sessions, agents.

## Local dev

```bash
npm install --legacy-peer-deps
cp firebase.env.example .env.local   # add Firebase keys for auth (optional locally)
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Without `.env.local`, the app runs **without auth** (all routes open). With Firebase configured, `/live`, `/twin`, etc. require sign-in.

## Connecting to the backend

Unset, the dashboard is self-contained: it stores events in localStorage and the
demo works on a laptop with nothing else running. Point it at the backend and the
real pipeline switches on.

```bash
# .env.local
NEXT_PUBLIC_BUS_URL=http://localhost:8000
NEXT_PUBLIC_BUS_EMAIL=admin@floats.demo   # optional; only used when nobody is signed in
```

With it set:

- **Events in.** A WebSocket per active session mirrors the backend's log into the
  durable local log every screen already reads. It reconnects with a `since_seq`
  cursor, so a dropped connection resumes with no gap and no duplicates.
- **Events out.** Anything this app emits is posted to `POST /events`. If the
  backend is down they stay in the local log and go out, in order, on reconnect —
  the local log *is* the outbox.
- **Sessions publish.** Finishing the wizard posts the zones and measurement
  parameters to `POST /v1/sessions`. **This is required for anything to be
  measured**: the tracker reads zone polygons from the graph, so a session that
  never published has no zones and produces no spatial events at all. If the post
  fails the wizard says so and does not navigate away.
- **A pill in the status bar** shows the connection, including when it is healthy.
  A feed that has silently dropped looks exactly like a quiet booth.

`NEXT_PUBLIC_BUS_EMAIL` matches what `python -m app.auth.seed` creates by default.
It is not a credential — the backend's token endpoint currently trusts the email
it is given, which `backend/README.md` flags as still open.

See `src/lib/bus/wire.ts` for why payloads are translated at this boundary: the
bus pins them in snake_case for the Python producers, this app's contract is
camelCase, and the mismatch is silent rather than loud.

## Firebase setup

1. [Create a Firebase project](https://console.firebase.google.com/)
2. **Build → Hosting** — note your site URL
3. **Authentication → Sign-in method** — enable **Email/Password** and **Google**
4. **Project settings → Your apps → Web** — copy config into `.env.local`
5. **Authentication → Settings → Authorized domains** — add `localhost` and your `*.web.app` / custom domain

```bash
# from repo root
cp .firebaserc.example .firebaserc   # set your project id
cd dashboard && npm run deploy
```

Or from repo root: `npx firebase login` then `cd dashboard && npm run deploy`.

## Tests

```bash
npm test          # vitest, one pass
npm run test:watch
```

Covers the logic whose failures are silent rather than loud — the wire
translation, the bus bridge's idempotency and ordered replay, and the ROI
scorecard against a **verbatim capture of real backend output**
(`src/lib/bus/__fixtures__/backend-events.json`). Layout and components are not
unit-tested; their failures are visible.

## Stack

Next.js 16 · React 19 · TensorFlow.js (on-device live) · React Three Fiber · Firebase Auth + Hosting
