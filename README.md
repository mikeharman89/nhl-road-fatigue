# NHL Road Fatigue Index

A data pipeline and interactive dashboard for measuring how NHL players hold up deep in road trips — and what it means for team performance over a full season.

Built using the NHL's public API. No API keys, no paid data sources.

---

## What this is

Standard hockey stats tell you what happened across a season. This project asks a more specific question: **does a player hold up as a road trip gets longer?**

The core metric is the **Road Trip Decay Index** — the difference in a player's points per game between game 1 of a road trip and game 3 and beyond, when travel fatigue, hotel beds, and consecutive nights of play start to compound. A score of 0.00 means no drop-off. Negative means fading. Positive means the player actually gets better deep in trips.

We apply this across all 32 NHL teams, every skater with sufficient data, and layer in back-to-back performance splits, home/road context, monthly scoring arcs, and full season travel burden.

---

## What's in this repo

```
├── pipeline.py              # Data pipeline — pulls all 32 teams from the NHL API
├── dashboard_v12.html       # Interactive league-wide dashboard
├── requirements.txt         # Python dependencies
├── src/
│   ├── nhl_client.py        # NHL API wrapper (schedules, rosters, game logs)
│   ├── schedule_analysis.py # Travel burden calculation (miles, back-to-backs, road trips)
│   └── fatigue_model.py     # Physical Output Index and fatigue scoring
└── README.md
```

---

## Getting started

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate       # Mac/Linux
# venv\Scripts\activate        # Windows

pip install -r requirements.txt
```

### 2. Run the pipeline

```bash
python pipeline.py
```

This pulls schedule, roster, and game log data for all 32 NHL teams and writes `nhl_fatigue_all_teams.json`. Runtime is approximately 10–15 minutes due to API rate limiting.

To run a single team for testing:

```bash
python pipeline.py --teams UTA
```

To run a specific set of teams:

```bash
python pipeline.py --teams UTA COL DAL VGK SEA
```

The pipeline saves a checkpoint every 8 teams, so if something fails mid-run you won't lose all your data.

### 3. Open the dashboard

The dashboard reads `nhl_fatigue_all_teams.json` via `fetch()`, which requires a local web server — you can't just open the HTML file directly.

```bash
python -m http.server 8000
```

Then open **http://localhost:8000/dashboard_v12.html** in your browser.

---

## Dashboard walkthrough

### League Overview

The home page shows all 32 teams in a sortable table. Sort by average road decay, total travel miles, back-to-backs, or individual player extremes. Click any row to jump to that team's full breakdown.

Each team shows:
- **Avg decay** — team-wide Road Trip Decay Index average
- **Mini bar** — visual decay severity at a glance
- **Most durable** — the player with the best (least negative) decay index
- **Most affected** — the player who fades most on long trips
- **Travel miles** — total season travel distance
- **B2Bs** — number of back-to-back games

### Team pages

Clicking a team opens four tabs:

**Overview**
The top three most durable and most fatigued players on the roster, each as a clickable card. Below that, a spark bar chart showing every tracked skater's decay index at a glance. Click any player to open their full detail panel — splits, back-to-back impact, and a monthly scoring arc chart.

**Roster Table**
Every tracked skater with full splits, sortable by any column. Key columns:

| Column | What it means |
|--------|--------------|
| Pts/G | Season points per game |
| Home | Points per game in home games |
| Road G1 | Points per game in the first game of any road trip |
| Road G3+ | Points per game in road game 3 and beyond |
| Decay | Road Trip Decay Index (G3+ minus G1) |
| B2B | Points per game on back-to-back games |
| Rested | Points per game with 2+ days rest |
| B2B Δ | Back-to-back drop (B2B minus rested) |
| TOI/G | Average time on ice per game |

Click any row to jump to that player's detail panel in the Overview tab.

**Charts**
Three interactive charts:
- **Road trip decay** — horizontal bar chart for all players, color-coded green/amber/red
- **Back-to-back vs. rested** — grouped bar comparing output on short rest vs. rested
- **Home vs. road scoring** — three-way split showing home, road game 1, and road game 3+ for top players by TOI

Hover any bar for exact values.

**Travel Load**
Season travel statistics plus an interactive road trip map. Each road trip is drawn as a colored arc on a dark map tile background. Click any arc or the legend buttons to isolate a specific trip. Hover arcs for city-to-city distances.

### Glossary

Accessible from the sidebar — explains every metric in plain language, including the decay index formula, how to read the scale, and a full limitations section.

---

## How the decay index is calculated

```
Decay Index = pts/gm (Road Game 3+) − pts/gm (Road Game 1)
```

- **Road Game 1**: the first game of any road trip, used as the "fresh road" baseline
- **Road Game 3+**: game three and beyond of the same trip, where cumulative fatigue compounds
- Minimum 3 qualifying deep road games required for inclusion
- Season-level metric — built from game log data via the NHL public API

---

## Data sources

All data is pulled from the NHL's public API — no authentication required:

- **`https://api-web.nhle.com/v1`** — rosters, schedules, game logs, player stats
- **`https://api.nhle.com/stats/rest/en`** — season-level skater summary stats

The pipeline stores schedule data per team for the travel map, player game logs for decay and back-to-back calculations, and season travel summaries (total miles, back-to-back counts, longest road trip).

---

## Limitations

- Points are a noisy single-game metric. Small samples (3–6 deep road games per phase) add variance — individual player numbers should be interpreted with appropriate skepticism.
- The decay index doesn't account for opponent quality, injuries mid-trip, or line combination changes.
- NHL EDGE tracking data — skating speed, acceleration events, distance per shift — would dramatically strengthen the physical load signal. That data remains inaccessible via the public API. When it becomes available, the framework here is ready for it.

---

## Season coverage

Currently built for the **2025–26 NHL regular season**. To pull a different season, update the `SEASON` variable in `pipeline.py`:

```python
SEASON = "20252026"  # change to e.g. "20242025" for last season
```

---

## Requirements

- Python 3.10+
- See `requirements.txt` for full dependency list
- Internet connection required for the pipeline and for map tiles in the dashboard
