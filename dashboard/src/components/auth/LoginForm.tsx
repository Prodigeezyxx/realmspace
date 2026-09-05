"use client";

import { Building2, Loader2, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { busEmail, ensureToken, isRemoteBusEnabled, signUpOrganisation } from "@/lib/bus";
import {
  logOut,
  resetPassword,
  signInWithEmail,
  signInWithGoogle,
  signUpWithEmail,
} from "@/lib/firebase/auth-actions";
import { isFirebaseConfigured } from "@/lib/firebase/config";
import { cn } from "@/lib/utils";

import { useAuth } from "./AuthProvider";

function LoginFormInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/live";
  const { user } = useAuth();

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  //: Set when Firebase says who somebody is and this backend has never heard of
  //: them. Signing in correctly and being told `auth failed (401)` was the dead
  //: end `multi-tenant.md` §4 step 1 left behind — see `needsOrganisation`.
  const [needsOrg, setNeedsOrg] = useState(false);
  const [orgName, setOrgName] = useState("");

  useEffect(() => {
    if (user && !needsOrg) router.replace(next);
  }, [user, next, router, needsOrg]);

  /**
   * Whether this verified identity has an organisation on this backend.
   *
   * A Firebase account is only half a login here: the address still has to map
   * to a tenant and a role. `ensureToken` returns null on the 401 that means it
   * does not, which is the moment to ask for a company name rather than to
   * strand somebody on a dashboard that will fail every request.
   *
   * With no backend configured there is nothing to have an account *on*, so the
   * laptop demo goes straight through — as it did before any of this.
   */
  async function needsOrganisation(address: string): Promise<boolean> {
    if (!isRemoteBusEnabled()) return false;
    return (await ensureToken(address || busEmail())) === null;
  }

  async function createOrganisation() {
    setBusy(true);
    setError(null);
    const result = await signUpOrganisation(email || busEmail(), orgName.trim());
    setBusy(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setNeedsOrg(false);
    router.replace(next);
  }

  async function handleEmail(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (mode === "signin") {
        await signInWithEmail(email, password);
      } else {
        await signUpWithEmail(email, password);
      }
      if (await needsOrganisation(email)) {
        setNeedsOrg(true);
        return;
      }
      router.replace(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleGoogle() {
    setBusy(true);
    setError(null);
    try {
      const account = await signInWithGoogle();
      if (await needsOrganisation(account.email ?? "")) {
        setNeedsOrg(true);
        return;
      }
      router.replace(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    if (!email.trim()) {
      setError("Enter your email first, then tap reset password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resetPassword(email);
      setMessage("Password reset email sent — check your inbox.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * No Firebase, but a backend: the local development path.
   *
   * This branch used to be one sentence telling the reader to go and configure
   * Firebase, which made `multi-tenant.md` §4 step 1 unreachable in the only
   * mode this repo can actually run — `POST /v1/auth/signup` existed and
   * nothing in the product could call it. The backend deliberately keeps an
   * email-only path *for exactly this case* (`app/routers/auth.py`: `local`
   * issues on an address and warns loudly, every other environment answers
   * 503), so the browser refusing to offer one was the half that disagreed.
   *
   * With no backend either, the original message is still right: there is
   * nothing here to have an account *on*, and the laptop demo walks straight
   * past this screen anyway (`AuthGate`).
   */
  if (!isFirebaseConfigured()) {
    if (!isRemoteBusEnabled()) {
      return (
        <div className="panel-elevated p-8 text-center">
          <p className="text-sm text-text-secondary">
            Add Firebase env vars to <code>.env.local</code> (see{" "}
            <code>firebase.env.example</code>).
          </p>
        </div>
      );
    }
    return <LocalOrgForm next={next} />;
  }

  if (needsOrg) {
    return (
      <div className="panel-elevated p-8 md:p-10 w-full max-w-md">
        <Pill variant="info" className="mb-6">
          <Building2 size={11} />
          One more step
        </Pill>

        <h1 className="text-2xl font-semibold tracking-tight">
          Name your organisation
        </h1>
        <p className="text-sm text-text-secondary mt-2 leading-relaxed">
          We know who you are — this address has no organisation on this
          deployment yet. Everything realmspace measures belongs to one:
          activations, zones, leads and integrations are all scoped to it.
        </p>

        <input
          type="text"
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
          placeholder="Northwind Traders"
          autoFocus
          className="w-full mt-6 px-3 py-2.5 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
        />

        <Button
          variant="primary"
          fullWidth
          className="mt-4"
          disabled={busy || !orgName.trim()}
          onClick={() => void createOrganisation()}
        >
          {busy ? <Loader2 className="animate-spin" size={16} /> : null}
          Create organisation
        </Button>

        <p className="text-xs text-text-muted mt-5 leading-relaxed">
          This creates a <em>new</em> organisation with you as its admin. If you
          were expecting to join one that already exists, stop here and ask an
          admin there to invite you — we deliberately never file an address into
          somebody&rsquo;s organisation by guessing from its domain, and carrying
          on would leave you in an empty one of your own.
        </p>

        <button
          type="button"
          className="text-xs text-text-muted hover:text-text-secondary underline mt-4"
          onClick={() => {
            setNeedsOrg(false);
            void logOut();
          }}
        >
          Sign in as somebody else
        </button>

        {error && (
          <p className="mt-4 text-xs text-accent-red leading-relaxed">{error}</p>
        )}
      </div>
    );
  }

  return (
    <div className="panel-elevated p-8 md:p-10 w-full max-w-md">
      <Pill variant="success" className="mb-6">
        <Mail size={11} />
        Sign in to realmspace
      </Pill>

      <h1 className="text-2xl font-semibold tracking-tight">
        {mode === "signin" ? "Welcome back" : "Create your account"}
      </h1>
      <p className="text-sm text-text-secondary mt-2">
        Access live sensor, twin, sessions and reports for your activations.
      </p>

      <div className="flex gap-2 mt-6 p-1 rounded-full bg-bg-elevated border border-border-subtle">
        {(["signin", "signup"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={cn(
              "flex-1 h-9 rounded-full text-sm font-medium transition-colors",
              mode === m
                ? "bg-accent text-text-inverse"
                : "text-text-muted hover:text-text-primary"
            )}
          >
            {m === "signin" ? "Sign in" : "Sign up"}
          </button>
        ))}
      </div>

      <Button
        type="button"
        variant="secondary"
        fullWidth
        className="mt-6"
        disabled={busy}
        onClick={handleGoogle}
      >
        {busy ? <Loader2 className="animate-spin" size={16} /> : null}
        Continue with Google
      </Button>

      <div className="flex items-center gap-3 my-6">
        <span className="flex-1 h-px bg-border-subtle" />
        <span className="text-[11px] uppercase tracking-wider text-text-muted">
          or email
        </span>
        <span className="flex-1 h-px bg-border-subtle" />
      </div>

      <form onSubmit={handleEmail} className="space-y-4">
        <label className="block text-sm">
          <span className="text-text-muted text-[11px] uppercase tracking-wider">
            Email
          </span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 w-full h-11 px-4 rounded-lg bg-bg-elevated border border-border-subtle focus:border-accent/50 outline-none"
          />
        </label>
        <label className="block text-sm">
          <span className="text-text-muted text-[11px] uppercase tracking-wider">
            Password
          </span>
          <input
            type="password"
            required
            minLength={6}
            autoComplete={
              mode === "signin" ? "current-password" : "new-password"
            }
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 w-full h-11 px-4 rounded-lg bg-bg-elevated border border-border-subtle focus:border-accent/50 outline-none"
          />
        </label>

        {error && (
          <p className="text-sm text-accent-red" role="alert">
            {error}
          </p>
        )}
        {message && (
          <p className="text-sm text-accent" role="status">
            {message}
          </p>
        )}

        {/* `Button` defaults to `secondary`, so omitting the variant left the
            one call to action on the sign-in screen wearing the calm colour —
            while the organisation panel three hundred lines up, added at the
            same time, has always been `primary`. brand.md §3 puts "primary
            CTAs" first on Action orange's short list. Found by the Phase 6
            acceptance's on-brand pass. */}
        <Button type="submit" variant="primary" fullWidth disabled={busy}>
          {busy ? <Loader2 className="animate-spin" size={16} /> : null}
          {mode === "signin" ? "Sign in" : "Create account"}
        </Button>
      </form>

      {mode === "signin" && (
        <button
          type="button"
          onClick={handleReset}
          disabled={busy}
          className="mt-4 text-xs text-text-muted hover:text-accent transition-colors"
        >
          Forgot password?
        </button>
      )}

      <p className="mt-8 text-center text-xs text-text-muted">
        <Link href="/" className="hover:text-text-primary">
          ← Back to home
        </Link>
      </p>
    </div>
  );
}

/**
 * Sign up, or continue, on a stack with no Firebase project.
 *
 * Deliberately not dressed up as a login. There is no password because nothing
 * here verifies one: the backend accepts a bare address only in its `local`
 * environment, says so in its own logs, and refuses it everywhere else. Making
 * this look like the real sign-in screen would be the more dangerous kindness —
 * somebody would eventually believe it.
 *
 * Two ways through, and they are different acts. **Create an organisation** is
 * `multi-tenant.md` §4 step 1, the thing this branch existed to make
 * unreachable. **Continue as the configured identity** is the seeded
 * `NEXT_PUBLIC_BUS_EMAIL` account the demo has always used, named on the button
 * rather than assumed, because "which organisation am I in" is the question
 * every empty screen in this product turns out to be.
 */
function LocalOrgForm({ next }: { next: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [orgName, setOrgName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function createOrg(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const result = await signUpOrganisation(email.trim(), orgName.trim());
    setBusy(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    router.replace(next);
  }

  async function continueAs(address: string) {
    setBusy(true);
    setError(null);
    const token = await ensureToken(address);
    setBusy(false);
    if (!token) {
      setError(
        `The backend does not know ${address}. Create an organisation for it above.`
      );
      return;
    }
    router.replace(next);
  }

  return (
    <div className="panel-elevated p-8 md:p-10 w-full max-w-md">
      <Pill variant="info" className="mb-6">
        <Building2 size={11} />
        Local development
      </Pill>

      <h1 className="text-2xl font-semibold tracking-tight">
        Create your organisation
      </h1>
      <p className="text-sm text-text-secondary mt-2 leading-relaxed">
        No Firebase project is configured, so there is nobody to verify. This
        backend accepts a plain address in its <code>local</code> environment
        only, and answers <code>503</code> anywhere else.
      </p>

      <form onSubmit={createOrg} className="space-y-4 mt-6">
        <label className="block text-sm">
          <span className="text-text-muted text-[11px] uppercase tracking-wider">
            Email
          </span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5 w-full h-11 px-4 rounded-lg bg-bg-elevated border border-border-subtle focus:border-accent/50 outline-none"
          />
        </label>
        <label className="block text-sm">
          <span className="text-text-muted text-[11px] uppercase tracking-wider">
            Organisation
          </span>
          <input
            type="text"
            required
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            className="mt-1.5 w-full h-11 px-4 rounded-lg bg-bg-elevated border border-border-subtle focus:border-accent/50 outline-none"
          />
        </label>

        {error && (
          <p className="text-sm text-accent-red leading-relaxed" role="alert">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" fullWidth disabled={busy}>
          {busy ? <Loader2 className="animate-spin" size={16} /> : null}
          Create organisation
        </Button>
      </form>

      <div className="flex items-center gap-3 my-6">
        <span className="flex-1 h-px bg-border-subtle" />
        <span className="text-[11px] uppercase tracking-wider text-text-muted">
          or
        </span>
        <span className="flex-1 h-px bg-border-subtle" />
      </div>

      <Button
        type="button"
        variant="secondary"
        fullWidth
        disabled={busy}
        onClick={() => void continueAs(busEmail())}
      >
        Continue as {busEmail()}
      </Button>
    </div>
  );
}

export function LoginForm() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20">
          <Loader2 className="animate-spin text-accent" size={28} />
        </div>
      }
    >
      <LoginFormInner />
    </Suspense>
  );
}

export function SignOutButton() {
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  if (!isFirebaseConfigured()) return null;

  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await logOut();
          router.push("/login");
        } finally {
          setBusy(false);
        }
      }}
      className="text-xs text-text-muted hover:text-accent-red transition-colors"
    >
      {busy ? "…" : "Sign out"}
    </button>
  );
}
