"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, ATELIER_LOCAL } from "@/lib/api";

export default function SettingsPage() {
  const [user, setUser] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // Local desktop: we open OAuth in the system browser and poll until it lands.
  const [connecting, setConnecting] = useState(false);
  const [manualUrl, setManualUrl] = useState<string | null>(null);
  const pollActive = useRef(false);

  const load = async () => {
    try { setUser(await api.me()); } catch {}
  };

  // Stop polling if the user navigates away mid-connect.
  useEffect(() => () => { pollActive.current = false; }, []);

  const pollUntilConnected = () => {
    pollActive.current = true;
    const deadline = Date.now() + 5 * 60 * 1000; // 5-minute browser-sign-in window
    const tick = async () => {
      if (!pollActive.current) return;
      try {
        const me = await api.me();
        if (me?.openrouter_connected) {
          pollActive.current = false;
          setUser(me); setConnecting(false); setManualUrl(null);
          setToast("OpenRouter connected ✓");
          setTimeout(() => setToast(null), 3000);
          return;
        }
      } catch {}
      if (pollActive.current && Date.now() < deadline) {
        setTimeout(tick, 2000);
      } else if (pollActive.current) {
        pollActive.current = false;
        setConnecting(false);
        setToast("Sign-in timed out. Click Connect to try again.");
        setTimeout(() => setToast(null), 4000);
      }
    };
    setTimeout(tick, 2000);
  };

  const startBrowserConnect = async () => {
    if (connecting) return;
    setConnecting(true);
    setManualUrl(null);
    setToast("Opening your browser to sign in…");
    setTimeout(() => setToast(null), 3000);
    try {
      const r = await api.connectOpenRouterBrowser();
      if (!r.opened) setManualUrl(r.url); // no default browser — offer a manual link
      pollUntilConnected();
    } catch (e: any) {
      setConnecting(false);
      setToast(`Couldn't start sign-in: ${e?.message ?? e}`);
      setTimeout(() => setToast(null), 4000);
    }
  };

  useEffect(() => { (async () => {
    await load();
    // Surface the result of the OAuth round-trip (?openrouter=connected|error).
    if (typeof window !== "undefined") {
      const p = new URLSearchParams(window.location.search).get("openrouter");
      if (p === "connected") setToast("OpenRouter connected ✓");
      else if (p === "error") setToast("Couldn't connect OpenRouter — please try again.");
      if (p) {
        setTimeout(() => setToast(null), 3500);
        window.history.replaceState({}, "", "/settings");
      }
    }
  })(); }, []);

  const connected = !!user?.openrouter_connected;

  const disconnect = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.disconnectOpenRouter();
      await load();
      setToast("Disconnected.");
      setTimeout(() => setToast(null), 2500);
    } catch (e: any) {
      setToast(`Failed: ${e?.message ?? e}`);
      setTimeout(() => setToast(null), 4000);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-dvh px-6 py-10">
      <div className="mx-auto max-w-[820px]">
        <div className="mb-6 flex items-center justify-between gap-4">
          <Link href="/" className="text-[12px] uppercase tracking-[0.2em] opacity-70 hover:opacity-100">
            ← back
          </Link>
          {!user?.local && user?.email && (
            <div className="text-[12px]" style={{ color: "var(--color-muted)" }}>
              {user.email}
            </div>
          )}
        </div>

        <p className="mb-2 text-[11px] uppercase tracking-[0.24em]" style={{ color: "var(--color-muted)" }}>
          Settings
        </p>
        <h1 className="h-display text-[40px] leading-[1.05]">AI provider</h1>
        <p className="mt-3 max-w-[640px] text-[14px]" style={{ color: "var(--color-ink-2)" }}>
          Connect your own OpenRouter account and Atelier runs every reply on your
          key — you control the models and the cost, and free models are available.
          Your key is encrypted at rest and never shown again.
        </p>

        <div className="mt-6 rounded-md border p-5" style={{ borderColor: "var(--color-rule)" }}>
          <div className="flex items-center justify-between gap-4">
            <div className="flex flex-col gap-1">
              <div className="font-display text-[16px]">OpenRouter</div>
              <div className="text-[13px]" style={{ color: "var(--color-muted)" }}>
                {connected ? "Connected — inference runs on your key." : "Not connected."}
              </div>
            </div>
            <span
              className="rounded-full px-2.5 py-1 text-[11px] uppercase tracking-[0.15em]"
              style={{
                background: connected ? "var(--color-moss-soft)" : "var(--color-paper-3)",
                color: connected ? "var(--color-moss)" : "var(--color-muted)",
                border: `1px solid ${connected ? "var(--color-moss)" : "var(--color-rule)"}`,
              }}
            >
              {connected ? "Active" : "Off"}
            </span>
          </div>

          <div className="mt-5 flex items-center gap-3">
            {!connected ? (
              ATELIER_LOCAL ? (
                <button
                  onClick={startBrowserConnect}
                  disabled={connecting}
                  className="rounded-full px-4 py-2 text-[13px] disabled:opacity-60"
                  style={{ background: "var(--color-brick)", color: "var(--color-paper)" }}
                >
                  {connecting ? "Waiting for browser sign-in…" : "Connect OpenRouter →"}
                </button>
              ) : (
                <a
                  href={api.connectOpenRouterUrl()}
                  className="rounded-full px-4 py-2 text-[13px]"
                  style={{ background: "var(--color-brick)", color: "var(--color-paper)" }}
                >
                  Connect OpenRouter →
                </a>
              )
            ) : (
              <>
                {ATELIER_LOCAL ? (
                  <button
                    onClick={startBrowserConnect}
                    disabled={connecting}
                    className="border px-4 py-2 text-[13px] disabled:opacity-60"
                    style={{ borderColor: "var(--color-rule)", color: "var(--color-ink)" }}
                  >
                    {connecting ? "Waiting for browser…" : "Reconnect"}
                  </button>
                ) : (
                  <a
                    href={api.connectOpenRouterUrl()}
                    className="border px-4 py-2 text-[13px]"
                    style={{ borderColor: "var(--color-rule)", color: "var(--color-ink)" }}
                  >
                    Reconnect
                  </a>
                )}
                <button
                  onClick={disconnect}
                  disabled={busy}
                  className="border px-4 py-2 text-[13px] disabled:opacity-40"
                  style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}
                >
                  {busy ? "…" : "Disconnect"}
                </button>
              </>
            )}
          </div>

          {connecting && (
            <p className="mt-4 text-[12px]" style={{ color: "var(--color-muted)" }}>
              Finish signing in on the browser tab that just opened, then come back —
              this page connects automatically.
              {manualUrl && (
                <>
                  {" "}Browser didn't open?{" "}
                  <a href={manualUrl} target="_blank" rel="noreferrer"
                     className="underline" style={{ color: "var(--color-brick)" }}>
                    Open the sign-in link
                  </a>.
                </>
              )}
            </p>
          )}

          <p className="mt-4 text-[12px]" style={{ color: "var(--color-muted)" }}>
            {ATELIER_LOCAL
              ? "Sign-in opens in your web browser (so Google passkeys and your existing session work), then connects back here. No credit card needed for free models."
              : "You'll sign in to OpenRouter (Google works) and authorize Atelier. No credit card needed for free models. Don't have an account? One is created during sign-in."}
          </p>
        </div>
      </div>

      {toast && (
        <div className="fixed inset-x-0 bottom-6 mx-auto w-fit rounded-md border bg-[var(--color-paper-2)] px-4 py-2 text-[13px]"
             style={{ borderColor: "var(--color-ink)" }}>
          {toast}
        </div>
      )}
    </main>
  );
}
