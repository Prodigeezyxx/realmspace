# RealmSpace Dashboard

Next.js experiential intelligence console — live sensor, digital twin, sessions, agents.

## Local dev

```bash
npm install --legacy-peer-deps
cp firebase.env.example .env.local   # add Firebase keys for auth (optional locally)
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Without `.env.local`, the app runs **without auth** (all routes open). With Firebase configured, `/live`, `/twin`, etc. require sign-in.

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

## Stack

Next.js 16 · React 19 · TensorFlow.js (on-device live) · React Three Fiber · Firebase Auth + Hosting
