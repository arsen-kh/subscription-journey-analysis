"""Aggregate pseudonymized device registrations and subscription requests; no raw input."""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def aggregate(rows, kind):
    groups = defaultdict(lambda: {'rows': 0, 'panel_users': set(), 'users': set()})
    for r in rows:
        app = r.get('app') or 'Missing'
        platform = (r.get('platform') or 'Other') if kind == 'devices' else 'All'
        group = groups[(app, platform)]
        group['rows'] += 1
        if r.get('panel_user'): group['panel_users'].add(r['panel_user'])
        if r.get('user'): group['users'].add(r['user'])
    apps = [{'app': a, 'platform': p, 'rows': g['rows'], 'panel_users': len(g['panel_users']), 'users': len(g['users'])} for (a,p),g in sorted(groups.items())]
    other = sum(r['rows'] for r in apps if r['app'] == 'Other')
    return {'rows': len(rows), 'other_rows': other, 'other_pct': round(100*other/len(rows), 2) if rows else 0, 'missing_rows': sum(r['rows'] for r in apps if r['app'] == 'Missing'), 'apps': apps}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir', required=True, type=Path)
    ap.add_argument('--output-dir', required=True, type=Path)
    ap.add_argument('--snapshot-date', type=date.fromisoformat, default=date(2026,11,7), help='End of inclusive 31-day calendar window, YYYY-MM-DD (default 2026-11-07)')
    args = ap.parse_args()
    end = datetime.combine(args.snapshot_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    start = end - timedelta(days=31)
    result = {'window': {'start_inclusive': start.isoformat(), 'end_exclusive': end.isoformat(), 'note': 'Calendar filter; naive source timestamps treated as UTC for this calculation. This is not a confirmed exact snapshot time.'}, 'tables': {}}
    for kind, name, field in [('devices','devices.csv','created_at'), ('requests','subscription_requests.csv','request_at')]:
        rows = read(args.data_dir/name)
        recent = [r for r in rows if (t := timestamp(r.get(field))) is not None and start <= t < end]
        result['tables'][kind] = {'all': aggregate(rows, kind), '30d': aggregate(recent, kind)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'app_usage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()

