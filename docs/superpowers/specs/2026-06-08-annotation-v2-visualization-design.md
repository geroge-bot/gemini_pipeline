# Annotation V2 Stage Visualization Design

## Goal

Add result visualization pages for every `annotation_v2` workflow stage, using the first-generation annotation site's image-pair result browsing as the visual reference while keeping the implementation native to `web/annotations_v2`.

The covered stages are:

- Rough screening
- Fine screening
- Sampling
- Label correction

## Context

The existing v2 site stores task items in `items.json` and stage records in `records.json`. Stage state is already separated into `rough_annotations`, aggregated `rough`, `fine_annotations`, aggregated `fine`, `sampled`, `sample_bucket`, and `label`.

The first-generation annotation site has richer result, statistics, issue, and visualization views. This v2 work should borrow the result visualization pattern: one image pair per page, navigation controls, path display, and a read-only result panel. It should not import the older QC, issue, filter drawer, Excel export, or statistics systems.

## Options Considered

### Option A: Port the first-generation result pages

This would copy the old `resultsView`, `visualizationView`, and supporting API logic into v2. It gives many features immediately, but it brings old assumptions about single final annotations, QC history, prompt extraction, and filter options. That would increase the chance of mismatched v2 behavior.

### Option B: Build one v2-native visualization page

This creates a single route, `/dataset/visualize/<task_id>?stage=...`, backed by one API that returns page-sized read-only rows for a selected stage. The page changes the side panel based on `rough`, `fine`, `sample`, or `label`. This is the recommended approach because it fits v2's stage model and keeps the code small.

### Option C: Embed visualization panels into each existing workflow page

This would add read-only result sections inside the rough/fine/label rating page and sample panel. It keeps routes minimal, but it makes the active workbench mix editing and review state. It is weaker for browsing completed output.

## Chosen Design

Use Option B: a unified v2-native visualization page.

Add a Flask route:

```text
GET /dataset/visualize/<task_id>?stage=rough|fine|sample|label
```

Add an API route:

```text
GET /api/tasks/<task_id>/visualization-results?stage=<stage>&page=<zero_based_page>&limit=1
```

The API returns:

```json
{
  "stage": "rough",
  "total": 12,
  "page": 0,
  "limit": 1,
  "results": []
}
```

The task card gets one visualization entry per stage:

- `粗筛结果`
- `精筛结果`
- `采样结果`
- `标签结果`

These links sit near the existing progress cells and open the matching stage visualization.

## Stage Semantics

### Rough Screening

Rows include all task items, because rough screening is the first stage. The side panel shows:

- Aggregated rough result, if present
- All rough annotations, with annotator, MOS, defect flag, issues, and update time
- Pass or fail according to the task's rough threshold and defect rule

### Fine Screening

Rows include only items that completed and passed rough screening. The side panel shows:

- Aggregated fine result, if present
- All fine annotations
- Pass or fail according to the task's fine threshold and defect rule
- A compact rough summary for context

### Sampling

Rows include only items that completed and passed fine screening. The side panel shows:

- Whether the item was sampled
- The sample bucket
- Rough and fine aggregate summaries

### Label Correction

Rows include sampled items. The side panel shows:

- Original labels
- Corrected labels, if saved
- Label correction username and update time
- Sample bucket

## Backend Data Flow

Add `AnnotationV2Store.get_visualization_results(task_id, stage, offset, limit)`.

The method reads the task, items, and records once. It filters items by stage rules, builds image URLs using the existing image endpoint, and returns read-only row payloads. It should use existing helpers such as `_stage_complete`, `_rough_passes`, `_fine_passes`, and `_item_payload` where appropriate.

The payload should include enough data for the frontend to render without extra requests:

- `item_index`
- `src_image`
- `dst_image`
- `src_relative_path`
- `dst_relative_path`
- `image_urls`
- `original_labels`
- `record`
- `stage_result`
- `stage_annotations`
- `stage_passed`
- `sampled`
- `sample_bucket`
- `corrected_labels`

## Frontend Design

Create `web/annotations_v2/templates/visualize.html`.

The layout follows the first-generation visualization page:

- Sticky topbar with return link and session line
- Work header with title, current page, previous/next buttons, jump input, and stage selector links
- Two image figures for source and target images
- Right side read-only result panel

The same `app.js` initializes the page by reading:

- `document.body.dataset.page === "visualize"`
- `document.body.dataset.taskId`
- URL query parameter `stage`

Add state fields for visualization stage, results, page, and total. Add render helpers for nested labels, screen annotations, badges, and timestamps.

## Error Handling

Unknown stages return `400` with `未知可视化阶段`.

Missing tasks continue to use the existing `KeyError` handler and return `404`.

Empty result sets render a readable empty state and clear images/result panels.

Image loading keeps the existing behavior: missing local image files return `404`, and the frontend leaves the image empty.

## Testing

Add tests to `tests/test_annotations_v2_app.py` before implementation:

- Store-level test that rough, fine, sample, and label visualization results return the expected item indexes and stage fields.
- API route test for `/api/tasks/<task_id>/visualization-results`.
- Template/static test that the visualization page is separate from the rating page and exposes expected IDs.
- Frontend string test that task cards link to rough, fine, sample, and label visualization pages.

Verification commands:

```bash
python -m pytest tests/test_annotations_v2_app.py -q
node --check web/annotations_v2/static/app.js
```

## Out Of Scope

- First-generation statistics charts
- Result filtering drawer
- QC edit or undo
- Issue creation
- Excel export
- Prompt extraction from label JSON files

