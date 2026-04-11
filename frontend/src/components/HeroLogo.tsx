"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type EyeStatus = "idle" | "active" | "stopped";

interface Props {
  size?: number;
  status?: EyeStatus;
}

/**
 * Generates small lotus-petal paths arranged radially inside a pupil.
 * cx/cy = center, r = petal reach, n = petal count
 */
function lotusPetalPaths(cx: number, cy: number, r: number, n: number) {
  const paths: { d: string; rotate: number }[] = [];
  for (let i = 0; i < n; i++) {
    const angle = (360 / n) * i;
    // Each petal: a narrow leaf from center outward
    const d = `M${cx},${cy}
      C${cx - r * 0.22},${cy - r * 0.5} ${cx - r * 0.12},${cy - r}  ${cx},${cy - r}
      C${cx + r * 0.12},${cy - r}  ${cx + r * 0.22},${cy - r * 0.5} ${cx},${cy}Z`;
    paths.push({ d, rotate: angle });
  }
  return paths;
}

/* Color palettes keyed by status */
const PALETTES = {
  idle: {
    iris: ["#f5d4a8", "#e8975e", "#b05a2a"],
    petalFrom: "#f0a860",
    petalTo: "#e07830",
    seed: "#f5c28a",
    highlight: "#fff5eb",
    highlightSecondary: "#f0a860",
    socketStroke: "#e8975e",
    bgPetal: ["#f0a860", "#d4753a"],
    glow: "#f0a860",
    centerDot: "#f0a860",
  },
  active: {
    iris: ["#a8f5c0", "#3dcc6e", "#1a7a3a"],
    petalFrom: "#60f0a0",
    petalTo: "#30c060",
    seed: "#8af5b8",
    highlight: "#ebfff2",
    highlightSecondary: "#60f0a0",
    socketStroke: "#3dcc6e",
    bgPetal: ["#60f0a0", "#2a9e50"],
    glow: "#60f0a0",
    centerDot: "#60f0a0",
  },
  stopped: {
    iris: ["#f5b0a8", "#cc3d3d", "#7a1a1a"],
    petalFrom: "#f06060",
    petalTo: "#c03030",
    seed: "#f58a8a",
    highlight: "#ffebeb",
    highlightSecondary: "#f06060",
    socketStroke: "#cc3d3d",
    bgPetal: ["#f06060", "#9e2a2a"],
    glow: "#f06060",
    centerDot: "#f06060",
  },
} as const;

export function HeroLogo({ size = 280, status = "idle" }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [look, setLook] = useState({ x: 0, y: 0 });

  const isActive = status === "active";
  const pal = PALETTES[status];

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (isActive) return;
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const dx = e.clientX - cx;
      const dy = e.clientY - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const maxDist = Math.max(window.innerWidth, window.innerHeight) * 0.5;
      const norm = Math.min(dist / maxDist, 1);
      const angle = Math.atan2(dy, dx);
      setLook({
        x: Math.cos(angle) * norm,
        y: Math.sin(angle) * norm,
      });
    },
    [isActive]
  );

  useEffect(() => {
    window.addEventListener("mousemove", handleMouseMove);
    return () => window.removeEventListener("mousemove", handleMouseMove);
  }, [handleMouseMove]);

  useEffect(() => {
    if (!isActive) return;
    const id = requestAnimationFrame(() => setLook({ x: 0, y: 0 }));
    return () => cancelAnimationFrame(id);
  }, [isActive]);

  const maxTravel = 6;
  const px = look.x * maxTravel;
  const py = look.y * maxTravel * 0.65;

  // Eye centers — wider viewBox for larger eyes
  const leftEye = { cx: 65, cy: 55 };
  const rightEye = { cx: 135, cy: 55 };

  const eyeRx = 28;
  const eyeRy = 18;
  const irisRadius = 11;
  const pupilRadius = 6.5;

  // Lotus petals inside each pupil
  const petalCount = 8;
  const petalReach = 5.5;

  function renderEye(eye: { cx: number; cy: number }) {
    const ix = eye.cx + px;
    const iy = eye.cy + py;
    const petals = lotusPetalPaths(ix, iy, petalReach, petalCount);

    return (
      <g filter="url(#softGlow)">
        {/* Eye socket */}
        <ellipse
          cx={eye.cx} cy={eye.cy}
          rx={eyeRx} ry={eyeRy}
          fill="#0d0d0d"
          stroke={pal.socketStroke} strokeWidth="0.7" strokeOpacity="0.3"
        />
        {/* Iris */}
        <circle
          cx={ix} cy={iy}
          r={irisRadius}
          fill="url(#irisG)"
          style={{ transition: "cx 0.08s ease-out, cy 0.08s ease-out" }}
        />
        {/* Pupil base (dark) */}
        <circle
          cx={ix} cy={iy}
          r={pupilRadius}
          fill="#0d0d0d"
          style={{ transition: "cx 0.08s ease-out, cy 0.08s ease-out" }}
        />
        {/* Lotus petals inside the pupil */}
        <g style={{ transition: "transform 0.08s ease-out" }}>
          {petals.map((p, i) => (
            <path
              key={i}
              d={p.d}
              fill="url(#pupilPetalG)"
              opacity={0.7}
              transform={`rotate(${p.rotate} ${ix} ${iy})`}
              style={{ transition: "d 0.08s ease-out" }}
            />
          ))}
        </g>
        {/* Center seed of the lotus */}
        <circle
          cx={ix} cy={iy}
          r="1.5"
          fill={pal.seed} opacity="0.8"
          style={{ transition: "cx 0.08s ease-out, cy 0.08s ease-out" }}
        />
        {/* Highlight — warm white */}
        <circle
          cx={ix + 3} cy={iy - 3}
          r="2.2"
          fill={pal.highlight} opacity="0.85"
          style={{ transition: "cx 0.08s ease-out, cy 0.08s ease-out" }}
        />
        {/* Secondary warm highlight */}
        <circle
          cx={ix - 2} cy={iy + 2.5}
          r="1.1"
          fill={pal.highlightSecondary} opacity="0.3"
          style={{ transition: "cx 0.08s ease-out, cy 0.08s ease-out" }}
        />
      </g>
    );
  }

  return (
    <svg
      ref={svgRef}
      width={size}
      height={size * 0.55}
      viewBox="0 0 200 110"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ transition: "filter 0.6s ease" }}
    >
      <defs>
        {/* Background petal gradient — shifts with status */}
        <linearGradient id="heropetalG" x1="0" y1="0" x2="200" y2="110">
          <stop offset="0%" stopColor={pal.bgPetal[0]} />
          <stop offset="100%" stopColor={pal.bgPetal[1]} />
        </linearGradient>
        {/* Iris gradient — shifts with status */}
        <radialGradient id="irisG" cx="50%" cy="40%" r="50%">
          <stop offset="0%" stopColor={pal.iris[0]} />
          <stop offset="50%" stopColor={pal.iris[1]} />
          <stop offset="100%" stopColor={pal.iris[2]} />
        </radialGradient>
        {/* Tiny petals inside pupil */}
        <linearGradient id="pupilPetalG" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={pal.petalFrom} />
          <stop offset="100%" stopColor={pal.petalTo} />
        </linearGradient>
        {/* Ambient glow */}
        <radialGradient id="eyeGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={pal.glow} stopOpacity="0.1" />
          <stop offset="100%" stopColor={pal.glow} stopOpacity="0" />
        </radialGradient>
        <filter id="softGlow">
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="petalBlur">
          <feGaussianBlur stdDeviation="1.2" />
        </filter>
      </defs>

      {/* Warm ambient glow */}
      <ellipse cx="100" cy="55" rx="80" ry="45" fill="url(#eyeGlow)" />

      {/* --- Background lotus petals --- */}
      {/* Top */}
      <path
        d="M100 2 C88 25, 84 45, 100 55 C116 45, 112 25, 100 2Z"
        fill="url(#heropetalG)" opacity="0.14" filter="url(#petalBlur)"
      />
      {/* Bottom */}
      <path
        d="M100 108 C88 85, 84 65, 100 55 C116 65, 112 85, 100 108Z"
        fill="url(#heropetalG)" opacity="0.14" filter="url(#petalBlur)"
      />
      {/* Left */}
      <path
        d="M20 55 C42 42, 65 38, 100 55 C65 72, 42 68, 20 55Z"
        fill="url(#heropetalG)" opacity="0.12" filter="url(#petalBlur)"
      />
      {/* Right */}
      <path
        d="M180 55 C158 42, 135 38, 100 55 C135 72, 158 68, 180 55Z"
        fill="url(#heropetalG)" opacity="0.12" filter="url(#petalBlur)"
      />
      {/* Top-left */}
      <path
        d="M42 12 C54 32, 68 44, 100 55 C76 46, 54 30, 42 12Z"
        fill="url(#heropetalG)" opacity="0.1" filter="url(#petalBlur)"
      />
      {/* Top-right */}
      <path
        d="M158 12 C146 32, 132 44, 100 55 C124 46, 146 30, 158 12Z"
        fill="url(#heropetalG)" opacity="0.1" filter="url(#petalBlur)"
      />
      {/* Bottom-left */}
      <path
        d="M42 98 C54 78, 68 66, 100 55 C76 64, 54 80, 42 98Z"
        fill="url(#heropetalG)" opacity="0.1" filter="url(#petalBlur)"
      />
      {/* Bottom-right */}
      <path
        d="M158 98 C146 78, 132 66, 100 55 C124 64, 146 80, 158 98Z"
        fill="url(#heropetalG)" opacity="0.1" filter="url(#petalBlur)"
      />

      {/* --- Eyes --- */}
      {renderEye(leftEye)}
      {renderEye(rightEye)}

      {/* Center lotus seed between eyes */}
      <circle cx="100" cy="55" r="2.5" fill={pal.centerDot} opacity="0.2" />
    </svg>
  );
}
