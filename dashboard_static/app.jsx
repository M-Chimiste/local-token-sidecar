// app.jsx — Token Sidecar dashboard root.
// Server-backed: every panel is fed by a /api/* call. The mock dataset that
// existed in the original design (data.js) is gone — all numbers are real.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "quiet",
  "chartMode": "stack",
  "showSparklines": true,
  "liveDefault": true
}/*EDITMODE-END*/;

// Poll cadence; overridable by config bootstrap (see /api/meta in future).
const POLL_INTERVAL_MS = 2200;
// Refetch the heavier panels (chart/leaderboard/by-host/kpi) every N polls
// so they catch up to new rows without thrashing Postgres.
const HEAVY_REFRESH_EVERY = 10;

// ───────────────────────── helpers ───────────────────────────────────────

function buildQS(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
    if (Array.isArray(v)) v.forEach(x => u.append(k, String(x)));
    else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? '?' + s : '';
}

async function jget(path) {
  const r = await fetch(path, { credentials: 'omit' });
  if (!r.ok) throw new Error(path + ': ' + r.status);
  return r.json();
}

function rangeLabelOf(range) {
  return ({ '1d': 'today', '7d': 'last 7 days', '30d': 'last 30 days', 'all': 'all time' })[range];
}

// "Active models" sparkline uses the server's sparkline_14d series; KPIs
// project the right axis out of it.
function spark(series, key) {
  return series && series.length ? series.map(p => p[key] || 0) : null;
}

function pctDelta(today, yest) {
  if (yest == null || yest === 0) return null;
  return (today - yest) / yest;
}

// ───────────────────────── App ───────────────────────────────────────────

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', t.theme);
  }, [t.theme]);

  // ─── Server-backed state
  const [meta, setMeta]               = React.useState({ now: null, models: [], hosts: [] });
  const [kpi, setKpi]                 = React.useState(null);
  const [chart, setChart]             = React.useState({ granularity: 'day', buckets: [] });
  const [leaderboard, setLeaderboard] = React.useState([]);
  const [byHost, setByHost]           = React.useState([]);
  const [feed, setFeed]               = React.useState([]);  // newest-first
  const cursorRef                     = React.useRef({ ts: null, id: null });
  const feedIdSetRef                  = React.useRef(new Set());
  const newIdsRef                     = React.useRef(new Set());

  // ─── Controls
  const [range, setRange]                   = React.useState('7d');
  const [selectedModels, setSelectedModels] = React.useState(null); // null = all
  const [liveOn, setLiveOn]                 = React.useState(t.liveDefault);
  const [clockStr, setClockStr]             = React.useState('');
  const [bootError, setBootError]           = React.useState(null);

  // Stable color assignment per model — recomputed when meta.models changes.
  const modelColors = React.useMemo(() => {
    const palette = [
      'var(--accent)', 'var(--accent-2)', 'var(--accent-3)',
      'var(--accent-4)', 'var(--accent-5)',
    ];
    const m = {};
    meta.models.forEach((mod, i) => { m[mod] = palette[i % palette.length]; });
    return m;
  }, [meta.models]);

  // Whether to pass a model filter to the server (null/empty = "all").
  const filterModels = React.useMemo(() => {
    if (!selectedModels) return null;
    if (selectedModels.size === 0 || selectedModels.size === meta.models.length) return null;
    return Array.from(selectedModels);
  }, [selectedModels, meta.models]);

  // ─── Live UTC clock
  React.useEffect(() => {
    const update = () => {
      const d = new Date(Date.now());
      setClockStr(d.toUTCString().slice(17, 25) + ' UTC');
    };
    update();
    const i = setInterval(update, 1000);
    return () => clearInterval(i);
  }, []);

  // ─── Initial bootstrap: /api/meta + first /api/rows (cold load).
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const m = await jget('/api/meta');
        if (cancelled) return;
        setMeta(m);
        setSelectedModels(new Set(m.models));

        const rows = await jget('/api/rows?limit=50');
        if (cancelled) return;
        setFeed(rows);
        feedIdSetRef.current = new Set(rows.map(r => r.id));
        if (rows.length) {
          cursorRef.current = { ts: rows[0].ts, id: rows[0].id };
        }
      } catch (e) {
        console.error(e);
        if (!cancelled) setBootError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ─── Heavy panels refetch whenever range or model filter changes.
  React.useEffect(() => {
    let cancelled = false;
    if (!meta.now) return;  // wait for bootstrap
    (async () => {
      const q = buildQS({ range, model: filterModels || undefined });
      const kpiQ = buildQS({ model: filterModels || undefined });
      try {
        const [k, b, lb, bh] = await Promise.all([
          jget('/api/kpi'         + kpiQ),
          jget('/api/buckets'     + q),
          jget('/api/leaderboard' + q),
          jget('/api/by-host'     + q),
        ]);
        if (cancelled) return;
        setKpi(k); setChart(b); setLeaderboard(lb); setByHost(bh);
      } catch (e) {
        console.error(e);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, filterModels, meta.now]);

  // ─── Live tick: poll /api/rows by cursor; refresh heavy panels every Nth.
  const tickCountRef = React.useRef(0);
  React.useEffect(() => {
    if (!liveOn || !meta.now) return;
    const i = setInterval(async () => {
      tickCountRef.current += 1;
      try {
        const cur = cursorRef.current;
        const qs = buildQS({
          since_ts: cur.ts, since_id: cur.id, limit: 200,
          model: filterModels || undefined,
        });
        const batch = await jget('/api/rows' + qs);

        if (batch.length) {
          // Client merge rule (matches plan §"Polling cursor"):
          //   1. drop ids already in feedIdSet
          //   2. reverse (server returned oldest-first)
          //   3. prepend
          //   4. trim to 50
          //   5. update cursor from batch[0] post-reverse
          const fresh = batch.filter(r => !feedIdSetRef.current.has(r.id));
          if (fresh.length) {
            fresh.reverse();
            const merged = fresh.concat(feed).slice(0, 50);
            newIdsRef.current = new Set(fresh.map(r => r.id));
            feedIdSetRef.current = new Set(merged.map(r => r.id));
            setFeed(merged);
            cursorRef.current = { ts: fresh[0].ts, id: fresh[0].id };
          }
        }

        if (tickCountRef.current % HEAVY_REFRESH_EVERY === 0) {
          const q = buildQS({ range, model: filterModels || undefined });
          const kpiQ = buildQS({ model: filterModels || undefined });
          const [k, b, lb, bh] = await Promise.all([
            jget('/api/kpi'         + kpiQ),
            jget('/api/buckets'     + q),
            jget('/api/leaderboard' + q),
            jget('/api/by-host'     + q),
          ]);
          setKpi(k); setChart(b); setLeaderboard(lb); setByHost(bh);
        }
      } catch (e) {
        console.error(e);
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveOn, range, filterModels, meta.now, feed]);

  // ─── Controls handlers
  const toggleModel = (m) => {
    setSelectedModels(prev => {
      const next = new Set(prev || meta.models);
      if (next.has(m)) next.delete(m); else next.add(m);
      if (next.size === 0) return new Set(meta.models);
      return next;
    });
  };
  const resetModels = () => setSelectedModels(new Set(meta.models));

  // ─── Derived view data
  const maxLeaderboard = leaderboard.length ? leaderboard[0].total : 1;
  const rangeLbl = rangeLabelOf(range);
  const gran = chart.granularity;
  const today  = kpi?.today               || { tok: 0, req: 0, avgMs: 0, models: 0 };
  const yest   = kpi?.yesterday_same_time || { tok: 0, req: 0, avgMs: 0, models: 0 };
  const spark14 = kpi?.sparkline_14d || [];

  // KPI value formatters
  const avgMsValue = today.avgMs ? (today.avgMs >= 1000 ? (today.avgMs / 1000).toFixed(2) : Math.round(today.avgMs)) : '—';
  const avgMsUnit  = today.avgMs ? (today.avgMs >= 1000 ? 's' : 'ms') : '';

  return (
    <div className="app">
      <div className="shell">
        {/* Header */}
        <header className="hdr">
          <div className="hdr-l">
            <div className="hdr-mark">ts</div>
            <div>
              <h1 className="hdr-title">Token Sidecar</h1>
              <div className="hdr-sub">central · postgres · token_usage</div>
            </div>
          </div>
          <div className="hdr-r">
            <span className={`live-dot${liveOn ? '' : ' paused'}`}>{liveOn ? 'live' : 'paused'}</span>
            <span className="hdr-clock">{clockStr}</span>
          </div>
        </header>

        {bootError && (
          <div className="card" style={{ borderColor: 'var(--neg)' }}>
            <div className="card-title">Failed to load dashboard data</div>
            <div className="card-sub">{bootError}</div>
          </div>
        )}

        {/* Controls */}
        <Controls
          range={range}
          onRange={setRange}
          models={meta.models}
          modelColors={modelColors}
          selectedModels={selectedModels || new Set()}
          onToggleModel={toggleModel}
          onResetModels={resetModels}
          liveOn={liveOn}
          onToggleLive={setLiveOn}
        />

        {/* KPI row */}
        <div className="kpi-row">
          <KPI
            label="Tokens today"
            value={fmt.abbr(today.tok)}
            delta={pctDelta(today.tok, yest.tok)}
            deltaLabel="vs same time yesterday"
            spark={t.showSparklines ? spark(spark14, 'tok') : null}
            color="var(--accent)"
          />
          <KPI
            label="Requests today"
            value={fmt.int(today.req)}
            delta={pctDelta(today.req, yest.req)}
            deltaLabel="vs same time yesterday"
            spark={t.showSparklines ? spark(spark14, 'req') : null}
            color="var(--accent-2)"
          />
          <KPI
            label="Avg response"
            value={avgMsValue}
            unit={avgMsUnit}
            delta={pctDelta(today.avgMs, yest.avgMs)}
            deltaLabel="vs same time yesterday"
            spark={t.showSparklines ? spark(spark14, 'ms') : null}
            color="var(--accent-3)"
          />
          <KPI
            label="Active models"
            value={fmt.int(today.models)}
            delta={pctDelta(today.models, yest.models)}
            deltaLabel="vs same time yesterday"
            spark={t.showSparklines ? spark(spark14, 'models') : null}
            color="var(--accent-4)"
          />
        </div>

        {/* Main row: chart + host breakdown */}
        <div className="row-main">
          <div className="card chart-card">
            <div className="card-h">
              <div>
                <h2 className="card-title">Tokens over time</h2>
                <div className="card-sub">
                  {rangeLbl} · {gran === 'hour' ? 'hourly' : gran === 'week' ? 'weekly' : 'daily'} buckets · stacked by model
                </div>
              </div>
              <div className="seg">
                <button className={t.chartMode === 'stack' ? 'on' : ''} onClick={() => setTweak('chartMode', 'stack')}>Stacked</button>
                <button className={t.chartMode === 'area' ? 'on' : ''} onClick={() => setTweak('chartMode', 'area')}>Total</button>
              </div>
            </div>
            <TimeSeriesChart
              buckets={chart.buckets}
              models={meta.models}
              modelColors={modelColors}
              granularity={gran}
              mode={t.chartMode}
            />
            <div className="chart-legend">
              {meta.models
                .filter(m => !selectedModels || selectedModels.has(m))
                .map(m => (
                  <span className="leg" key={m}>
                    <span className="swatch" style={{ '--c': modelColors[m] }}></span>{m}
                  </span>
                ))}
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <div>
                <h2 className="card-title">By machine</h2>
                <div className="card-sub">{rangeLbl} · share by model</div>
              </div>
            </div>
            <HostBars hosts={byHost} modelColors={modelColors} />
          </div>
        </div>

        {/* Bottom row: model leaderboard + recent activity */}
        <div className="row-bottom">
          <div className="card">
            <div className="card-h">
              <div>
                <h2 className="card-title">Models leaderboard</h2>
                <div className="card-sub">{rangeLbl} · sorted by total tokens</div>
              </div>
              <div className="card-sub">{fmt.int(leaderboard.reduce((a, r) => a + r.calls, 0))} requests</div>
            </div>
            <ModelTable rows={leaderboard} modelColors={modelColors} maxTokens={maxLeaderboard} />
          </div>

          <div className="card">
            <div className="card-h">
              <div>
                <h2 className="card-title">Recent activity</h2>
                <div className="card-sub">latest {feed.length} requests</div>
              </div>
              {liveOn && <span className="live-dot">streaming</span>}
            </div>
            <ActivityFeed rows={feed} modelColors={modelColors} newIds={newIdsRef.current} />
          </div>
        </div>
      </div>

      {/* Tweaks */}
      <TweaksPanel>
        <TweakSection label="Theme" />
        <TweakRadio
          label="Style"
          value={t.theme}
          options={['quiet', 'terminal']}
          onChange={(v) => setTweak('theme', v)}
        />
        <TweakSection label="Chart" />
        <TweakRadio
          label="Time-series"
          value={t.chartMode}
          options={['stack', 'area']}
          onChange={(v) => setTweak('chartMode', v)}
        />
        <TweakToggle
          label="Sparklines on tiles"
          value={t.showSparklines}
          onChange={(v) => setTweak('showSparklines', v)}
        />
        <TweakSection label="Live" />
        <TweakToggle
          label="Auto-refresh by default"
          value={t.liveDefault}
          onChange={(v) => setTweak('liveDefault', v)}
        />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
