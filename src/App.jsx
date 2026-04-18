import { useState, useEffect, useMemo, useRef } from 'react'

const SUPPLEMENTS = ['omega3', 'astaxanthin', 'lycopene']
const ACCENT = { omega3: '#3b8beb', astaxanthin: '#e85d4a', lycopene: '#d4a843' }
const TAB_LABELS = { omega3: 'Omega-3', astaxanthin: 'Astaxanthin', lycopene: 'Lycopene' }
const TAB_SUB = { omega3: '(EPA)', astaxanthin: '', lycopene: '' }

function fmtPrice(v) { return v != null ? '£' + v.toFixed(2) : '—' }
function fmtCost(v) { return v != null ? '£' + v.toFixed(4) : '—' }
function fmtNum(v) { return v != null ? String(v) : '—' }
function fmtDose(v) { return v != null ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : '—' }

function rankClass(i) {
  if (i === 0) return 'rank-1'
  if (i === 1) return 'rank-2'
  if (i === 2) return 'rank-3'
  return 'rank-n'
}

function SrcBadge({ src }) {
  const cls = 'src-' + src
  const label = src === 'live' ? 'LIVE' : src === 'mixed' ? 'MIX' : 'SHEET'
  return <span className={`src-badge ${cls}`}>{label}</span>
}

/* ── Sparkline helpers ──────────────────────────────────────────── */

function getHistory(history, supplement, brand) {
  if (!history?.snapshots?.length) return []
  return history.snapshots
    .map(s => {
      const cost = s[supplement]?.[brand]
      if (cost == null) return null
      return { date: s.date, cost }
    })
    .filter(Boolean)
}

function Sparkline({ points, color, onClick }) {
  if (!points || points.length < 2) return null
  const w = 64, h = 22, pad = 2
  const costs = points.map(p => p.cost)
  const min = Math.min(...costs)
  const max = Math.max(...costs)
  const range = max - min || 1

  const coords = costs.map((c, i) => {
    const x = pad + (i / (costs.length - 1)) * (w - pad * 2)
    const y = pad + (1 - (c - min) / range) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const trend = costs[costs.length - 1] - costs[0]
  const lineColor = color || (trend <= 0 ? '#3dc97a' : '#e85d4a')

  return (
    <svg
      width={w} height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="sparkline"
      onClick={onClick}
      style={{ cursor: 'pointer' }}
    >
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke={lineColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={coords[coords.length - 1].split(',')[0]}
        cy={coords[coords.length - 1].split(',')[1]}
        r="2"
        fill={lineColor}
      />
    </svg>
  )
}


/* ── Expanded Chart Modal ───────────────────────────────────────── */

function ChartModal({ points, brand, doseLabel, accent, onClose }) {
  const overlayRef = useRef(null)
  const [hover, setHover] = useState(null)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  if (!points || points.length < 2) return null

  const W = 580, H = 280
  const PAD = { top: 28, right: 24, bottom: 48, left: 64 }
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom

  const costs = points.map(p => p.cost)
  const min = Math.min(...costs)
  const max = Math.max(...costs)
  const margin = (max - min) * 0.12 || 0.001
  const yMin = min - margin
  const yMax = max + margin
  const yRange = yMax - yMin

  const coords = points.map((p, i) => ({
    x: PAD.left + (i / (points.length - 1)) * plotW,
    y: PAD.top + (1 - (p.cost - yMin) / yRange) * plotH,
    ...p,
  }))

  const polyline = coords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')

  const areaPath = `M ${coords[0].x.toFixed(1)},${coords[0].y.toFixed(1)} ` +
    coords.slice(1).map(c => `L ${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ') +
    ` L ${coords[coords.length - 1].x.toFixed(1)},${(PAD.top + plotH).toFixed(1)}` +
    ` L ${coords[0].x.toFixed(1)},${(PAD.top + plotH).toFixed(1)} Z`

  const nTicks = 5
  const yTicks = Array.from({ length: nTicks }, (_, i) => {
    const val = yMin + (i / (nTicks - 1)) * yRange
    const y = PAD.top + (1 - (val - yMin) / yRange) * plotH
    return { val, y }
  })

  const nLabels = Math.min(5, points.length)
  const xLabels = Array.from({ length: nLabels }, (_, i) => {
    const idx = Math.round(i * (points.length - 1) / (nLabels - 1))
    const p = coords[idx]
    const d = new Date(p.date)
    const label = `${d.getDate()}/${d.getMonth() + 1}`
    return { x: p.x, label }
  })

  const trend = costs[costs.length - 1] - costs[0]
  const trendPct = ((costs[costs.length - 1] / costs[0] - 1) * 100).toFixed(1)
  const trendColor = trend <= 0 ? '#3dc97a' : '#e85d4a'
  const trendArrow = trend <= 0 ? '↓' : '↑'

  const gradId = `grad-${brand.replace(/\W/g, '')}`

  const handleMouseMove = (e) => {
    const svg = e.currentTarget
    const rect = svg.getBoundingClientRect()
    const mouseX = ((e.clientX - rect.left) / rect.width) * W
    let closest = 0, bestDist = Infinity
    coords.forEach((c, i) => {
      const d = Math.abs(c.x - mouseX)
      if (d < bestDist) { bestDist = d; closest = i }
    })
    setHover(closest)
  }

  return (
    <div
      className="chart-overlay"
      ref={overlayRef}
      onClick={(e) => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="chart-modal">
        <div className="chart-modal-header">
          <div>
            <h3>{brand}</h3>
            <span className="chart-subtitle">Daily cost history ({doseLabel})</span>
          </div>
          <div className="chart-meta-row">
            <span className="chart-trend" style={{ color: trendColor }}>
              {trendArrow} {Math.abs(trendPct)}%
            </span>
            <span className="chart-range">
              {fmtCost(min)} – {fmtCost(max)}
            </span>
            <button className="chart-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <svg
          width="100%"
          viewBox={`0 0 ${W} ${H}`}
          className="chart-svg"
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={accent} stopOpacity="0.18" />
              <stop offset="100%" stopColor={accent} stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {yTicks.map((t, i) => (
            <g key={i}>
              <line x1={PAD.left} y1={t.y} x2={PAD.left + plotW} y2={t.y}
                stroke="#252a35" strokeWidth="0.5" />
              <text x={PAD.left - 8} y={t.y + 4} textAnchor="end"
                fill="#5c6275" fontSize="10" fontFamily="'JetBrains Mono', monospace">
                {'£' + t.val.toFixed(4)}
              </text>
            </g>
          ))}

          {/* X labels */}
          {xLabels.map((l, i) => (
            <text key={i} x={l.x} y={PAD.top + plotH + 20} textAnchor="middle"
              fill="#5c6275" fontSize="10" fontFamily="'JetBrains Mono', monospace">
              {l.label}
            </text>
          ))}

          {/* Area fill */}
          <path d={areaPath} fill={`url(#${gradId})`} />

          {/* Line */}
          <polyline
            points={polyline}
            fill="none"
            stroke={accent}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Hover crosshair & tooltip */}
          {hover != null && coords[hover] && (
            <g>
              <line
                x1={coords[hover].x} y1={PAD.top}
                x2={coords[hover].x} y2={PAD.top + plotH}
                stroke={accent} strokeWidth="1" strokeDasharray="3,3" opacity="0.5"
              />
              <circle cx={coords[hover].x} cy={coords[hover].y}
                r="5" fill={accent} stroke="#14171e" strokeWidth="2" />
              <rect
                x={coords[hover].x - 48} y={coords[hover].y - 32}
                width="96" height="22" rx="4"
                fill="#14171e" stroke={accent} strokeWidth="0.5" opacity="0.92"
              />
              <text
                x={coords[hover].x} y={coords[hover].y - 18}
                textAnchor="middle" fill={accent} fontSize="10"
                fontFamily="'JetBrains Mono', monospace" fontWeight="600"
              >
                {fmtCost(coords[hover].cost)} · {new Date(coords[hover].date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
              </text>
            </g>
          )}

          {/* Last point highlight (when not hovering) */}
          {hover == null && (
            <circle
              cx={coords[coords.length - 1].x}
              cy={coords[coords.length - 1].y}
              r="4" fill={accent} stroke="#14171e" strokeWidth="2"
            />
          )}
        </svg>

        <div className="chart-footer">
          <span>{points.length} data points</span>
          <span>Latest: {fmtCost(costs[costs.length - 1])}/day</span>
        </div>
      </div>
    </div>
  )
}


/* ── Cost cell with sparkline ───────────────────────────────────── */

function CostCell({ cost, history, supplement, brand, accent, doseLabel, isTop }) {
  const [showChart, setShowChart] = useState(false)
  const points = useMemo(() => getHistory(history, supplement, brand), [history, supplement, brand])

  return (
    <>
      <td className="num cost-cell" style={{ fontWeight: 700, color: isTop ? 'var(--green)' : undefined }}>
        <span className="cost-value">{fmtCost(cost)}</span>
        {points.length >= 2 && (
          <Sparkline points={points} color={accent} onClick={() => setShowChart(true)} />
        )}
      </td>
      {showChart && (
        <ChartModal
          points={points}
          brand={brand}
          doseLabel={doseLabel}
          accent={accent}
          onClose={() => setShowChart(false)}
        />
      )}
    </>
  )
}


/* ── All-Brands Chart (inline in each panel) ───────────────────── */

const CHART_COLORS = [
  '#3dc97a', '#3b8beb', '#e85d4a', '#d4a843', '#a87cf7',
  '#4dd4c0', '#eb6b9d', '#7aade0', '#f0a050', '#8dd47a',
  '#c97adb', '#5cc4e8', '#e0c45a', '#7a8cf7', '#e87a6a',
]

function AllBrandsChart({ history, supplement, accent }) {
  const [highlighted, setHighlighted] = useState(null)
  const [hover, setHover] = useState(null)

  const { brands, series, dates } = useMemo(() => {
    if (!history?.snapshots?.length) return { brands: [], series: {}, dates: [] }

    const brandSet = new Set()
    for (const snap of history.snapshots) {
      const costs = snap[supplement]
      if (costs) Object.keys(costs).forEach(b => brandSet.add(b))
    }

    const ser = {}
    const allDates = history.snapshots.map(s => s.date)
    for (const brand of brandSet) {
      const vals = history.snapshots.map(s => s[supplement]?.[brand] ?? null)
      if (vals.filter(v => v != null).length >= 2) ser[brand] = vals
    }

    const sorted = Object.keys(ser).sort((a, b) => {
      const aLast = [...ser[a]].reverse().find(v => v != null) ?? Infinity
      const bLast = [...ser[b]].reverse().find(v => v != null) ?? Infinity
      return aLast - bLast
    })

    return { brands: sorted, series: ser, dates: allDates }
  }, [history, supplement])

  if (brands.length === 0) return null

  const colorFor = (i) => CHART_COLORS[i % CHART_COLORS.length]

  const W = 660, H = 260
  const PAD = { top: 16, right: 16, bottom: 36, left: 60 }
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom

  const allVals = brands.flatMap(b => series[b].filter(v => v != null))
  const dataMin = Math.min(...allVals)
  const dataMax = Math.max(...allVals)
  const margin = (dataMax - dataMin) * 0.08 || 0.01
  const yMin = Math.max(0, dataMin - margin)
  const yMax = dataMax + margin
  const yRange = yMax - yMin

  const toX = (i) => PAD.left + (i / (dates.length - 1)) * plotW
  const toY = (v) => PAD.top + (1 - (v - yMin) / yRange) * plotH

  const polylines = brands.map((brand, bi) => {
    const vals = series[brand]
    const segments = []
    let current = []
    for (let i = 0; i < vals.length; i++) {
      if (vals[i] != null) {
        current.push({ x: toX(i), y: toY(vals[i]), idx: i, cost: vals[i] })
      } else if (current.length > 0) {
        segments.push(current)
        current = []
      }
    }
    if (current.length > 0) segments.push(current)
    return { brand, color: colorFor(bi), segments }
  })

  const nTicks = 5
  const yTicks = Array.from({ length: nTicks }, (_, i) => {
    const val = yMin + (i / (nTicks - 1)) * yRange
    return { val, y: toY(val) }
  })

  const nLabels = Math.min(6, dates.length)
  const xLabels = nLabels < 2 ? [] : Array.from({ length: nLabels }, (_, i) => {
    const idx = Math.round(i * (dates.length - 1) / (nLabels - 1))
    const d = new Date(dates[idx])
    return { x: toX(idx), label: `${d.getDate()}/${d.getMonth() + 1}` }
  })

  const handleMouseMove = (e) => {
    const svg = e.currentTarget
    const rect = svg.getBoundingClientRect()
    const mouseX = ((e.clientX - rect.left) / rect.width) * W
    const snapIdx = Math.round(((mouseX - PAD.left) / plotW) * (dates.length - 1))
    setHover(Math.max(0, Math.min(dates.length - 1, snapIdx)))
  }

  const tooltipData = hover != null ? brands
    .map((b, i) => ({ brand: b, cost: series[b][hover], color: colorFor(i) }))
    .filter(d => d.cost != null)
    .sort((a, b) => a.cost - b.cost)
    .slice(0, 8) : []

  const tooltipXPct = hover != null ? (toX(hover) / W) * 100 : 0
  const tooltipFlip = tooltipXPct > 65

  return (
    <div className="ab-chart">
      <div className="ab-chart-label">Price history — all products</div>
      <div className="ab-chart-body">
        <svg
          width="100%"
          viewBox={`0 0 ${W} ${H}`}
          className="ab-svg"
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHover(null)}
        >
          {yTicks.map((t, i) => (
            <g key={i}>
              <line x1={PAD.left} y1={t.y} x2={PAD.left + plotW} y2={t.y}
                stroke="var(--border)" strokeWidth="0.5" />
              <text x={PAD.left - 8} y={t.y + 3.5} textAnchor="end"
                fill="var(--text3)" fontSize="9" fontFamily="'JetBrains Mono', monospace">
                {'£' + t.val.toFixed(2)}
              </text>
            </g>
          ))}
          {xLabels.map((l, i) => (
            <text key={i} x={l.x} y={PAD.top + plotH + 18} textAnchor="middle"
              fill="var(--text3)" fontSize="9" fontFamily="'JetBrains Mono', monospace">
              {l.label}
            </text>
          ))}

          {polylines.map(({ brand, color, segments }) => {
            const isHigh = highlighted === brand
            const isFaded = highlighted != null && !isHigh
            return segments.map((pts, si) => (
              <polyline
                key={`${brand}-${si}`}
                points={pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')}
                fill="none"
                stroke={color}
                strokeWidth={isHigh ? 2.5 : 1.2}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={isFaded ? 0.07 : isHigh ? 1 : 0.5}
                style={{ transition: 'opacity 0.15s, stroke-width 0.15s' }}
              />
            ))
          })}

          {hover != null && (
            <line
              x1={toX(hover)} y1={PAD.top}
              x2={toX(hover)} y2={PAD.top + plotH}
              stroke="var(--text3)" strokeWidth="0.5" strokeDasharray="3,3"
            />
          )}
        </svg>

        {hover != null && tooltipData.length > 0 && (
          <div className="ab-tooltip" style={{
            left: tooltipFlip ? undefined : `${tooltipXPct}%`,
            right: tooltipFlip ? `${100 - tooltipXPct}%` : undefined,
            transform: tooltipFlip ? 'translateX(8px)' : 'translateX(-50%)',
          }}>
            <div className="ab-tooltip-date">
              {new Date(dates[hover]).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
            </div>
            {tooltipData.map(d => (
              <div key={d.brand} className="ab-tooltip-row">
                <span className="ab-tooltip-sw" style={{ background: d.color }} />
                <span className="ab-tooltip-brand">{d.brand}</span>
                <span className="ab-tooltip-cost">{fmtCost(d.cost)}</span>
              </div>
            ))}
            {brands.filter(b => series[b][hover] != null).length > 8 && (
              <div className="ab-tooltip-more">
                +{brands.filter(b => series[b][hover] != null).length - 8} more
              </div>
            )}
          </div>
        )}
      </div>

      <div className="ab-legend">
        {brands.map((brand, i) => {
          const lastCost = [...series[brand]].reverse().find(v => v != null)
          return (
            <button
              key={brand}
              className={`ab-legend-item ${highlighted === brand ? 'ab-active' : ''}`}
              onMouseEnter={() => setHighlighted(brand)}
              onMouseLeave={() => setHighlighted(null)}
            >
              <span className="ab-legend-sw" style={{ background: colorFor(i) }} />
              <span className="ab-legend-name">{brand}</span>
              {lastCost != null && (
                <span className="ab-legend-cost">{fmtCost(lastCost)}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}


/* ── Main Panel ─────────────────────────────────────────────────── */

function Panel({ data, accent, history, supplement }) {
  if (!data || !data.results) return <p className="loading">Loading…</p>

  const r = data.results
  const best = r.length > 0 && r[0].prac_cost != null ? r[0] : null
  const liveCount = r.filter(x => x.data_source === 'live').length

  return (
    <>
      <div className="panel-header">
        <div className="panel-meta">
          <span className="badge">Target: {data.target_dose}mg {data.dose_label}/day</span>
          <span className="badge">{liveCount}/{r.length} live</span>
          {history?.snapshots?.length > 0 && (
            <span className="badge">{history.snapshots.length} price snapshots</span>
          )}
        </div>
      </div>

      <div className="summary-row">
        <div className="summary-card">
          <div className="label">Best daily cost</div>
          <div className="value green">{best ? fmtCost(best.prac_cost) : '—'}</div>
        </div>
        <div className="summary-card">
          <div className="label">Best brand</div>
          <div className="value small">{best ? best.brand : '—'}</div>
        </div>
        <div className="summary-card">
          <div className="label">Caps/day (best)</div>
          <div className="value">{best ? best.prac_caps : '—'}</div>
        </div>
        <div className="summary-card">
          <div className="label">Products tracked</div>
          <div className="value">{r.length}</div>
        </div>
      </div>

      <AllBrandsChart history={history} supplement={supplement} accent={accent} />

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 40 }}>#</th>
              <th>Brand</th>
              <th className="num">Price</th>
              <th className="num">Caps</th>
              <th className="num">{data.dose_label}/cap</th>
              <th className="num">Caps/day</th>
              <th className="num">Cost/day</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {r.map((row, i) => {
              const hasCost = row.prac_cost != null
              return (
                <tr key={row.brand + i}>
                  <td>
                    <span className={`rank-badge ${hasCost ? rankClass(i) : 'rank-n'}`}>
                      {hasCost ? i + 1 : '—'}
                    </span>
                  </td>
                  <td className="brand-cell">
                    <a href={row.url} target="_blank" rel="noopener noreferrer">{row.brand}</a>
                    {row.vegan && <span className="vegan-badge">VEGAN</span>}
                  </td>
                  <td className={`num${row.price_changed ? ' price-changed' : ''}`}>{fmtPrice(row.final_price)}</td>
                  <td className="num">{fmtNum(row.final_amount)}</td>
                  <td className="num">{fmtDose(row.final_dosage)}</td>
                  <td className="num">{fmtNum(row.prac_caps)}</td>
                  <CostCell
                    cost={row.prac_cost}
                    history={history}
                    supplement={supplement}
                    brand={row.brand}
                    accent={accent}
                    doseLabel={data.dose_label}
                    isTop={hasCost && i === 0}
                  />
                  <td><SrcBadge src={row.data_source} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}


/* ── App ────────────────────────────────────────────────────────── */

export default function App() {
  const [data, setData] = useState(null)
  const [history, setHistory] = useState(null)
  const [tab, setTab] = useState('omega3')
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/data/results.json')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message))

    fetch('/data/history.json')
      .then(r => r.ok ? r.json() : null)
      .then(h => { if (h) setHistory(h) })
      .catch(() => {})
  }, [])

  const scrapedAt = data?.scraped_at
    ? new Date(data.scraped_at).toLocaleString()
    : null

  return (
    <>
      <style>{CSS}</style>
      <div className="header">
        <h1>StackArb <span>/ supplement price tracker</span></h1>
        {scrapedAt && <span className="updated">Last updated: {scrapedAt}</span>}
      </div>

      <div className="tabs">
        {SUPPLEMENTS.map(key => (
          <button
            key={key}
            className={`tab ${tab === key ? 'active' : ''}`}
            data-tab={key}
            onClick={() => setTab(key)}
          >
            <span className="dot" style={{ background: ACCENT[key] }} />
            {TAB_LABELS[key]} {TAB_SUB[key] && <small>{TAB_SUB[key]}</small>}
          </button>
        ))}
      </div>

      <div className="panel">
        {error ? (
          <p className="loading">
            No data yet — run the scraper first.<br />
            <code>python scraper/run_all.py</code><br />
            <small style={{ color: 'var(--text3)' }}>({error})</small>
          </p>
        ) : (
          <Panel
            data={data?.supplements?.[tab]}
            accent={ACCENT[tab]}
            history={history}
            supplement={tab}
          />
        )}
      </div>
    </>
  )
}

const CSS = `
:root {
  --bg: #0c0e12;
  --surface: #14171e;
  --surface2: #1a1e27;
  --border: #252a35;
  --border-hi: #353b4a;
  --text: #e2e5eb;
  --text2: #8b91a0;
  --text3: #5c6275;
  --green: #3dc97a;
  --red: #e85d4a;
  --yellow: #d4a843;
  --font: 'DM Sans', -apple-system, sans-serif;
  --mono: 'JetBrains Mono', 'Consolas', monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}
.header {
  padding: 32px 40px 0;
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 16px;
}
.header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.header h1 span { color: var(--text3); font-weight: 400; }
.updated {
  font-family: var(--mono); font-size: 11px; color: var(--text3);
  background: var(--surface2); border: 1px solid var(--border);
  padding: 4px 10px; border-radius: 6px;
}
.tabs { display: flex; gap: 4px; padding: 24px 40px 0; }
.tab {
  font-family: var(--font); font-size: 13px; font-weight: 600;
  padding: 10px 20px; border: 1px solid var(--border); border-bottom: none;
  border-radius: 10px 10px 0 0; background: transparent; color: var(--text2);
  cursor: pointer; transition: all 0.15s; position: relative;
}
.tab:hover { color: var(--text); background: var(--surface); }
.tab.active { background: var(--surface); color: var(--text); border-color: var(--border-hi); }
.tab.active::after {
  content: ''; position: absolute; bottom: -1px; left: 0; right: 0; height: 2px;
}
.tab[data-tab="omega3"].active::after { background: #3b8beb; }
.tab[data-tab="astaxanthin"].active::after { background: #e85d4a; }
.tab[data-tab="lycopene"].active::after { background: #d4a843; }
.tab small { color: var(--text3); }
.dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  margin-right: 6px; vertical-align: middle;
}
.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 0 12px 12px 12px; margin: 0 40px 40px; padding: 28px;
  animation: fadeIn 0.2s;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.panel-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 20px; flex-wrap: wrap; gap: 12px;
}
.panel-meta { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.badge {
  font-family: var(--mono); font-size: 11px; font-weight: 500;
  padding: 4px 10px; border-radius: 6px;
  background: var(--surface2); border: 1px solid var(--border); color: var(--text2);
}
.summary-row { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.summary-card {
  flex: 1; min-width: 140px; padding: 14px 18px;
  background: var(--surface2); border: 1px solid var(--border); border-radius: 10px;
}
.summary-card .label {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--text3); margin-bottom: 4px;
}
.summary-card .value { font-family: var(--mono); font-size: 18px; font-weight: 700; }
.summary-card .value.green { color: var(--green); }
.summary-card .value.small { font-size: 15px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 14px; font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.5px; color: var(--text3);
  border-bottom: 1px solid var(--border); white-space: nowrap;
  position: sticky; top: 0; background: var(--surface);
}
thead th.num { text-align: right; }
tbody td {
  padding: 12px 14px; border-bottom: 1px solid var(--border);
  white-space: nowrap; vertical-align: middle;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: rgba(255,255,255,0.02); }
td.num { text-align: right; font-family: var(--mono); font-size: 12px; font-weight: 500; }
td.brand-cell {
  font-weight: 600; max-width: 280px; overflow: hidden; text-overflow: ellipsis;
}
td.brand-cell a {
  color: var(--text); text-decoration: none;
  border-bottom: 1px dashed var(--border-hi); transition: border-color 0.15s;
}
td.brand-cell a:hover { border-color: var(--text2); }
.rank-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: 7px;
  font-family: var(--mono); font-size: 11px; font-weight: 700;
}
.rank-1 { background: rgba(61,201,122,0.12); color: var(--green); }
.rank-2 { background: rgba(61,201,122,0.06); color: rgba(61,201,122,0.7); }
.rank-3 { background: rgba(212,168,67,0.08); color: var(--yellow); }
.rank-n { background: var(--surface2); color: var(--text3); }
.src-badge {
  font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 4px;
  text-transform: uppercase; letter-spacing: 0.3px;
}
.src-live { background: rgba(61,201,122,0.1); color: var(--green); }
.src-mixed { background: rgba(212,168,67,0.1); color: var(--yellow); }
.src-spreadsheet { background: var(--surface2); color: var(--text3); }
.vegan-badge {
  font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
  background: rgba(61,201,122,0.12); color: var(--green);
  letter-spacing: 0.5px; margin-left: 6px; vertical-align: middle;
}
.price-changed { color: var(--yellow); }
.loading { color: var(--text3); padding: 40px; text-align: center; line-height: 2; }
.loading code {
  font-family: var(--mono); background: var(--surface2);
  padding: 2px 8px; border-radius: 4px; font-size: 12px;
}

/* ── Sparkline & Cost cell ──────────────────────────────────── */
.cost-cell {
  display: flex !important;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.cost-value { order: 2; }
.sparkline {
  order: 1;
  opacity: 0.65;
  transition: opacity 0.15s, transform 0.15s;
  flex-shrink: 0;
}
.sparkline:hover {
  opacity: 1;
  transform: scaleY(1.25);
}

/* ── Chart Modal ────────────────────────────────────────────── */
.chart-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,0.65);
  backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center;
  animation: fadeIn 0.15s;
}
.chart-modal {
  background: var(--surface);
  border: 1px solid var(--border-hi);
  border-radius: 16px;
  padding: 24px 28px 20px;
  width: min(640px, calc(100vw - 32px));
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  animation: modalSlide 0.2s ease-out;
  box-shadow: 0 24px 80px rgba(0,0,0,0.5);
}
@keyframes modalSlide {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.chart-modal-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 16px; gap: 12px; flex-wrap: wrap;
}
.chart-modal-header h3 {
  font-size: 16px; font-weight: 700; letter-spacing: -0.3px;
}
.chart-subtitle {
  display: block; font-size: 11px; color: var(--text3); margin-top: 2px;
  font-family: var(--mono);
}
.chart-meta-row {
  display: flex; align-items: center; gap: 12px;
}
.chart-trend {
  font-family: var(--mono); font-size: 13px; font-weight: 700;
}
.chart-range {
  font-family: var(--mono); font-size: 11px; color: var(--text3);
}
.chart-close {
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text2); cursor: pointer;
  width: 28px; height: 28px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; transition: all 0.15s;
}
.chart-close:hover { background: var(--border); color: var(--text); }
.chart-svg { display: block; margin: 0 -4px; }
.chart-footer {
  display: flex; justify-content: space-between;
  font-family: var(--mono); font-size: 10px; color: var(--text3);
  margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);
}

/* ── All-Brands Chart ───────────────────────────────────────── */
.ab-chart {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px 14px;
  margin-bottom: 20px;
}
.ab-chart-label {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--text3); margin-bottom: 12px;
}
.ab-chart-body { position: relative; }
.ab-svg { display: block; }
.ab-tooltip {
  position: absolute; top: 4px;
  background: var(--surface); border: 1px solid var(--border-hi);
  border-radius: 8px; padding: 8px 12px;
  font-size: 11px; pointer-events: none;
  z-index: 10; min-width: 190px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.45);
}
.ab-tooltip-date {
  font-family: var(--mono); font-size: 9px; color: var(--text3);
  margin-bottom: 5px; padding-bottom: 5px; border-bottom: 1px solid var(--border);
}
.ab-tooltip-row {
  display: flex; align-items: center; gap: 6px;
  padding: 1.5px 0; font-family: var(--mono);
}
.ab-tooltip-sw {
  width: 7px; height: 7px; border-radius: 2px; flex-shrink: 0;
}
.ab-tooltip-brand {
  flex: 1; font-size: 10px; color: var(--text2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 130px;
}
.ab-tooltip-cost {
  font-size: 10px; font-weight: 600; color: var(--text);
}
.ab-tooltip-more {
  font-size: 9px; color: var(--text3); margin-top: 3px; font-style: italic;
}
.ab-legend {
  display: flex; flex-wrap: wrap; gap: 2px 4px; margin-top: 12px;
  padding-top: 12px; border-top: 1px solid var(--border);
}
.ab-legend-item {
  display: flex; align-items: center; gap: 5px;
  background: transparent; border: 1px solid transparent;
  padding: 3px 7px; border-radius: 5px;
  cursor: pointer; transition: all 0.15s;
  font-family: var(--font); font-size: 10px; color: var(--text2);
}
.ab-legend-item:hover, .ab-legend-item.ab-active {
  background: rgba(255,255,255,0.04); border-color: var(--border);
  color: var(--text);
}
.ab-legend-sw {
  width: 10px; height: 3px; border-radius: 1px; flex-shrink: 0;
}
.ab-legend-name {
  max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ab-legend-cost {
  font-family: var(--mono); font-size: 9px; color: var(--text3);
}

@media (max-width: 768px) {
  .header, .tabs { padding-left: 16px; padding-right: 16px; }
  .panel { margin-left: 16px; margin-right: 16px; padding: 16px; }
  .chart-modal { padding: 16px; }
  .sparkline { width: 48px; }
  .ab-chart { padding: 14px 12px 10px; }
  .ab-tooltip { min-width: 150px; }
}
`
