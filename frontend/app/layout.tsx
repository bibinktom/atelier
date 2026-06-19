import type { Metadata, Viewport } from "next";
import { Fraunces, Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";

// Type stack is part of the "paper-and-ink atelier" identity, not interchangeable:
// Fraunces (display) + Instrument Sans (body) + JetBrains Mono (code).
// Do NOT swap to Inter / Geist / Space Grotesk — that erases the look.
const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
});
const instrument = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-instrument",
  display: "swap",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Atelier",
  description: "A private AI workspace for research, writing, spreadsheets, and slide decks.",
  manifest: "/manifest.webmanifest",
  applicationName: "Atelier",
  appleWebApp: {
    capable: true,
    title: "Atelier",
    statusBarStyle: "black-translucent",
  },
  icons: {
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  // viewport-fit=cover lets the iOS notch + home indicator areas be reachable;
  // safe-area-inset-* paddings in CSS keep content out of those zones.
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)",  color: "#0a0a0a" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${instrument.variable} ${jetbrains.variable}`}>
      <head>
        {/* Synchronous, pre-hydration theme bootstrap so the first paint already
            has the right palette. Source lives in /public so we avoid inline
            scripts entirely. ThemeToggle writes localStorage('atelier-theme'). */}
        <script src="/theme-bootstrap.js" />
      </head>
      <body className="min-h-dvh">
        {children}
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
