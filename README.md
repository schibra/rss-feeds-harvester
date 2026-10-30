# rss-feeds-harvester

Daily GitHub Action (about 06:03 Copenhagen) that fetches the public RSS/Atom feeds in `feeds.json` and writes `data/news.json` (title, teaser, link, date; 7 days kept), `data/newswire.csv` (last 24 h headlines, via `front.py wire`), plus MET Norway forecast CSVs. `front.py front picks.json out.csv` builds the dashboard front page from hand-picked links. Feeds the Garmin Coach dashboard.
