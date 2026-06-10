# Annotations V3 Release Checklist

## Automated Commands

```bash
pytest tests/test_annotations_v3_dataset_foundation.py -q
pytest tests/test_annotations_v3_assignments.py -q
pytest tests/test_annotations_v3_records.py -q
pytest tests/test_annotations_v3_assets.py -q
pytest tests/test_annotations_v3_sampling_visualization.py -q
pytest tests/test_annotations_v3_imports.py -q
pytest tests/test_annotations_v3_migration_export_admin.py -q
pytest tests/test_annotations_v3_transactions.py -q
pytest tests/test_annotations_v3_payload_size.py -q
pytest tests/test_annotations_v3_performance.py -q
pytest tests/test_annotations_app.py tests/test_annotations_v2_app.py -q
```

## Manual Checks

- Create dataset with `natural` order.
- Create dataset with `shuffled` order and fixed seed.
- Claim rough as two users.
- Save a rough record.
- Generate image assets.
- Open rate page on desktop and mobile.
- Run sample.
- Open visualization.
- Dry-run import.
- Commit import.
- Export JSONL.
- Migrate a v2 fixture.

## Browser Layout

- Desktop `1440x900`: image pair is visible, controls do not overlap, progress and save controls fit, console has no uncaught errors.
- Mobile `390x844`: images stack above fields, buttons stay inside containers, labels wrap cleanly, save controls remain reachable.
- Preview retention: after moving through 45 items, retained preview URL references should not exceed 40.
