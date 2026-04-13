# StackArb

**Supplement price arbitrage tracker** — scrapes live prices from UK supplement retailers and ranks products by daily cost against your target dosages. Auto-updates twice daily via GitHub Actions, hosted as a static site on Netlify.

## How it works

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  GitHub Actions   │────▶│  public/data/     │────▶│  Netlify         │
│  (scraper, 2x/day)│     │  results.json     │     │  (React frontend)│
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

1. **Scraper** (`scraper/run_all.py`) runs at 08:00 and 18:00 UTC via GitHub Actions
2. It scrapes prices from Amazon, iHerb, Shopify stores, eBay, Superdrug, and more
3. Outputs `public/data/results.json`
4. Commits the updated JSON → triggers a Netlify rebuild
5. **Frontend** (React + Vite) deploys to Netlify

### Supplements tracked

| Supplement | Target dose | Products | Retailers |
|---|---|---|---|
| **Omega-3** (EPA) | 3,000 mg/day | 13 + 6 vegan | Shopify, Amazon, iHerb, Superdrug, Bulk |
| **Astaxanthin** | 24 mg/day | 15 | Shopify, Amazon, iHerb, Dolphin Fitness |
| **Lycopene** | 50 mg/day | 7 | Shopify, eBay, iHerb |

## Setup

### 1. Install frontend dependencies

```bash
npm install
```

### 2. Run the scraper (first time)

```bash
pip install -r scraper/requirements.txt
python scraper/run_all.py
```

This populates `public/data/results.json` with real data.

### 3. Local development

```bash
npm run dev    # → http://localhost:5173
```

### 4. Connect Netlify

1. Go to [app.netlify.com](https://app.netlify.com) → **Add new site** → **Import an existing project**
2. Connect your GitHub repo (`s6mzenge/stackarb`)
3. Build settings:
   - **Build command:** `npm run build`
   - **Publish directory:** `dist`
4. Deploy

### 5. Run the scraper manually (first time)

Go to **Actions → Scrape Supplement Prices → Run workflow** in your GitHub repo.

This populates `results.json` and triggers the first Netlify rebuild with real data.

## Repo structure

```
├── .github/workflows/
│   └── scrape.yml              # Twice-daily scraper cron
├── scraper/
│   ├── run_all.py              # Orchestrator — outputs JSON
│   ├── requirements.txt        # Python dependencies
│   ├── omega3_scraper.py       # 13 products
│   ├── vegan_omega3_scraper.py # 6 products
│   ├── astaxanthin_scraper.py  # 15 products
│   └── lycopene_scraper.py     # 7 products
├── src/
│   ├── main.jsx                # React entry
│   └── App.jsx                 # Dashboard component
├── public/
│   └── data/
│       └── results.json        # Auto-generated (committed by scraper)
├── index.html
├── package.json
└── vite.config.js
```

## Scraping strategies

| Strategy | Retailer | Method |
|---|---|---|
| `shopify` | WeightWorld, Supplemented, etc. | Shopify JSON API |
| `amazon` | Amazon UK | HTML scraping |
| `iherb` | iHerb | `cloudscraper` (Cloudflare bypass) |
| `superdrug` | Superdrug | `curl_cffi` (Akamai TLS bypass) |
| `ebay` | eBay UK | JSON-LD + HTML |
| `dolphin` | Dolphin Fitness | JSON-LD + meta tags |
| `magento` | Cytoplan | `window.digitalData` parsing |
| `vegetology` | Vegetology | Craft CMS + Sprig v2 |

## Notes

- GitHub Actions scraper runs twice daily (08:00 and 18:00 UTC)
- Some retailers (Amazon, Superdrug) may block GitHub Actions IPs more aggressively — those products fall back to spreadsheet reference values
- The `KNOWN` dict in each scraper holds fallback values, never used for primary extraction
- Costs: fully free on GitHub Actions + Netlify free tier

## License

MIT
