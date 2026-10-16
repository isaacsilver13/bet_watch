export interface ChartPoint { at: string; value: number }

const WIDTH = 300;
const HEIGHT = 80;
const PAD = 8;

/** A plain SVG trend of one number over time: the consensus line at each snapshot. */
export function LineChart({
  label,
  points,
  format = String,
}: {
  label: string;
  points: ChartPoint[];
  format?: (value: number) => string;
}) {
  if (points.length < 2) {
    return <p className="slate-venue">{label}: not enough snapshots yet to draw a trend.</p>;
  }
  const times = points.map((point) => Date.parse(point.at));
  const values = points.map((point) => point.value);
  const [t0, t1] = [Math.min(...times), Math.max(...times)];
  const [low, high] = [Math.min(...values), Math.max(...values)];
  const x = (time: number) => PAD + (t1 === t0 ? 0 : ((time - t0) / (t1 - t0)) * (WIDTH - 2 * PAD));
  const y = (value: number) =>
    high === low ? HEIGHT / 2 : HEIGHT - PAD - ((value - low) / (high - low)) * (HEIGHT - 2 * PAD);
  const coordinates = points.map((_, index) => `${x(times[index]).toFixed(1)},${y(values[index]).toFixed(1)}`);
  const first = points[0];
  const last = points[points.length - 1];
  return (
    <figure className="line-chart">
      <svg aria-label={`${label} trend`} role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        <polyline fill="none" points={coordinates.join(" ")} stroke="currentColor" strokeWidth="2" />
        {coordinates.map((pair, index) => (
          <circle cx={pair.split(",")[0]} cy={pair.split(",")[1]} key={index} r="3" fill="currentColor" />
        ))}
      </svg>
      <figcaption>
        {label}: {format(first.value)} → {format(last.value)} over {points.length} snapshots
      </figcaption>
    </figure>
  );
}
