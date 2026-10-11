import { useEffect, useRef } from "react";

// Background for the sign-in page: double helices drawn as thin lines on a 2D canvas.
// Each helix is two sine strands half a turn apart with rungs between them; depth drives line weight and
// opacity, so the strand turning away fades and the one turning toward the viewer sharpens.

type Helix = { x: number; y: number; angle: number; length: number; radius: number; turns: number; speed: number; phase: number; weight: number; alpha: number };

// Positions and sizes are fractions of the viewport, so the composition holds at any size. Far to near.
const HELICES: Helix[] = [
  { x: 0.56, y: 1.14, angle: -0.98, length: 0.86, radius: 0.034, turns: 6, speed: 0.11, phase: 2.6, weight: 1, alpha: 0.22 },
  { x: 0.28, y: -0.14, angle: 0.6, length: 1.2, radius: 0.05, turns: 6.5, speed: -0.15, phase: 1.3, weight: 1.3, alpha: 0.36 },
  // The main strand rises from the bottom edge, passes under the copy and behind the card, and leaves top right.
  { x: 0.2, y: 1.2, angle: -0.72, length: 1.12, radius: 0.088, turns: 4.5, speed: 0.2, phase: 0, weight: 2.1, alpha: 0.82 },
];
const SAMPLES = 240;
const RUNG_EVERY = 6;
const PURPLE = [139, 92, 246];
const PINK = [236, 72, 153];
const TAU = Math.PI * 2;

const colour = (t: number, alpha: number) =>
  `rgba(${Math.round(PURPLE[0] + (PINK[0] - PURPLE[0]) * t)},${Math.round(PURPLE[1] + (PINK[1] - PURPLE[1]) * t)},${Math.round(PURPLE[2] + (PINK[2] - PURPLE[2]) * t)},${alpha})`;

export default function HelixBackground() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0, height = 0, frame = 0;
    const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
    const motes = Array.from({ length: 34 }, (_, i) => ({ x: (i * 0.618) % 1, y: (i * 0.371) % 1, r: 0.8 + (i % 4) * 0.5, drift: 0.004 + (i % 5) * 0.002 }));

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (still) draw(0);
    };

    const draw = (time: number) => {
      ctx.clearRect(0, 0, width, height);
      ctx.lineCap = "round";
      const diagonal = Math.hypot(width, height);
      const short = Math.min(width, height);

      for (const m of motes) {
        const y = ((m.y - time * m.drift) % 1 + 1) % 1;
        ctx.fillStyle = colour(m.x, 0.22);
        ctx.beginPath();
        ctx.arc(m.x * width + Math.sin(time * 0.3 + m.y * 9) * 12, y * height, m.r, 0, TAU);
        ctx.fill();
      }

      for (const h of HELICES) {
        const dirX = Math.cos(h.angle), dirY = Math.sin(h.angle);
        const normX = -dirY, normY = dirX;
        const radius = h.radius * short;
        const length = h.length * diagonal;
        // The nearer the helix, the more it leans toward the pointer.
        const lean = h.weight * 9;
        const originX = h.x * width + pointer.x * lean, originY = h.y * height + pointer.y * lean;
        const points: { x: number; y: number; depth: number }[][] = [[], []];

        for (let i = 0; i <= SAMPLES; i++) {
          const t = i / SAMPLES;
          const bend = Math.sin(t * 4.2 + time * 0.25 + h.phase) * radius * 0.9;
          const baseX = originX + dirX * t * length + normX * bend;
          const baseY = originY + dirY * t * length + normY * bend;
          const turn = t * h.turns * TAU + h.phase + time * h.speed + pointer.x * 0.5;
          for (let s = 0; s < 2; s++) {
            const z = Math.sin(turn + s * Math.PI);
            const offset = radius * Math.cos(turn + s * Math.PI) * (1 + z * 0.22);
            points[s].push({ x: baseX + normX * offset, y: baseY + normY * offset, depth: (z + 1) / 2 });
          }
        }

        for (let i = 0; i <= SAMPLES; i += RUNG_EVERY) {
          const a = points[0][i], b = points[1][i], t = i / SAMPLES;
          ctx.strokeStyle = colour(t, h.alpha * 0.26);
          ctx.lineWidth = h.weight * 0.6;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          for (const p of [a, b]) {
            ctx.fillStyle = colour(t, h.alpha * (0.25 + 0.75 * p.depth));
            ctx.beginPath(); ctx.arc(p.x, p.y, h.weight * (0.9 + p.depth * 1.5), 0, TAU); ctx.fill();
          }
        }

        // Far halves first, near halves on top.
        for (const near of [false, true]) {
          for (const strand of points) {
            for (let i = 0; i < SAMPLES; i++) {
              const p = strand[i], q = strand[i + 1];
              const depth = (p.depth + q.depth) / 2;
              if ((depth >= 0.5) !== near) continue;
              ctx.strokeStyle = colour(i / SAMPLES, h.alpha * (0.16 + 0.84 * depth));
              ctx.lineWidth = h.weight * (0.55 + depth * 1.15);
              ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
            }
          }
        }
      }
    };

    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (document.hidden) return;
      pointer.x += (pointer.tx - pointer.x) * 0.04;
      pointer.y += (pointer.ty - pointer.y) * 0.04;
      draw(now / 1000);
    };
    const move = (e: PointerEvent) => {
      pointer.tx = (e.clientX / window.innerWidth) * 2 - 1;
      pointer.ty = (e.clientY / window.innerHeight) * 2 - 1;
    };

    resize();
    window.addEventListener("resize", resize);
    if (!still) {
      window.addEventListener("pointermove", move);
      frame = requestAnimationFrame(tick);
    }
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", move);
    };
  }, []);

  return <canvas ref={ref} className="helix-canvas" aria-hidden="true" />;
}
