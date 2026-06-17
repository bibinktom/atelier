export function TomoseMark({ className = "" }: { className?: string }) {
  return (
    <p
      className={`text-[11px] tracking-[0.16em] uppercase ${className}`}
      style={{ color: "var(--color-muted)" }}
    >
      Made by{" "}
      <span style={{ color: "var(--color-ink)" }} className="font-medium">
        Tomose Systems
      </span>
    </p>
  );
}
