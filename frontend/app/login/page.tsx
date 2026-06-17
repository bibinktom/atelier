"use client";
import { useEffect, useState } from "react";
import { api, BACKEND } from "@/lib/api";
import { TomoseMark } from "@/components/TomoseMark";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const u = new URL(window.location.href);
    const e = u.searchParams.get("error");
    if (e === "not_allowed") setError("That email isn't on the family allowlist.");
    else if (e === "unverified") setError("Your Google email isn't verified.");
    else if (e === "registration_full") setError("The approval queue is full right now. Ask the admin to clear it, then try again.");
    else if (e === "oauth") setError("Sign-in didn't complete. Try again.");
    else if (e === "unreachable") {
      let host = BACKEND;
      try { host = new URL(BACKEND).host; } catch {}
      setError(`Can't reach the server. Ask the admin to check the ${host} tunnel route.`);
    }
  }, []);

  return (
    <main className="relative grid min-h-dvh place-items-center px-6">
      <div className="relative w-full max-w-[480px]">
        <p className="mb-4 text-[11px] uppercase tracking-[0.24em]" style={{ color: "var(--color-muted)" }}>
          Private workspace · Invite only
        </p>
        <h1 className="h-display text-[60px] leading-[0.98] tracking-tight">
          The family<br/>
          atelier.
        </h1>
        <p className="mt-5 max-w-[440px] text-[15px] leading-relaxed" style={{ color: "var(--color-ink-2)" }}>
          A shared workspace for research, writing, spreadsheets, and slide decks —
          powered by frontier models, kept inside the home.
        </p>

        <ul className="mt-7 space-y-2.5 text-[14px]" style={{ color: "var(--color-ink-2)" }}>
          {[
            "Search the web and read sources you can verify",
            "Draft polished PDFs, workbooks, and presentations",
            "Run code in a private sandbox to crunch your data",
          ].map((text) => (
            <li key={text} className="flex items-start gap-3">
              <span aria-hidden className="mt-[7px] inline-block h-[6px] w-[6px] flex-none" style={{ background: "var(--color-ink)" }} />
              <span>{text}</span>
            </li>
          ))}
        </ul>

        <a
          href={api.loginUrl()}
          className="mt-9 inline-flex items-center gap-3 border bg-[var(--color-paper)] px-5 py-3 text-[14px] font-medium transition hover:bg-[var(--color-paper-3)]"
          style={{ borderColor: "var(--color-ink)", color: "var(--color-ink)" }}
        >
          <GoogleMark />
          <span>Continue with Google</span>
        </a>

        {error && (
          <div className="mt-5 border px-3 py-2 text-sm"
            style={{ borderColor: "var(--color-ink)", background: "var(--color-paper-3)", color: "var(--color-ink)" }}>
            {error}
          </div>
        )}

        <p className="mt-10 text-[12px] leading-relaxed" style={{ color: "var(--color-muted)" }}>
          Sign in with any Google account. New sign-ins wait briefly for the
          household admin to admit them — you'll see a holding screen until then.
        </p>
      </div>

      <footer className="absolute inset-x-0 bottom-5 flex justify-center">
        <TomoseMark />
      </footer>
    </main>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
      <path d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.49h4.84a4.14 4.14 0 0 1-1.79 2.71v2.26h2.9c1.7-1.56 2.69-3.87 2.69-6.62z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.47-.81 5.96-2.18l-2.9-2.26c-.81.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.96v2.34A9 9 0 0 0 9 18z" fill="#34A853"/>
      <path d="M3.95 10.7A5.4 5.4 0 0 1 3.66 9c0-.59.1-1.16.29-1.7V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.04l2.99-2.34z" fill="#FBBC05"/>
      <path d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 9 0 9 9 0 0 0 .96 4.96L3.95 7.3C4.66 5.17 6.65 3.58 9 3.58z" fill="#EA4335"/>
    </svg>
  );
}
