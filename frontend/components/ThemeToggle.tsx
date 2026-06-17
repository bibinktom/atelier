"use client";
import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "auto";

const STORAGE_KEY = "atelier-theme";

function readStored(): Theme {
  if (typeof window === "undefined") return "auto";
  const v = window.localStorage.getItem(STORAGE_KEY);
  return v === "dark" || v === "light" ? v : "auto";
}

function applyTheme(t: Theme) {
  if (typeof window === "undefined") return;
  let isDark: boolean;
  if (t === "auto") {
    window.localStorage.removeItem(STORAGE_KEY);
    isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  } else {
    window.localStorage.setItem(STORAGE_KEY, t);
    isDark = t === "dark";
  }
  document.documentElement.classList.toggle("dark", isDark);
}

export function ThemeToggle() {
  // Start as null to avoid an SSR/CSR mismatch — read on mount instead.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(readStored());
    // Re-apply on system-pref change, but only when the user is on auto.
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (readStored() === "auto") {
        document.documentElement.classList.toggle("dark", mq.matches);
      }
    };
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  if (theme === null) {
    // Empty placeholder to keep the layout stable before mount.
    return <span className="inline-block h-7 w-7" aria-hidden />;
  }

  // Cycle: auto -> light -> dark -> auto …
  const next: Theme = theme === "auto" ? "light" : theme === "light" ? "dark" : "auto";
  const cycle = () => {
    applyTheme(next);
    setTheme(next);
  };

  const glyph = theme === "dark" ? "☾" : theme === "light" ? "☼" : "◐";
  const label =
    theme === "dark" ? "Dark theme" :
    theme === "light" ? "Light theme" :
    "Auto theme (matches system)";

  return (
    <button
      type="button"
      onClick={cycle}
      title={label + " — click for " + (next === "auto" ? "auto" : next)}
      aria-label={label}
      className="inline-flex h-7 w-7 items-center justify-center text-[15px] transition hover:opacity-70"
      style={{ color: "var(--color-muted)" }}
    >
      <span aria-hidden>{glyph}</span>
    </button>
  );
}
