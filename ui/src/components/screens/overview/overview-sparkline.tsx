import { useId } from "react";

export function Sparkline({
  values,
  color,
}: {
  values: number[];
  color: string;
}) {
  const gradientId = useId();

  if (values.length < 2) return null;

  const width = 100;
  const height = 34;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;

  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - 3 - ((value - min) / span) * (height - 8);
    return [x, y] as const;
  });

  const line = points
    .map(
      (point, index) =>
        `${index ? "L" : "M"}${point[0].toFixed(2)} ${point[1].toFixed(2)}`,
    )
    .join(" ");
  const area = `${line} L${width} ${height} L0 ${height} Z`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-9 w-full"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.26} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
