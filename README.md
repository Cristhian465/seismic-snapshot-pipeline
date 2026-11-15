# Seismic Snapshot

A batch data engineering project by Cristhian Carlos Mamani Cori. It converts the USGS magnitude 2.5+ weekly GeoJSON feed into a SQLite event table, UTC daily aggregates, and a public static report on GitHub Pages.

[Public report](https://cristhian465.github.io/seismic-snapshot-pipeline/) · [Build and deployment history](https://github.com/Cristhian465/seismic-snapshot-pipeline/actions) · [USGS feed format](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)

## Run locally

Use Python 3.9 or newer. The project uses the standard library.

```bash
python3 pipeline.py
python3 -m http.server 8000 --directory site
```

Open http://localhost:8000. The pipeline writes HTML and two CSV files to `site/`. The original GeoJSON and the SQLite database go to `evidence/`. To replay an archived snapshot, run `python3 pipeline.py --input evidence/usgs.geojson`.

## Data and deployment

The data source is the [USGS magnitude 2.5+ weekly feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson). The pipeline retains records classified as earthquakes and uses UTC for event times and daily grouping. It checks the feed count, timestamps, geometry, coordinates and numerical values, rejects duplicate IDs in SQLite, then reconciles daily totals with the number of loaded events. The weekly snapshot can include partial days and revised estimates.

In Settings → Pages, select GitHub Actions as the publishing source. The workflow runs on pushes to main, manual dispatch and every six hours. The build uploads the public files to a Pages artifact and retains the raw input and SQLite database separately for seven days. Deployment depends on a successful build. The report displays the source generation time.

The project uses GitHub-hosted infrastructure and does not require AWS or API credentials.
