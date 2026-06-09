# Annotation V2 Import JSONL

Each line is one JSON object for one task item. The importer matches an item by `item_index` first. If `item_index` is absent, it matches by the pair `src_image` and `dst_image`.

Supported fields:

```json
{
  "item_index": 0,
  "src_image": "src/a.jpg",
  "dst_image": "dst/a.jpg",
  "object_labels": {
    "输入图": {
      "菜品种类": "中餐"
    }
  },
  "rough_annotations": [
    {
      "username": "rough_user_1",
      "mos": 5,
      "has_defect": false,
      "primary_issue": "",
      "issues": [],
      "other_issue": "",
      "note": ""
    }
  ],
  "fine": {
    "username": "fine_user_1",
    "mos": 4,
    "has_defect": false
  },
  "sampled": true,
  "sample_bucket": "输入图/菜品种类=中餐",
  "corrected_labels": {
    "输入图": {
      "菜品种类": "融合菜"
    }
  },
  "label_username": "label_user_1"
}
```

Notes:

- `object_labels`, `labels`, and `original_labels` are accepted as original item labels. `object_labels` is the preferred field for external platform imports.
- `rough_annotations` and `fine_annotations` accept multi-annotator records. If there is only one annotator, `rough` or `fine` may be used instead.
- Screening annotations require `username`, `mos`, and `has_defect`. `annotator` and `labeler` are also accepted as username aliases.
- Label objects are sanitized against the system's standard label schema. Non-standard fields are ignored during import.
- `corrected_labels` plus `label_username` imports the label correction stage. A `label` object with `username` and `labels` is also accepted.
- Rows that cannot match any task item are skipped and reported in the import summary.
