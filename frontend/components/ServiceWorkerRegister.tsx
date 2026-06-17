"use client";
import { useEffect } from "react";

// Registers the PWA service worker on first load. Only runs over HTTPS — local
// `npm run dev` over plain HTTP won't try to register, since the browser
// would reject it anyway and dev SW caching tends to fight hot reload.
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    if (window.location.protocol !== "https:") return;
    navigator.serviceWorker.register("/sw.js").catch(() => { /* swallow */ });
  }, []);
  return null;
}
