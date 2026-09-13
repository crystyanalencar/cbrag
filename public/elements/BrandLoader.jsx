export default function BrandLoader() {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", height: 14 }}>
      <style>{`
        @keyframes brand-dot-pulse {
          0%, 80%, 100% { opacity: 0.25; transform: scale(0.7); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
      {[
        { color: "#1428A0", border: "none", delay: "0s" },
        { color: "#E4032E", border: "none", delay: "0.2s" },
        { color: "#FFFFFF", border: "1px solid #c8c8c8", delay: "0.4s" },
      ].map((dot, i) => (
        <span
          key={i}
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            display: "inline-block",
            marginRight: i < 2 ? 5 : 0,
            background: dot.color,
            border: dot.border,
            animation: `brand-dot-pulse 1.1s ease-in-out infinite`,
            animationDelay: dot.delay,
          }}
        />
      ))}
    </div>
  );
}
