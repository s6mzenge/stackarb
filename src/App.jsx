import { useState, useEffect } from 'react'

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

function Panel({ data, accent }) {
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
                  <td className="num" style={{ fontWeight: 700, color: hasCost && i === 0 ? 'var(--green)' : undefined }}>
                    {fmtCost(row.prac_cost)}
                  </td>
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

export default function App() {
  const [data, setData] = useState(null)
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
.panel-meta { display: flex; align-items: center; gap: 16px; }
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
@media (max-width: 768px) {
  .header, .tabs { padding-left: 16px; padding-right: 16px; }
  .panel { margin-left: 16px; margin-right: 16px; padding: 16px; }
}
`
