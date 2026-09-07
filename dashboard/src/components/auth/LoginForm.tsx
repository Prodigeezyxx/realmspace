"use client";

import { Loader2, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
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

  useEffect(() => {
    if (user) router.replace(next);
  }, [user, next, router]);

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
      await signInWithGoogle();
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

  return (
    <div className="panel-elevated p-8 md:p-10 w-full max-w-md">
      <Pill variant="success" className="mb-6">
        <Mail size={11} />
        realmspace account
      </Pill>

      <h1 className="text-2xl font-semibold tracking-tight">
        {mode === "signin" ? "Welcome back" : "Create your account"}
      </h1>
      <p className="text-sm text-text-secondary mt-2">
        Open your sessions, live view, replay and reports.
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
