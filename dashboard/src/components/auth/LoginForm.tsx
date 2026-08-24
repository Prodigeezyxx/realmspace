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

  if (!isFirebaseConfigured()) {
    return (
      <div className="panel-elevated p-8 text-center">
        <p className="text-sm text-text-secondary">
          Add Firebase env vars to <code>.env.local</code> (see{" "}
          <code>firebase.env.example</code>).
        </p>
      </div>
    );
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

        <Button type="submit" fullWidth disabled={busy}>
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
