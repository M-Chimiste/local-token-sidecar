// components.jsx — Chart, KPI, ModelTable, HostBars, ActivityFeed, Controls.

const fmt = {
  int: (n) => Math.round(n).toLocaleString('en-US'),
  abbr: (n) => {
    if (n == null || isNaN(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 1 : 2).replace(/\.0+$/, '') + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.0+$/, '') + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 1 : 2).replace(/\.0+$/, '') + 'k';
    return String(Math.round(n));
  },
  ms: (n) => (n == null ? '—' : (n >= 1000 ? (n / 1000).toFixed(2) + 's' : Math.round(n) + 'ms')),
  pct: (n) => (n >= 0 ? '+' : '') + (n * 100).toFixed(1) + '%',
  hour: (h) => String(h).padStart(2, '0') + ':00',
  shortDate: (iso) => {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  },
  hms: (iso) => {
    const d = new Date(iso);
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map(n => String(n).padStart(2, '0')).join(':');
  },
};

// ───────────────────────── Theme toggle ─────────────────────────────────
// Single cycling button: auto → quiet → terminal → auto. The dot is shown
// when pref is 'auto' so the user can tell the OS is driving the choice.
function ThemeToggle({ pref, effective, onCycle }) {
  const glyph = effective === 'terminal' ? '☾' : '☀';
  const nextLabel = ({ auto: 'light', quiet: 'dark', terminal: 'auto' })[pref];
  const stateLabel = pref === 'auto'
    ? `Auto (${effective === 'terminal' ? 'dark' : 'light'})`
    : (pref === 'quiet' ? 'Light' : 'Dark');
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={onCycle}
      title={`Theme: ${stateLabel} — click for ${nextLabel}`}
      aria-label={`Theme: ${stateLabel}. Click to switch to ${nextLabel}.`}
    >
      <span className="theme-toggle-glyph">{glyph}</span>
      {pref === 'auto' && <span className="theme-toggle-auto">auto</span>}
    </button>
  );
}

// ───────────────────────── KPI tile ─────────────────────────────────────
function KPI({ label, value, unit, delta, deltaLabel, spark, color }) {
  let deltaCls = 'flat';
  let deltaTxt = '—';
  if (delta != null && !isNaN(delta)) {
    if (delta > 0.005) deltaCls = 'pos';
    else if (delta < -0.005) deltaCls = 'neg';
    deltaTxt = fmt.pct(delta);
  }
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      <div className="kpi-foot">
        <span className={`delta ${deltaCls}`}>
          {deltaCls === 'pos' ? '▲' : deltaCls === 'neg' ? '▼' : '·'} {deltaTxt}
        </span>
        <span style={{ color: 'var(--text-3)' }}>{deltaLabel || 'vs prev period'}</span>
      </div>
      {spark && <Sparkline data={spark} color={color} className="kpi-spark" />}
    </div>
  );
}

function Sparkline({ data, color, className }) {
  if (!data || data.length < 2) return null;
  const w = 130, h = 36;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = Math.max(max - min, 1);
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return [x, y];
  });
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const area = path + ` L${w},${h} L0,${h} Z`;
  return (
    <svg className={className} width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <path d={area} fill={color || 'var(--accent)'} opacity="0.13" />
      <path d={path} fill="none" stroke={color || 'var(--accent)'} strokeWidth="1.5" />
    </svg>
  );
}

// ───────────────────────── Stacked time-series chart ────────────────────
function TimeSeriesChart({ buckets, models, modelColors, granularity, mode }) {
  // buckets: [{ key, label, byModel: {modelName: tokens}, total }]
  const wrapRef = React.useRef(null);
  const [width, setWidth] = React.useState(900);
  const [hoverIdx, setHoverIdx] = React.useState(null);

  React.useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const H = 280;
  const padL = 52, padR = 16, padT = 14, padB = 30;
  const innerW = width - padL - padR;
  const innerH = H - padT - padB;

  const maxY = Math.max(1, ...buckets.map(b => b.total));
  const niceMax = niceCeil(maxY);
  const bw = innerW / Math.max(1, buckets.length);
  const barW = Math.max(1, bw * 0.78);

  const yTicks = 4;
  const ticks = [];
  for (let i = 0; i <= yTicks; i++) ticks.push((niceMax / yTicks) * i);

  // line series total
  const linePts = buckets.map((b, i) => {
    const x = padL + bw * (i + 0.5);
    const y = padT + innerH - (b.total / niceMax) * innerH;
    return [x, y];
  });
  const linePath = linePts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const areaPath = linePath + ` L${padL + innerW},${padT + innerH} L${padL},${padT + innerH} Z`;

  // x-axis ticks — show ~6 labels
  const labelEvery = Math.max(1, Math.floor(buckets.length / 6));

  // Tooltip positioning
  let tooltip = null;
  if (hoverIdx != null && buckets[hoverIdx]) {
    const b = buckets[hoverIdx];
    const x = padL + bw * (hoverIdx + 0.5);
    const entries = models
      .map(m => ({ m, v: b.byModel[m] || 0 }))
      .filter(e => e.v > 0)
      .sort((a, b2) => b2.v - a.v);
    tooltip = (
      <div className="tooltip" style={{ left: x, top: padT - 4 }}>
        <div className="ttip-h">{b.label}</div>
        {entries.map(e => (
          <div className="ttip-row" key={e.m}>
            <span className="lt"><span className="dot" style={{ '--c': modelColors[e.m] }}></span>{e.m}</span>
            <span className="v">{fmt.abbr(e.v)}</span>
          </div>
        ))}
        <div className="ttip-tot ttip-row">
          <span>total</span>
          <span className="v">{fmt.int(b.total)} tok</span>
        </div>
      </div>
    );
  }

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <div className="chart">
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`}>
          {/* grid + y labels */}
          {ticks.map((t, i) => {
            const y = padT + innerH - (t / niceMax) * innerH;
            return (
              <g key={i}>
                <line x1={padL} x2={padL + innerW} y1={y} y2={y}
                      stroke="var(--grid)" strokeWidth="1"
                      strokeDasharray={i === 0 ? '0' : '2 3'} />
                <text x={padL - 8} y={y + 3.5} textAnchor="end"
                      fontFamily="var(--font-mono)" fontSize="10"
                      fill="var(--text-3)" style={{ letterSpacing: '0.02em' }}>
                  {fmt.abbr(t)}
                </text>
              </g>
            );
          })}
          {/* bars stacked OR area */}
          {mode === 'stack' && buckets.map((b, i) => {
            let yCursor = padT + innerH;
            const x = padL + bw * i + (bw - barW) / 2;
            return (
              <g key={b.key}>
                {models.map(m => {
                  const v = b.byModel[m] || 0;
                  if (v <= 0) return null;
                  const segH = (v / niceMax) * innerH;
                  yCursor -= segH;
                  return (
                    <rect key={m} x={x} y={yCursor} width={barW} height={Math.max(0.5, segH)}
                          fill={modelColors[m]}
                          opacity={hoverIdx == null || hoverIdx === i ? 0.95 : 0.35} />
                  );
                })}
              </g>
            );
          })}
          {mode === 'area' && (
            <g>
              <path d={areaPath} fill="var(--accent)" opacity="0.14" />
              <path d={linePath} stroke="var(--accent)" strokeWidth="1.8" fill="none" />
              {linePts.map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r={hoverIdx === i ? 3.5 : 0}
                        fill="var(--accent)" stroke="var(--panel)" strokeWidth="1.5" />
              ))}
            </g>
          )}
          {/* x labels */}
          {buckets.map((b, i) => {
            if (i % labelEvery !== 0) return null;
            const x = padL + bw * (i + 0.5);
            return (
              <text key={b.key} x={x} y={H - padB + 18} textAnchor="middle"
                    fontFamily="var(--font-mono)" fontSize="10" fill="var(--text-3)">
                {b.label}
              </text>
            );
          })}
          {/* hover overlay */}
          {buckets.map((b, i) => (
            <rect key={'h' + b.key}
                  x={padL + bw * i} y={padT}
                  width={bw} height={innerH}
                  fill="transparent"
                  onMouseEnter={() => setHoverIdx(i)}
                  onMouseLeave={() => setHoverIdx(null)} />
          ))}
          {/* hover vertical guide */}
          {hoverIdx != null && (
            <line x1={padL + bw * (hoverIdx + 0.5)} x2={padL + bw * (hoverIdx + 0.5)}
                  y1={padT} y2={padT + innerH}
                  stroke="var(--text-2)" strokeWidth="0.8" strokeDasharray="2 3" opacity="0.5" />
          )}
        </svg>
        {tooltip}
      </div>
    </div>
  );
}

function niceCeil(v) {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  let nice;
  if (n <= 1) nice = 1;
  else if (n <= 2) nice = 2;
  else if (n <= 2.5) nice = 2.5;
  else if (n <= 5) nice = 5;
  else nice = 10;
  return nice * pow;
}

// ───────────────────────── Model leaderboard ────────────────────────────
function ModelTable({ rows, modelColors, maxTokens }) {
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>Model</th>
          <th className="num">Requests</th>
          <th className="num">Prompt</th>
          <th className="num">Completion</th>
          <th className="num">Total</th>
          <th className="num">Avg ms</th>
          <th>Share</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.model}>
            <td>
              <div className="modelcell" style={{ '--c': modelColors[r.model] }}>
                <span className="dot"></span>{r.model}
              </div>
            </td>
            <td className="num">{fmt.int(r.calls)}</td>
            <td className="num">{fmt.abbr(r.prompt)}</td>
            <td className="num">{fmt.abbr(r.completion)}</td>
            <td className="num">{fmt.abbr(r.total)}</td>
            <td className="num">{fmt.ms(r.avgMs)}</td>
            <td className="barcell">
              <div className="bar" style={{
                '--c': modelColors[r.model],
                width: `${Math.max(2, (r.total / maxTokens) * 100)}%`
              }}></div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ───────────────────────── Host breakdown ───────────────────────────────
function HostBars({ hosts, modelColors }) {
  const max = Math.max(1, ...hosts.map(h => h.total));
  return (
    <div className="hosts">
      {hosts.map(h => {
        const segs = Object.entries(h.byModel).sort((a, b) => b[1] - a[1]);
        const widthPct = (h.total / max) * 100;
        return (
          <div className="host-row" key={h.host}>
            <div className="host-row-top">
              <span className="host-name">{h.host}</span>
              <span className="host-tot">{fmt.abbr(h.total)} tok · {fmt.int(h.calls)} req</span>
            </div>
            <div className="host-bar" style={{ width: `${widthPct}%` }}>
              {segs.map(([m, v]) => (
                <div key={m} className="seg-bar"
                     title={`${m}: ${fmt.abbr(v)}`}
                     style={{ '--c': modelColors[m], width: `${(v / h.total) * 100}%` }}></div>
              ))}
            </div>
            <div className="host-sub">
              {segs.slice(0, 2).map(([m, v]) => `${m.split('-')[0]} ${Math.round((v / h.total) * 100)}%`).join(' · ')}
              {segs.length > 2 ? ` · +${segs.length - 2}` : ''}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ───────────────────────── Activity feed ────────────────────────────────
function ActivityFeed({ rows, modelColors, newIds }) {
  return (
    <div className="feed">
      {rows.map(r => (
        <div key={r.id} className={`feed-row${newIds.has(r.id) ? ' new' : ''}`}>
          <span className="t">{fmt.hms(r.ts)}</span>
          <span className="h" title={r.host}>{r.host}</span>
          <span className="m" style={{ '--c': modelColors[r.model] }} title={r.model}>
            <span className="dot"></span>
            <span className="m-name">{r.model}</span>
          </span>
          <span className="io">{fmt.int(r.prompt)} in / {fmt.int(r.completion)} out</span>
          <span className="toks">{fmt.int(r.total)}</span>
          <span className="ms">{fmt.ms(r.ms)}</span>
        </div>
      ))}
    </div>
  );
}

// ───────────────────────── Controls ─────────────────────────────────────
function Controls({ range, onRange, models, modelColors, selectedModels, onToggleModel, onResetModels, liveOn, onToggleLive }) {
  const ranges = [
    { k: '1d',  label: 'Today' },
    { k: '7d',  label: '7d' },
    { k: '30d', label: '30d' },
    { k: 'all', label: 'All' },
  ];
  return (
    <div className="ctrls">
      <div className="ctrl-group">
        <span className="ctrl-lbl">Range</span>
        <div className="seg">
          {ranges.map(r => (
            <button key={r.k} className={range === r.k ? 'on' : ''} onClick={() => onRange(r.k)}>{r.label}</button>
          ))}
        </div>
      </div>
      <div className="ctrl-group" style={{ flexWrap: 'wrap' }}>
        <span className="ctrl-lbl">Models</span>
        {models.map(m => (
          <span key={m}
                className={`chip ${selectedModels.has(m) ? 'on' : ''}`}
                style={{ '--c': modelColors[m] }}
                onClick={() => onToggleModel(m)}>
            <span className="chip-dot" style={{ background: modelColors[m] }}></span>{m}
          </span>
        ))}
        {selectedModels.size < models.length && (
          <span className="chip" onClick={onResetModels}>reset</span>
        )}
      </div>
      <div className="ctrl-group" style={{ marginLeft: 'auto' }}>
        <span className="ctrl-lbl">Auto-refresh</span>
        <div className="seg">
          <button className={liveOn ? 'on' : ''} onClick={() => onToggleLive(true)}>On</button>
          <button className={!liveOn ? 'on' : ''} onClick={() => onToggleLive(false)}>Off</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { KPI, Sparkline, TimeSeriesChart, ModelTable, HostBars, ActivityFeed, Controls, fmt });
