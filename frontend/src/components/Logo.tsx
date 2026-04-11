"use client";

import type { EyeStatus } from "./HeroLogo";

const COLORS = {
  idle:    { iris: "#e8975e", stroke: "#e8975e", highlight: "#fff5eb", petal: ["#f0a860", "#d4753a"] },
  active:  { iris: "#3dcc6e", stroke: "#3dcc6e", highlight: "#ebfff2", petal: ["#60f0a0", "#2a9e50"] },
  stopped: { iris: "#cc3d3d", stroke: "#cc3d3d", highlight: "#ffebeb", petal: ["#f06060", "#9e2a2a"] },
} as const;

export function Logo({ size = 32, status = "idle" }: { size?: number; status?: EyeStatus }) {
  const c = COLORS[status];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Outer lotus petals */}
      <path
        d="M32 4C32 4 20 18 20 32C20 46 32 60 32 60C32 60 44 46 44 32C44 18 32 4 32 4Z"
        fill="url(#sidebarPetalG)" opacity="0.3"
      />
      <path
        d="M4 32C4 32 18 20 32 20C46 20 60 32 60 32C60 32 46 44 32 44C18 44 4 32 4 32Z"
        fill="url(#sidebarPetalG)" opacity="0.3"
      />
      {/* Inner lotus petals */}
      <path
        d="M32 12C32 12 24 22 24 32C24 42 32 52 32 52C32 52 40 42 40 32C40 22 32 12 32 12Z"
        fill="url(#sidebarPetalG)" opacity="0.5"
      />
      <path
        d="M12 32C12 32 22 24 32 24C42 24 52 32 52 32C52 32 42 40 32 40C22 40 12 32 12 32Z"
        fill="url(#sidebarPetalG)" opacity="0.5"
      />
      {/* Left eye */}
      <ellipse cx="24" cy="32" rx="5" ry="3.5" fill="#0e0d0b" />
      <ellipse cx="24" cy="32" rx="5" ry="3.5" stroke={c.stroke} strokeWidth="1" />
      <circle cx="24" cy="32" r="1.8" fill={c.iris} />
      <circle cx="24.8" cy="31.4" r="0.6" fill={c.highlight} />
      {/* Right eye */}
      <ellipse cx="40" cy="32" rx="5" ry="3.5" fill="#0e0d0b" />
      <ellipse cx="40" cy="32" rx="5" ry="3.5" stroke={c.stroke} strokeWidth="1" />
      <circle cx="40" cy="32" r="1.8" fill={c.iris} />
      <circle cx="40.8" cy="31.4" r="0.6" fill={c.highlight} />
      {/* Glow ring */}
      <circle cx="32" cy="32" r="28" stroke="url(#sidebarPetalG)" strokeWidth="0.5" opacity="0.2" />
      <defs>
        <linearGradient id="sidebarPetalG" x1="0" y1="0" x2="64" y2="64">
          <stop offset="0%" stopColor={c.petal[0]} />
          <stop offset="100%" stopColor={c.petal[1]} />
        </linearGradient>
      </defs>
    </svg>
  );
}
