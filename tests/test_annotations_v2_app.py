import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_workspace_tmp():
    path = Path("annotations_test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_v2_json_file_writes_are_safe_when_concurrent(monkeypatch):
    from web.annotations_v2 import app as annotations_v2_app

    tmp_path = make_workspace_tmp()
    path = tmp_path / "state.json"
    original_dump = annotations_v2_app.json.dump
    dump_condition = threading.Condition()
    entered_dumps = 0

    def slow_dump(data, handle, **kwargs):
        nonlocal entered_dumps
        handle.write("{")
        handle.flush()
        with dump_condition:
            entered_dumps += 1
            dump_condition.notify_all()
            if entered_dumps < 2:
                dump_condition.wait(timeout=0.2)
        handle.seek(0)
        handle.truncate()
        original_dump(data, handle, **kwargs)

    monkeypatch.setattr(annotations_v2_app.json, "dump", slow_dump)

    with RealThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(annotations_v2_app.write_json_file, path, {"writer": "alice"}),
            executor.submit(annotations_v2_app.write_json_file, path, {"writer": "bob"}),
        ]
        errors = []
        for future in futures:
            error = future.exception(timeout=5)
            if error is not None:
                errors.append(error)

    assert errors == []
    assert json.loads(path.read_text(encoding="utf-8")) in [{"writer": "alice"}, {"writer": "bob"}]


def test_v2_screening_record_updates_are_safe_when_concurrent(monkeypatch):
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    original_read_records = store._read_records
    read_barrier = threading.Barrier(2)
    guarded_read_count = 0
    read_count_lock = threading.Lock()

    records_lock = None

    def synchronized_read_records(task_payload):
        nonlocal guarded_read_count
        records = original_read_records(task_payload)
        if records_lock is not None and getattr(records_lock, "_is_owned", lambda: False)():
            return records
        with read_count_lock:
            guarded_read_count += 1
            read_count = guarded_read_count
        if read_count <= 2:
            read_barrier.wait(timeout=5)
        return records

    monkeypatch.setattr(store, "_read_records", synchronized_read_records)
    from web.annotations_v2.app import json_write_lock
    records_lock = json_write_lock(store._records_path(store._require_task(task["id"])))

    with RealThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.save_rough, task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False}),
            executor.submit(store.save_rough, task["id"], 1, {"username": "bob", "mos": 4, "has_defect": False}),
        ]
        for future in futures:
            future.result(timeout=5)

    records = original_read_records(store._require_task(task["id"]))

    assert set(records) == {"0", "1"}
    assert records["0"]["rough"]["username"] == "alice"
    assert records["1"]["rough"]["username"] == "bob"


def test_v2_records_are_saved_as_per_item_files():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 4, "has_defect": False})

    records_dir = Path(task["data_dir"]) / "records"
    assert records_dir.is_dir()
    assert json.loads((records_dir / "0.json").read_text(encoding="utf-8"))["rough"]["username"] == "alice"
    assert json.loads((records_dir / "1.json").read_text(encoding="utf-8"))["rough"]["username"] == "bob"
    assert not (Path(task["data_dir"]) / "records.json").exists()


def test_v2_reads_legacy_records_json_and_new_item_records_together():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    data_dir = Path(task["data_dir"])
    write_json(
        data_dir / "records.json",
        {
            "0": {"rough": {"username": "legacy", "mos": 3}},
            "1": {"rough": {"username": "legacy", "mos": 4}},
        },
    )
    write_json(data_dir / "records" / "0.json", {"rough": {"username": "new", "mos": 5}})

    records = store._read_records(store._require_task(task["id"]))

    assert records["0"]["rough"]["username"] == "new"
    assert records["1"]["rough"]["username"] == "legacy"


def test_v2_item_record_read_skips_legacy_records_when_shard_exists(monkeypatch):
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    data_dir = Path(task["data_dir"])
    write_json(data_dir / "records.json", {"0": {"rough": {"username": "legacy", "mos": 3}}})
    write_json(data_dir / "records" / "0.json", {"rough": {"username": "new", "mos": 5}})
    original_read_json_file = annotations_v2_app.read_json_file
    legacy_reads = 0

    def counting_read_json_file(path, default):
        nonlocal legacy_reads
        if Path(path).name == "records.json":
            legacy_reads += 1
        return original_read_json_file(path, default)

    monkeypatch.setattr(annotations_v2_app, "read_json_file", counting_read_json_file)

    record = store._read_record(store._require_task(task["id"]), 0)

    assert record["rough"]["username"] == "new"
    assert legacy_reads == 0


def make_test_image(path, size):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(20, 120, 220)).save(path, format="JPEG")


def test_v2_task_creation_loads_jsonl_and_label_files():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    label_dir = tmp_path / "labels"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_json(label_dir / "src" / "a.json", {"labels": {"菜品种类": "中餐"}})
    write_json(label_dir / "dst" / "a.json", {"美学评分": 4})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    items = store.list_stage_items(task["id"], "rough")
    assert task["name"] == "v2 food"
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}, "输出图": {"美学评分": 4}}
    assert store.summary(task["id"])["total"] == 1


def test_v2_task_creation_loads_generation_prompt_from_user_json_root():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    prompt_dir = tmp_path / "output_gen_json"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "ori_image/酒水饮料/a.jpg",
                "dst_image": "output_gen/酒水饮料/a_p1_方案_1_12345.jpg",
            }
        ],
    )
    write_json(
        prompt_dir / "酒水饮料" / "a_p1_方案_1_12345.json",
        {"original_plan": "### 方案 1\n\n- 放低机位\n- 增强纵深"},
    )

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "prompt task",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "generation_prompt_dir": str(prompt_dir),
        }
    )

    item = store.list_stage_items(task["id"], "rough")[0]
    total, rows = store.get_visualization_results(task["id"], "rough")

    assert task["generation_prompt_dir"] == str(prompt_dir)
    assert item["generation_prompt"] == "### 方案 1\n\n- 放低机位\n- 增强纵深"
    assert item["generation_prompt_json_path"].endswith("a_p1_方案_1_12345.json")
    assert rows[0]["generation_prompt"] == item["generation_prompt"]


def test_v2_update_task_generation_prompt_dir_refreshes_existing_items():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    prompt_dir = tmp_path / "output_gen_json"
    write_jsonl(
        jsonl_path,
        [{"src_image": "ori_image/甜品/a.jpg", "dst_image": "output_gen/甜品/a_p2_方案_2_777.jpg"}],
    )
    write_json(prompt_dir / "甜品" / "a_p2_方案_2_777.json", {"original_plan": {"title": "甜品特写"}})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    assert store.list_stage_items(task["id"], "rough")[0].get("generation_prompt") == ""

    store.update_task(task["id"], {"generation_prompt_dir": str(prompt_dir)})
    item = store.list_stage_items(task["id"], "rough")[0]

    assert '"title": "甜品特写"' in item["generation_prompt"]
    assert item["generation_prompt_json_path"].endswith("a_p2_方案_2_777.json")


def test_v2_task_data_dir_can_be_configured_separately_from_state_path():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    data_root = tmp_path / "annotation-records"
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state" / "state.json", data_root=data_root)
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    assert Path(task["data_dir"]).parent == data_root
    assert (data_root / task["id"] / "items.json").exists()
    assert (data_root / task["id"] / "records").is_dir()
    assert not (tmp_path / "state" / "tasks" / task["id"]).exists()


def test_v2_store_paths_can_be_configured_by_environment(monkeypatch):
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    state_path = tmp_path / "configured" / "state.json"
    data_root = tmp_path / "configured-data"
    preview_cache_root = tmp_path / "configured-preview-cache"
    monkeypatch.setenv("ANNOTATIONS_V2_STATE_PATH", str(state_path))
    monkeypatch.setenv("ANNOTATIONS_V2_DATA_DIR", str(data_root))
    monkeypatch.setenv("ANNOTATIONS_V2_PREVIEW_CACHE_DIR", str(preview_cache_root))

    store = AnnotationV2Store()

    assert store.state_path == state_path
    assert store._task_data_dir("task-1") == data_root / "task-1"
    assert store.preview_cache_dir("task-1") == preview_cache_root / "task-1"


def test_v2_default_server_host_matches_platform(monkeypatch):
    import types

    label_options = types.ModuleType("web.annotations.label_options")
    label_options.LABEL_OPTION_GROUPS = []
    monkeypatch.setitem(sys.modules, "web.annotations.label_options", label_options)

    from web.annotations_v2 import app as annotations_v2_app

    def set_platform(system_name):
        fake_platform = type("FakePlatform", (), {"system": staticmethod(lambda: system_name)})
        monkeypatch.setattr(annotations_v2_app, "platform", fake_platform, raising=False)

    set_platform("Darwin")
    assert annotations_v2_app.default_server_host() == "127.0.0.1"

    set_platform("Linux")
    assert annotations_v2_app.default_server_host() == "0.0.0.0"

    set_platform("Windows")
    assert annotations_v2_app.default_server_host() == "127.0.0.1"


def test_v2_image_endpoint_limits_long_edge_by_default_and_can_return_original():
    from PIL import Image
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    make_test_image(tmp_path / "src" / "large.jpg", (2048, 512))
    make_test_image(tmp_path / "dst" / "small.jpg", (64, 64))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/large.jpg", "dst_image": "dst/small.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        client = annotations_v2_app.app.test_client()

        preview_response = client.get(f"/api/tasks/{task['id']}/images/0/src")
        original_response = client.get(f"/api/tasks/{task['id']}/images/0/src?original=1")

        preview = Image.open(BytesIO(preview_response.data))
        original = Image.open(BytesIO(original_response.data))
        assert preview_response.status_code == 200
        assert original_response.status_code == 200
        assert max(preview.size) == 1024
        assert original.size == (2048, 512)
    finally:
        annotations_v2_app.store = old_store


def test_v2_image_endpoint_caches_resized_preview_in_configured_dir():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    preview_cache_root = tmp_path / "preview-cache"
    make_test_image(tmp_path / "src" / "large.jpg", (2048, 512))
    make_test_image(tmp_path / "dst" / "small.jpg", (64, 64))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/large.jpg", "dst_image": "dst/small.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json", preview_cache_dir=preview_cache_root)
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        client = annotations_v2_app.app.test_client()

        response = client.get(f"/api/tasks/{task['id']}/images/0/src")

        assert response.status_code == 200
        cached_files = list((preview_cache_root / task["id"]).glob("*.jpg"))
        assert len(cached_files) == 1
        assert cached_files[0].stat().st_size == len(response.data)
        assert not (Path(task["data_dir"]) / "preview_cache").exists()
    finally:
        annotations_v2_app.store = old_store


def test_v2_preview_cache_job_generates_all_resized_image_previews():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store, PreviewCacheJobs

    tmp_path = make_workspace_tmp()
    preview_cache_root = tmp_path / "preview-cache"
    make_test_image(tmp_path / "src" / "0.jpg", (1400, 800))
    make_test_image(tmp_path / "src" / "1.jpg", (1200, 900))
    make_test_image(tmp_path / "dst" / "0.jpg", (1600, 900))
    make_test_image(tmp_path / "dst" / "1.jpg", (1300, 1100))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/0.jpg", "dst_image": "dst/0.jpg"},
            {"src_image": "src/1.jpg", "dst_image": "dst/1.jpg"},
        ],
    )

    old_store = annotations_v2_app.store
    old_jobs = annotations_v2_app.preview_cache_jobs
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json", preview_cache_dir=preview_cache_root)
    annotations_v2_app.preview_cache_jobs = PreviewCacheJobs(annotations_v2_app.store)
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        client = annotations_v2_app.app.test_client()

        start_response = client.post(f"/api/tasks/{task['id']}/preview-cache/jobs")

        assert start_response.status_code == 202
        job_id = start_response.get_json()["job"]["id"]
        for _ in range(50):
            job_response = client.get(f"/api/tasks/{task['id']}/preview-cache/jobs/{job_id}")
            job = job_response.get_json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        else:
            raise AssertionError("preview cache job did not complete")

        assert job["progress"] == 100
        assert job["result"]["total"] == 4
        assert job["result"]["generated_count"] == 4
        assert job["result"]["failed_count"] == 0
        assert len(list((preview_cache_root / task["id"]).glob("*.jpg"))) == 4
        assert not (Path(task["data_dir"]) / "preview_cache").exists()
    finally:
        annotations_v2_app.preview_cache_jobs = old_jobs
        annotations_v2_app.store = old_store


def test_v2_preview_cache_uses_sixteen_worker_threads(monkeypatch):
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    rows = []
    for index in range(8):
        make_test_image(tmp_path / "src" / f"{index}.jpg", (1200, 800))
        make_test_image(tmp_path / "dst" / f"{index}.jpg", (1200, 800))
        rows.append({"src_image": f"src/{index}.jpg", "dst_image": f"dst/{index}.jpg"})
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, rows)

    worker_counts = []

    class CapturingThreadPoolExecutor(RealThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            if "max_workers" in kwargs:
                worker_counts.append(kwargs["max_workers"])
            elif args:
                worker_counts.append(args[0])
            super().__init__(*args, **kwargs)

    active_count = 0
    max_active_count = 0
    lock = threading.Lock()

    def fake_resized_image_file(path, cache_dir, max_edge=1024):
        nonlocal active_count, max_active_count
        with lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
        try:
            time.sleep(0.03)
            return Path(path).resolve(), "image/jpeg"
        finally:
            with lock:
                active_count -= 1

    monkeypatch.setattr(annotations_v2_app, "ThreadPoolExecutor", CapturingThreadPoolExecutor, raising=False)
    monkeypatch.setattr(annotations_v2_app, "resized_image_file", fake_resized_image_file)

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    result = store.warm_preview_cache(task["id"])

    assert worker_counts == [16]
    assert max_active_count > 1
    assert result["total"] == 16
    assert result["processed_count"] == 16
    assert result["failed_count"] == 0


def test_v2_preview_cache_deduplicates_repeated_image_paths(monkeypatch):
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    make_test_image(tmp_path / "shared" / "src.jpg", (1200, 800))
    make_test_image(tmp_path / "shared" / "dst.jpg", (1300, 900))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "shared/src.jpg", "dst_image": "shared/dst.jpg"},
            {"src_image": "shared/src.jpg", "dst_image": "shared/dst.jpg"},
            {"src_image": "shared/src.jpg", "dst_image": "shared/dst.jpg"},
        ],
    )

    resized_paths = []

    def fake_resized_image_file(path, cache_dir, max_edge=1024):
        resized_paths.append(Path(path).resolve())
        return cache_dir / f"{len(resized_paths)}.jpg", "image/jpeg"

    monkeypatch.setattr(annotations_v2_app, "resized_image_file", fake_resized_image_file)

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    result = store.warm_preview_cache(task["id"])

    assert result["total"] == 6
    assert result["processed_count"] == 6
    assert result["unique_image_count"] == 2
    assert result["duplicate_ref_count"] == 4
    assert result["generated_count"] == 6
    assert resized_paths == [
        (tmp_path / "shared" / "src.jpg").resolve(),
        (tmp_path / "shared" / "dst.jpg").resolve(),
    ]


def test_v2_preview_cache_job_reuses_running_task_job(monkeypatch):
    from web.annotations_v2.app import AnnotationV2Store, PreviewCacheJobs

    tmp_path = make_workspace_tmp()
    make_test_image(tmp_path / "src" / "a.jpg", (1200, 800))
    make_test_image(tmp_path / "dst" / "a.jpg", (1200, 800))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    jobs = PreviewCacheJobs(store)
    started = threading.Event()
    release = threading.Event()

    def slow_warm_preview_cache(task_id, progress_callback=None):
        started.set()
        release.wait(timeout=2)
        return {"total": 0, "processed_count": 0, "generated_count": 0, "skipped_count": 0, "failed_count": 0, "failures": []}

    monkeypatch.setattr(store, "warm_preview_cache", slow_warm_preview_cache)

    first = jobs.start(task["id"])
    assert started.wait(timeout=1)
    second = jobs.start(task["id"])
    release.set()

    assert second["id"] == first["id"]


def test_v2_preview_cache_job_reuses_job_when_started_concurrently(monkeypatch):
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store, PreviewCacheJobs

    tmp_path = make_workspace_tmp()
    make_test_image(tmp_path / "src" / "a.jpg", (1200, 800))
    make_test_image(tmp_path / "dst" / "a.jpg", (1200, 800))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    jobs = PreviewCacheJobs(store)
    uuid_barrier = threading.Barrier(2)
    uuid_lock = threading.Lock()
    uuid_count = 0
    release_job = threading.Event()

    def slow_warm_preview_cache(task_id, progress_callback=None):
        release_job.wait(timeout=2)
        return {"total": 0, "processed_count": 0, "generated_count": 0, "skipped_count": 0, "failed_count": 0, "failures": []}

    def synchronized_uuid4():
        nonlocal uuid_count
        uuid_barrier.wait(timeout=5)
        with uuid_lock:
            uuid_count += 1
            return f"job-{uuid_count}"

    monkeypatch.setattr(store, "warm_preview_cache", slow_warm_preview_cache)
    monkeypatch.setattr(annotations_v2_app.uuid, "uuid4", synchronized_uuid4)

    try:
        with RealThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(jobs.start, task["id"]) for _ in range(2)]
            started_jobs = [future.result(timeout=5) for future in futures]

        assert started_jobs[0]["id"] == started_jobs[1]["id"]
        assert len(jobs._jobs) == 1
    finally:
        release_job.set()


def test_v2_task_creation_removes_non_canonical_ai_label_fields():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    label_dir = tmp_path / "labels"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输出图": {
                        "景别": "中近景",
                        "景别_v2": "近景",
                        "景别_alt": "近景",
                        "图一": "污染字段",
                    }
                },
            }
        ],
    )
    write_json(label_dir / "dst" / "a.json", {"labels": {"景别_secondary": "近景", "美学评分": 4}})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "sanitize labels",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir),
        }
    )

    item = store.list_stage_items(task["id"], "rough")[0]
    assert item["labels"] == {"输出图": {"景别": "中近景", "美学评分": 4}}


def test_v2_task_payload_exposes_standard_label_choice_options():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label choices",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    assert task["label_option_groups"][0]["name"] == "输入图"
    assert task["label_option_groups"][0]["dimensions"][1]["name"] == "菜品种类"
    assert "中餐" in task["label_option_groups"][0]["dimensions"][1]["options"]


def test_v2_all_standard_label_dimensions_expose_options():
    from web.annotations.label_options import LABEL_OPTION_GROUPS

    missing_options = []
    for group in LABEL_OPTION_GROUPS:
        for dimension in group.get("dimensions", []):
            if not isinstance(dimension.get("options"), list):
                missing_options.append(f"{group.get('name')}/{dimension.get('name')}")

    assert missing_options == []


def test_v2_update_task_properties_persists_issue_options_and_label_paths():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "editable task",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"issue_options": ["主体问题"], "min_mos": 4},
            "fine": {"min_mos": 4},
        }
    )

    updated = store.update_task(
        task["id"],
        {
            "rough": {"issue_options": "构图问题\n颜色问题"},
            "selected_label_paths": "输出图/景别, 输入图/拍摄场景",
        },
    )

    assert updated["rough"]["issue_options"] == ["构图问题", "颜色问题"]
    assert updated["rough"]["min_mos"] == 4
    assert updated["selected_label_paths"] == [["输出图", "景别"], ["输入图", "拍摄场景"]]
    persisted = AnnotationV2Store(tmp_path / "state.json").list_tasks()[0]
    assert persisted["rough"]["issue_options"] == ["构图问题", "颜色问题"]
    assert persisted["selected_label_paths"] == [["输出图", "景别"], ["输入图", "拍摄场景"]]


def test_v2_delete_task_only_unregisters_task_and_preserves_data_dir():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"name": "delete me", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    task_data_dir = Path(task["data_dir"])
    items_path = task_data_dir / "items.json"
    records_dir = task_data_dir / "records"

    deleted = store.delete_task(task["id"])

    assert deleted["id"] == task["id"]
    assert store.list_tasks() == []
    assert task_data_dir.exists()
    assert items_path.exists()
    assert records_dir.exists()


def test_v2_stage_gates_sampling_label_correction_and_export():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True, "issue_options": ["主体问题"]},
            "fine": {"min_mos": 4, "enable_defect": True},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 4, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True, "issues": ["主体问题"]})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})

    fine_items = store.list_stage_items(task["id"], "fine")
    assert [item["item_index"] for item in fine_items] == [0, 2]

    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})

    sample = store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    assert sample["sampled_count"] == 1
    assert sample["buckets"] == [{"bucket": "输入图/菜品种类=中餐", "candidate_count": 1, "sampled_count": 1}]
    assert [item["item_index"] for item in store.list_stage_items(task["id"], "label")] == [0]

    corrected = {"输入图": {"菜品种类": "融合菜"}}
    store.save_label(task["id"], 0, {"username": "carol", "labels": corrected})

    summary = store.summary(task["id"])
    assert summary["rough_passed"] == 2
    assert summary["fine_passed"] == 1
    assert summary["sampled"] == 1
    assert summary["label_completed"] == 1

    rows = [json.loads(line) for line in store.export_jsonl(task["id"]).splitlines()]
    assert rows[0]["rough"]["mos"] == 4
    assert rows[0]["fine"]["mos"] == 5
    assert rows[0]["sampled"] is True
    assert rows[0]["corrected_labels"] == corrected
    assert rows[1]["rough"]["has_defect"] is True


def test_v2_label_stage_exposes_auto_labels_as_editable_draft():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输入图": {"菜品种类": "中餐", "拍摄场景": "室内"},
                    "输出图": {"美学评分": 4},
                },
            }
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label draft",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"], ["输入图", "拍摄场景"], ["输出图", "美学评分"]],
        }
    )
    store.save_rough(task["id"], 0, {"username": "rough", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "fine", "mos": 5, "has_defect": False})
    store.sample(task["id"], {"select_all": True})

    item = store.list_stage_items(task["id"], "label")[0]

    assert item["record"]["label_draft"]["labels"] == {
        "输入图": {"菜品种类": "中餐", "拍摄场景": "室内"},
        "输出图": {"美学评分": 4},
    }
    assert "label" not in item["record"]


def test_v2_label_stage_draft_overlays_saved_corrections_on_auto_labels():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输入图": {"菜品种类": "中餐", "拍摄场景": "室内"},
                    "输出图": {"美学评分": 4},
                },
            }
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label draft overlay",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"], ["输入图", "拍摄场景"], ["输出图", "美学评分"]],
        }
    )
    store.save_rough(task["id"], 0, {"username": "rough", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "fine", "mos": 5, "has_defect": False})
    store.sample(task["id"], {"select_all": True})
    store.save_label(task["id"], 0, {"username": "labeler", "labels": {"输入图": {"菜品种类": "西餐"}}})

    stored_task = store._require_task(task["id"])
    item = store._read_items(stored_task)[0]
    record = store._read_records(stored_task)["0"]
    payload = store._item_payload(stored_task, item, record, stage="label")

    assert payload["record"]["label"]["labels"] == {"输入图": {"菜品种类": "西餐"}}
    assert payload["record"]["label_draft"]["labels"] == {
        "输入图": {"菜品种类": "西餐", "拍摄场景": "室内"},
        "输出图": {"美学评分": 4},
    }


def test_v2_save_label_keeps_only_selected_standard_label_paths():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输入图": {"菜品种类": "中餐", "拍摄场景": "室内"},
                    "输出图": {"美学评分": 4},
                },
            }
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )
    store.save_rough(task["id"], 0, {"username": "rough", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "fine", "mos": 5, "has_defect": False})
    store.sample(task["id"], {"select_all": True})

    saved = store.save_label(
        task["id"],
        0,
        {
            "username": "labeler",
            "labels": {
                "输入图": {
                    "菜品种类": "融合菜",
                    "拍摄场景": "室外",
                    "景别_v2": "污染字段",
                },
                "输出图": {"美学评分": 5},
            },
        },
    )

    assert saved["labels"] == {"输入图": {"菜品种类": "融合菜"}}


def test_v2_label_stage_hides_items_that_already_have_saved_labels():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "hide labeled",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )
    for item_index in range(2):
        store.save_rough(task["id"], item_index, {"username": "rough", "mos": 5, "has_defect": False})
        store.save_fine(task["id"], item_index, {"username": "fine", "mos": 5, "has_defect": False})
    store.sample(task["id"], {"select_all": True})
    store.save_label(task["id"], 0, {"username": "labeler", "labels": {"输入图": {"菜品种类": "中餐"}}})

    label_items = store.list_stage_items(task["id"], "label")

    assert [item["item_index"] for item in label_items] == [1]
    assert store.summary(task["id"])["label_completed"] == 1


def test_v2_label_stage_reserves_open_item_for_current_user():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task(
            {
                "name": "reserve labels",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            }
        )
        for item_index in range(2):
            annotations_v2_app.store.save_rough(task["id"], item_index, {"username": "rough", "mos": 5, "has_defect": False})
            annotations_v2_app.store.save_fine(task["id"], item_index, {"username": "fine", "mos": 5, "has_defect": False})
        annotations_v2_app.store.sample(task["id"], {"select_all": True})
        client = annotations_v2_app.app.test_client()

        alice_response = client.get(f"/api/tasks/{task['id']}/items?stage=label&username=alice&include_history=1")
        bob_response = client.get(f"/api/tasks/{task['id']}/items?stage=label&username=bob&include_history=1")

        assert alice_response.status_code == 200
        assert bob_response.status_code == 200
        assert alice_response.get_json()["items"][0]["item_index"] == 0
        assert bob_response.get_json()["items"][0]["item_index"] == 1
    finally:
        annotations_v2_app.store = old_store


def test_v2_sampling_buckets_support_selected_counts_and_select_all():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {"输入图": {"菜品种类": "中餐"}, "输出图": {"景别": "近景"}},
            },
            {
                "src_image": "src/b.jpg",
                "dst_image": "dst/b.jpg",
                "labels": {"输入图": {"菜品种类": "中餐"}, "输出图": {"景别": "近景"}},
            },
            {
                "src_image": "src/c.jpg",
                "dst_image": "dst/c.jpg",
                "labels": {"输入图": {"菜品种类": "西餐"}, "输出图": {"景别": "全景"}},
            },
            {
                "src_image": "src/d.jpg",
                "dst_image": "dst/d.jpg",
                "labels": {"输入图": {"菜品种类": "甜品"}, "输出图": {"景别": "特写"}},
            },
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "sample by bucket",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4},
            "fine": {"min_mos": 4},
        }
    )
    for item_index in range(4):
        store.save_rough(task["id"], item_index, {"username": "alice", "mos": 5, "has_defect": False})
        store.save_fine(task["id"], item_index, {"username": "bob", "mos": 5 if item_index != 3 else 3, "has_defect": False})

    buckets = store.sample_buckets(task["id"])

    assert buckets["candidate_count"] == 3
    assert buckets["buckets"] == [
        {"bucket": "输入图/菜品种类=中餐", "candidate_count": 2, "sampled_count": 0},
        {"bucket": "输入图/菜品种类=西餐", "candidate_count": 1, "sampled_count": 0},
        {"bucket": "输出图/景别=全景", "candidate_count": 1, "sampled_count": 0},
        {"bucket": "输出图/景别=近景", "candidate_count": 2, "sampled_count": 0},
    ]

    sample = store.sample(
        task["id"],
        {
            "selections": [
                {"bucket": "输出图/景别=近景", "count": 1},
                {"bucket": "输入图/菜品种类=西餐", "count": 5},
            ]
        },
    )

    assert sample["candidate_count"] == 3
    assert sample["sampled_count"] == 2
    assert sample["buckets"] == [
        {"bucket": "输入图/菜品种类=中餐", "candidate_count": 2, "sampled_count": 1},
        {"bucket": "输入图/菜品种类=西餐", "candidate_count": 1, "sampled_count": 1},
        {"bucket": "输出图/景别=全景", "candidate_count": 1, "sampled_count": 1},
        {"bucket": "输出图/景别=近景", "candidate_count": 2, "sampled_count": 1},
    ]
    assert len(store.list_stage_items(task["id"], "label")) == 2

    sample_all = store.sample(task["id"], {"select_all": True})

    assert sample_all["sampled_count"] == 3
    assert len(store.list_stage_items(task["id"], "label")) == 3


def test_v2_visualization_results_are_stage_specific():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "visualize",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True},
            "fine": {"min_mos": 4, "enable_defect": False},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})
    store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    store.save_label(task["id"], 0, {"username": "carol", "labels": {"输入图": {"菜品种类": "融合菜"}}})

    rough_total, rough_rows = store.get_visualization_results(task["id"], "rough", offset=0, limit=10)
    fine_total, fine_rows = store.get_visualization_results(task["id"], "fine", offset=0, limit=10)
    sample_total, sample_rows = store.get_visualization_results(task["id"], "sample", offset=0, limit=10)
    label_total, label_rows = store.get_visualization_results(task["id"], "label", offset=0, limit=10)

    assert rough_total == 3
    assert [row["item_index"] for row in rough_rows] == [0, 1, 2]
    assert rough_rows[1]["stage_passed"] is False
    assert rough_rows[1]["stage_annotations"][0]["has_defect"] is True

    assert fine_total == 2
    assert [row["item_index"] for row in fine_rows] == [0, 2]
    assert fine_rows[0]["stage_passed"] is True
    assert fine_rows[1]["stage_passed"] is False

    assert sample_total == 1
    assert sample_rows[0]["sampled"] is True
    assert sample_rows[0]["sample_bucket"] == "输入图/菜品种类=中餐"

    assert label_total == 1
    assert label_rows[0]["corrected_labels"] == {"输入图": {"菜品种类": "融合菜"}}
    assert label_rows[0]["label_username"] == "carol"
    assert label_rows[0]["src_relative_path"] == "src/a.jpg"
    assert label_rows[0]["dst_relative_path"] == "dst/a.jpg"


def test_v2_visualization_results_support_mos_defect_annotator_and_tag_filters():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "visualize filters",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True},
            "fine": {"min_mos": 4, "enable_defect": True},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": True})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 4, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "dora", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "erin", "mos": 4, "has_defect": True})

    _, mos_rows = store.get_visualization_results(task["id"], "rough", filters={"mos": [5]}, offset=0, limit=10)
    _, defect_rows = store.get_visualization_results(task["id"], "rough", filters={"has_defect": [True]}, offset=0, limit=10)
    _, annotator_rows = store.get_visualization_results(task["id"], "rough", filters={"annotators": ["alice"]}, offset=0, limit=10)
    _, tag_rows = store.get_visualization_results(
        task["id"],
        "rough",
        filters={"labels": [{"path": ["输入图", "菜品种类"], "values": ["西餐"]}]},
        offset=0,
        limit=10,
    )
    _, fine_rows = store.get_visualization_results(
        task["id"],
        "fine",
        filters={"has_defect": [True], "annotators": ["erin"]},
        offset=0,
        limit=10,
    )

    assert [row["item_index"] for row in mos_rows] == [0]
    assert [row["item_index"] for row in defect_rows] == [1]
    assert [row["item_index"] for row in annotator_rows] == [0, 2]
    assert [row["item_index"] for row in tag_rows] == [1]
    assert [row["item_index"] for row in fine_rows] == [2]


def test_v2_visualization_results_api_returns_filter_options_and_accepts_filters():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task(
            {
                "name": "api visualize filters",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            }
        )
        annotations_v2_app.store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
        annotations_v2_app.store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": True})
        client = annotations_v2_app.app.test_client()

        filters = json.dumps({"mos": [5], "has_defect": [False], "annotators": ["alice"]}, ensure_ascii=False)
        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=rough&filters={filters}")
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["total"] == 1
        assert payload["results"][0]["item_index"] == 0
        assert payload["filter_options"]["mos"] == [3, 5]
        assert payload["filter_options"]["has_defect"] == [False, True]
        assert payload["filter_options"]["annotators"] == ["alice", "bob"]
        assert payload["filter_options"]["label_options"] == [
            {"name": "输入图", "dimensions": [{"name": "菜品种类", "options": ["中餐", "西餐"]}]}
        ]
    finally:
        annotations_v2_app.store = old_store


def test_v2_visualization_results_api_pages_stage_rows():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        task = client.post(
            "/api/tasks",
            json={"name": "api visualize", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)},
        ).get_json()["task"]
        client.post(f"/api/tasks/{task['id']}/items/0/rough", json={"username": "alice", "mos": 5, "has_defect": False})

        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=rough&page=0&limit=1")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["stage"] == "rough"
        assert payload["total"] == 1
        assert payload["page"] == 0
        assert payload["limit"] == 1
        assert payload["results"][0]["item_index"] == 0
        assert payload["results"][0]["stage_result"]["mos"] == 5
    finally:
        annotations_v2_app.store = old_store


def test_v2_sample_page_and_bucket_api_are_available():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task(
            {
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            }
        )
        annotations_v2_app.store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
        annotations_v2_app.store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
        client = annotations_v2_app.app.test_client()

        page_response = client.get(f"/dataset/sample/{task['id']}")
        bucket_response = client.get(f"/api/tasks/{task['id']}/sample-buckets")

        page_html = page_response.data.decode("utf-8")
        assert page_response.status_code == 200
        assert 'data-page="sample"' in page_html
        assert f'data-task-id="{task["id"]}"' in page_html
        assert 'id="selectAllSampleBtn"' in page_html
        assert 'id="sampleBucketList"' in page_html
        assert 'id="runSampleBtn"' in page_html

        assert bucket_response.status_code == 200
        payload = bucket_response.get_json()
        assert payload["result"]["candidate_count"] == 1
        assert payload["result"]["buckets"][0]["bucket"] == "输入图/菜品种类=中餐"
    finally:
        annotations_v2_app.store = old_store


def test_v2_visualization_results_reject_unknown_stage():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=other")
        assert response.status_code == 400
        assert response.get_json()["error"] == "未知可视化阶段"
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_import_annotations_jsonl_merges_screening_sampling_and_labels():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    import_path = tmp_path / "import.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    write_jsonl(
        import_path,
        [
            {
                "item_index": 0,
                "object_labels": {"输入图": {"菜品种类": "融合菜", "景别_alt": "污染字段"}},
                "rough_annotations": [
                    {"username": "r1", "mos": 5, "has_defect": False},
                    {"username": "r2", "mos": 4, "has_defect": False, "issues": ["主体问题"]},
                ],
                "fine": {"username": "f1", "mos": 5, "has_defect": False},
                "sampled": True,
                "sample_bucket": "外部平台分桶",
                "corrected_labels": {"输入图": {"菜品种类": "创意菜", "景别_v2": "污染字段"}},
                "label_username": "labeler",
            },
            {
                "src_image": "src/b.jpg",
                "dst_image": "dst/b.jpg",
                "rough": {"username": "r3", "mos": 3, "has_defect": True, "primary_issue": "主体问题"},
            },
            {"item_index": 99, "rough": {"username": "missing", "mos": 5, "has_defect": False}},
        ],
    )

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "import external",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4, "issue_options": ["主体问题"]},
            "fine": {"annotator_count": 1, "min_mos": 4},
        }
    )

    result = store.import_annotations_jsonl(task["id"], import_path)

    assert result["total_rows"] == 3
    assert result["imported_count"] == 2
    assert result["unmatched_count"] == 1
    assert result["updated_items"] == 1
    assert result["updated_records"] == 2
    assert result["unmatched_rows"] == [{"line": 3, "reason": "未匹配到任务图片"}]

    item = store.list_stage_items(task["id"], "rough")[0]
    assert item["labels"] == {"输入图": {"菜品种类": "融合菜"}}

    records = store._read_records(store._require_task(task["id"]))
    first_record = records["0"]
    assert [entry["username"] for entry in first_record["rough_annotations"]] == ["r1", "r2"]
    assert first_record["rough"]["mos"] == 4
    assert first_record["rough"]["has_defect"] is False
    assert first_record["fine"]["username"] == "f1"
    assert first_record["sampled"] is True
    assert first_record["sample_bucket"] == "外部平台分桶"
    assert first_record["label"]["username"] == "labeler"
    assert first_record["label"]["labels"] == {"输入图": {"菜品种类": "创意菜"}}

    second_record = records["1"]
    assert second_record["rough"]["username"] == "r3"
    assert second_record["rough"]["has_defect"] is True


def test_v2_import_annotations_with_corrected_labels_marks_item_sampled():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    import_path = tmp_path / "v2-labels.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_jsonl(
        import_path,
        [
            {
                "item_index": 0,
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "original_labels": {"输入图": {"菜品种类": "中餐"}},
                "corrected_labels": {"输入图": {"菜品种类": "融合菜", "景别_v2": "污染字段"}},
                "label_username": "label-user",
                "label_updated_at": 12345,
            }
        ],
    )

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    result = store.import_annotations_jsonl(task["id"], import_path)

    assert result["imported_count"] == 1
    assert result["skipped_count"] == 0
    assert result["updated_items"] == 1
    assert result["updated_records"] == 1
    assert result["summary"]["sampled"] == 1
    assert result["summary"]["label_completed"] == 1

    item = store.list_stage_items(task["id"], "rough", include_history=True)[0]
    assert item["labels"] == {"输入图": {"菜品种类": "中餐"}}

    records = store._read_records(store._require_task(task["id"]))
    record = records["0"]
    assert record["sampled"] is True
    assert record["label"]["username"] == "label-user"
    assert record["label"]["labels"] == {"输入图": {"菜品种类": "融合菜"}}

    total, label_rows = store.get_visualization_results(task["id"], "label")
    assert total == 1
    assert label_rows[0]["corrected_labels"] == {"输入图": {"菜品种类": "融合菜"}}


def test_v2_import_annotations_api_accepts_jsonl_path():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    import_path = tmp_path / "import.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_jsonl(import_path, [{"item_index": 0, "rough": {"username": "alice", "mos": 5, "has_defect": False}}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        client = annotations_v2_app.app.test_client()

        response = client.post(f"/api/tasks/{task['id']}/import", json={"jsonl_path": str(import_path)})

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["result"]["imported_count"] == 1
        assert annotations_v2_app.store.summary(task["id"])["rough_completed"] == 1
    finally:
        annotations_v2_app.store = old_store


def test_v2_screening_assignment_keeps_per_user_records_and_caps_annotators():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg"},
            {"src_image": "src/d.jpg", "dst_image": "dst/d.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "multi user",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 2, "min_mos": 4},
        }
    )

    alice_first = store.list_stage_items(task["id"], "rough", username="alice")[0]["item_index"]
    bob_first = store.list_stage_items(task["id"], "rough", username="bob")[0]["item_index"]
    assert alice_first != bob_first

    alice_record = store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 0, {"username": "alice", "mos": 4, "has_defect": False})
    bob_record = store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})

    full_item_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="carol")]
    assert 0 not in full_item_indexes
    assert alice_record["username"] == "alice"
    assert bob_record["username"] == "bob"

    records = store._read_records(store._require_task(task["id"]))
    assert [entry["username"] for entry in records["0"]["rough_annotations"]] == ["alice", "bob"]
    assert records["0"]["rough"]["mos"] == 4

    try:
        store.save_rough(task["id"], 0, {"username": "carol", "mos": 5, "has_defect": False})
    except ValueError as exc:
        assert str(exc) == "该图片的粗筛标注人数已达到上限"
    else:
        raise AssertionError("expected rough annotator cap to reject the third user")


def test_v2_stage_items_do_not_reload_records_already_annotated_by_same_user():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "no repeated assignment",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 2, "min_mos": 4},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    alice_rough_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="alice")]
    bob_rough_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="bob")]

    assert 0 not in alice_rough_indexes
    assert 0 in bob_rough_indexes

    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "carol", "mos": 5, "has_defect": False})

    carol_fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="carol")]
    dora_fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="dora")]

    assert 0 not in carol_fine_indexes
    assert 0 in dora_fine_indexes


def test_v2_stage_items_can_include_only_current_user_history_for_paging():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "history paging",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 5, "has_defect": False})

    default_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="alice")]
    history_items = store.list_stage_items(task["id"], "rough", username="alice", include_history=True)
    history_indexes = [item["item_index"] for item in history_items]

    assert 0 not in default_indexes
    assert 0 in history_indexes
    assert 1 in history_indexes
    assert history_indexes.index(0) < history_indexes.index(2)
    assert history_items[history_indexes.index(0)]["record"]["rough"]["username"] == "alice"
    assert "rough" not in history_items[history_indexes.index(1)]["record"]


def test_v2_label_stage_history_is_scoped_to_current_user():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "甜品"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label history",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )
    for item_index in range(3):
        store.save_rough(task["id"], item_index, {"username": "rough", "mos": 5, "has_defect": False})
        store.save_fine(task["id"], item_index, {"username": "fine", "mos": 5, "has_defect": False})
    store.sample(task["id"], {"select_all": True})

    store.save_label(task["id"], 0, {"username": "alice", "labels": {"输入图": {"菜品种类": "融合菜"}}})
    store.save_label(task["id"], 1, {"username": "bob", "labels": {"输入图": {"菜品种类": "西餐"}}})

    default_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "label", username="alice")]
    history_items = store.list_stage_items(task["id"], "label", username="alice", include_history=True)
    history_indexes = [item["item_index"] for item in history_items]

    assert default_indexes == [2]
    assert history_indexes == [0, 2]
    assert history_items[0]["record"]["label"]["username"] == "alice"
    assert history_items[0]["record"]["label_draft"]["labels"] == {"输入图": {"菜品种类": "融合菜"}}


def test_v2_fine_assignment_waits_for_required_rough_votes_and_aggregate_pass():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "fine gates",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 1, "min_mos": 4},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    assert store.list_stage_items(task["id"], "fine", username="carol") == []

    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": False})

    fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="carol")]
    assert fine_indexes == [0]


def test_v2_summary_reports_screening_round_progress():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "round progress",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2},
            "fine": {"annotator_count": 2},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": False})

    summary = store.summary(task["id"])

    assert summary["rough_annotator_count"] == 2
    assert summary["rough_rounds"] == [
        {"round": 1, "completed": 2, "total": 3},
        {"round": 2, "completed": 1, "total": 3},
    ]
    assert summary["rough_completed"] == 1
    assert summary["rough_annotation_completed"] == 3
    assert summary["rough_annotation_target"] == 6


def test_v2_api_exposes_summary_and_stage_endpoints():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        create_response = client.post(
            "/api/tasks",
            json={
                "name": "api task",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            },
        )
        task = create_response.get_json()["task"]
        rough_response = client.post(
            f"/api/tasks/{task['id']}/items/0/rough",
            json={"username": "alice", "mos": 5, "has_defect": False},
        )
        fine_items_response = client.get(f"/api/tasks/{task['id']}/items?stage=fine")

        assert create_response.status_code == 201
        assert rough_response.status_code == 200
        assert fine_items_response.get_json()["items"][0]["item_index"] == 0
        assert client.get(f"/api/tasks/{task['id']}/summary").get_json()["summary"]["rough_passed"] == 1
    finally:
        annotations_v2_app.store = old_store


def test_v2_update_task_api_refreshes_stage_configuration():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输入图": {"菜品种类": "中餐"},
                    "输出图": {"景别": "近景"},
                },
            }
        ],
    )
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task(
            {
                "name": "api editable",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
                "rough": {"issue_options": ["主体问题"]},
            }
        )

        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={
                "issue_options": ["颜色问题"],
                "selected_label_paths": [["输出图", "景别"]],
            },
        )
        annotations_v2_app.store.save_rough(task["id"], 0, {"username": "rough", "mos": 5, "has_defect": False})
        annotations_v2_app.store.save_fine(task["id"], 0, {"username": "fine", "mos": 5, "has_defect": False})
        annotations_v2_app.store.sample(task["id"], {"select_all": True})
        label_response = client.get(f"/api/tasks/{task['id']}/items?stage=label")

        assert response.status_code == 200
        updated_task = response.get_json()["task"]
        assert updated_task["rough"]["issue_options"] == ["颜色问题"]
        assert updated_task["selected_label_paths"] == [["输出图", "景别"]]
        assert label_response.get_json()["items"][0]["record"]["label_draft"]["labels"] == {"输出图": {"景别": "近景"}}
    finally:
        annotations_v2_app.store = old_store


def test_v2_delete_task_api_requires_admin_username_and_unregisters_only():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task(
            {"name": "api delete", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)}
        )
        task_data_dir = Path(task["data_dir"])

        forbidden_response = client.delete(f"/api/tasks/{task['id']}", json={"username": "alice"})
        allowed_response = client.delete(f"/api/tasks/{task['id']}", json={"username": "孙本猿"})

        assert forbidden_response.status_code == 403
        assert forbidden_response.get_json()["error"] == "只有孙本猿可以删除任务"
        assert allowed_response.status_code == 200
        assert allowed_response.get_json()["task"]["id"] == task["id"]
        assert annotations_v2_app.store.list_tasks() == []
        assert task_data_dir.exists()
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_create_task_reports_missing_jsonl_as_bad_request():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()

        response = client.post(
            "/api/tasks",
            json={
                "name": "missing file",
                "root_dir": str(tmp_path),
                "jsonl_path": str(tmp_path / "missing.jsonl"),
            },
        )

        assert response.status_code == 400
        assert response.get_json()["error"] == f"jsonl 文件不存在：{tmp_path / 'missing.jsonl'}"
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_rate_page_route_and_main_page_are_separate():
    from web.annotations_v2 import app as annotations_v2_app

    annotations_v2_app.app.config.update(TESTING=True)
    client = annotations_v2_app.app.test_client()

    main_response = client.get("/")
    rate_response = client.get("/dataset/rate/task-123?stage=rough")

    main_html = main_response.data.decode("utf-8")
    rate_html = rate_response.data.decode("utf-8")
    assert main_response.status_code == 200
    assert rate_response.status_code == 200
    assert 'id="taskList"' in main_html
    assert 'id="stageBody"' not in main_html
    assert 'id="stageBody"' in rate_html
    assert 'data-page="rate"' in rate_html


def test_v2_visualization_page_route_is_separate_from_rate_page():
    from web.annotations_v2 import app as annotations_v2_app

    annotations_v2_app.app.config.update(TESTING=True)
    client = annotations_v2_app.app.test_client()

    visualize_response = client.get("/dataset/visualize/task-123?stage=rough")
    visualize_html = visualize_response.data.decode("utf-8")

    assert visualize_response.status_code == 200
    assert 'data-page="visualize"' in visualize_html
    assert 'data-task-id="task-123"' in visualize_html
    assert 'id="visualizationStageTabs"' in visualize_html
    assert 'id="visualizationBody"' in visualize_html
    assert 'id="visualizationResultPanel"' in visualize_html
    assert 'id="stageForm"' not in visualize_html


def test_v2_frontend_uses_progress_entries_login_gate_and_no_primary_issue_field():
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="loginView"' in index_template
    assert 'id="appView"' in index_template
    assert 'class="topbar hidden"' in index_template
    assert 'id="primaryIssueInput"' not in index_template
    assert 'screeningProgressCells("粗筛", summary.rough_rounds, summary.rough_passed, { href: `/dataset/rate/${task.id}?stage=rough` })' in script
    assert 'screeningProgressCells("精筛", summary.fine_rounds, summary.fine_passed, { href: `/dataset/rate/${task.id}?stage=fine` })' in script
    assert 'progressCell("标签纠错", summary.label_completed, summary.sampled, null, { href: `/dataset/rate/${task.id}?stage=label` })' in script
    assert 'progressCell("采样", summary.sampled, summary.fine_passed, null, { href: `/dataset/sample/${task.id}` })' in script
    assert '<a href="${entry.href}" class="progressCell progressEntry">' in script
    assert '<button class="progressCell progressEntry" data-action="${entry.action}"' in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=rough">粗筛</a>' not in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=fine">精筛</a>' not in script
    assert '<button data-action="sample" data-id="${task.id}" type="button">采样</button>' not in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=label">标签纠错</a>' not in script
    assert 'id="samplePanel"' not in index_template
    assert 'id="ratingControls"' in rate_template
    assert 'id="labelPreviewBlock"' not in rate_template
    assert 'id="recordPreview"' not in rate_template
    assert 'primaryIssueField' not in script
    assert 'otherIssueInput' not in script


def test_v2_frontend_exposes_task_edit_dialog_for_issue_and_label_paths():
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="taskEditOverlay"' in index_template
    assert 'id="editTaskForm"' in index_template
    assert 'id="editIssueOptionsInput"' in index_template
    assert 'id="editLabelPathsInput"' in index_template
    assert 'data-action="edit"' in script
    assert "openEditTaskDialog(taskId)" in script
    assert "taskIssueOptionsText(task)" in script
    assert "taskLabelPathsText(task)" in script
    assert 'method: "PATCH"' in script
    assert 'rough: { issue_options: parseList($("editIssueOptionsInput").value) }' in script
    assert 'selected_label_paths: parseLabelPaths($("editLabelPathsInput").value)' in script
    assert ".modalOverlay" in styles
    assert ".modalPanel" in styles


def test_v2_frontend_serializes_checked_label_choice_inputs():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'record.label_draft?.labels' in script
    assert 'input.setAttribute("checked", "checked")' in script


def test_v2_templates_bust_shared_app_js_cache_after_queue_changes():
    template_dir = PROJECT_ROOT / "web" / "annotations_v2" / "templates"
    expected_version = "app.js') }}?v=20260609-task-editing"

    for template_name in ["index.html", "rate.html", "sample.html", "visualize.html"]:
        template = (template_dir / template_name).read_text(encoding="utf-8")
        assert expected_version in template
        assert "20260609-label-queue-refresh" not in template
        assert "20260609-label-choice-bars" not in template
        assert "20260609-sample-page" not in template
        assert "20260608-visualization" not in template


def test_v2_screening_page_uses_three_column_rating_panel():
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    stage_grid_start = rate_template.index('id="stageBody"')
    src_index = rate_template.index('id="srcImage"', stage_grid_start)
    dst_index = rate_template.index('id="dstImage"', stage_grid_start)
    form_index = rate_template.index('id="ratingControls"', stage_grid_start)

    assert src_index < dst_index < form_index
    assert 'class="panel annotationPanel"' in rate_template
    assert "mosScoreBar" in script
    assert 'name="mosOption"' in script
    assert 'name="defectOption"' in script
    assert 'value="false"' in script
    assert '${hasDefect ? "" : "checked"}' in script
    assert "otherIssueField" not in script
    assert "noteInput" not in script
    assert "grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) minmax(520px, 680px);" in styles


def test_v2_screening_page_exposes_keyboard_shortcuts():
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "下一条（Space）" in rate_template
    assert "handleRateShortcuts" in script
    assert "selectMosScore" in script
    assert "setDefectValue(true)" in script
    assert 'event.code === "Space"' in script
    assert "/^[1-5]$/.test(event.key)" in script
    assert "是（E）" in script
    assert "isTextEditingShortcutTarget" in script
    assert '["radio", "checkbox"].includes(inputType)' in script
    assert "isEditableShortcutTarget" not in script


def test_v2_label_page_supports_space_paging_with_autosave():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "function isRatePagingStage()" in script
    assert '["rough", "fine", "label"].includes(state.stage)' in script
    assert "function collectCurrentStagePayload()" in script
    assert 'if (state.stage === "label")' in script
    assert "labels: collectLabels()" in script
    assert "saveCurrentStageBeforePageChange" in script
    assert "await saveCurrentStageBeforePageChange()" in script
    assert "if (isScreeningStage() && /^[1-5]$/.test(event.key))" in script
    assert 'if (event.code === "Space")' in script


def test_v2_rate_paging_saves_forward_boundary_item_before_returning():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "const movingPastLastItem = nextIndex > state.index && boundedIndex === state.index;" in script
    assert "if (boundedIndex === state.index && !movingPastLastItem) return;" in script
    assert "if (!(await saveCurrentStageBeforePageChange())) return;" in script
    assert "const preferredIndex = nextIndex > state.index ? state.index + 1 : boundedIndex;" in script
    assert "await reloadCurrentStageAfterSave(preferredIndex);" in script
    assert "if (movingPastLastItem) {" in script


def test_v2_rate_paging_reloads_with_user_history_but_initial_entry_does_not():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    open_stage_start = script.index("async function openStage")
    render_current_start = script.index("function renderCurrentItem", open_stage_start)
    open_stage_body = script[open_stage_start:render_current_start]
    reload_start = script.index("async function reloadCurrentStageAfterSave")
    go_to_start = script.index("async function goToItem", reload_start)
    reload_body = script[reload_start:go_to_start]
    go_to_end = script.index("function goNextItem", go_to_start)
    go_to_body = script[go_to_start:go_to_end]

    assert "function stageItemsUrl(taskId, stage, includeHistory = false)" in script
    assert "params.set(\"include_history\", \"1\");" in script
    assert "stageItemsUrl(taskId, stage, true)" in open_stage_body
    assert "state.index = firstUnannotatedItemIndex();" in open_stage_body
    assert "stageItemsUrl(taskId, stage, true)" in reload_body
    assert "const preferredIndex = nextIndex > state.index ? state.index + 1 : boundedIndex;" in go_to_body


def test_v2_label_forward_paging_uses_local_queue_after_autosave():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    go_to_start = script.index("async function goToItem")
    go_to_end = script.index("function goNextItem", go_to_start)
    go_to_body = script[go_to_start:go_to_end]

    assert 'if (state.stage === "label")' in go_to_body
    label_branch_start = go_to_body.index('if (state.stage === "label")')
    label_branch_end = go_to_body.index("await reloadCurrentStageAfterSave", label_branch_start)
    label_branch = go_to_body[label_branch_start:label_branch_end]

    assert "function advanceCurrentStageLocally(preferredIndex)" in script
    assert "advanceCurrentStageLocally(preferredIndex);" in label_branch
    assert "reloadCurrentStageAfterSave" not in label_branch
    assert "loadTasks" not in label_branch
    assert "stageItemsUrl" not in label_branch


def test_v2_rate_previous_page_does_not_require_saving_current_item():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    go_to_start = script.index("async function goToItem")
    go_to_end = script.index("function goNextItem", go_to_start)
    go_to_body = script[go_to_start:go_to_end]
    previous_branch_start = go_to_body.index("if (nextIndex < state.index)")
    previous_branch_end = go_to_body.index("if (!(await saveCurrentStageBeforePageChange())) return;")
    previous_branch = go_to_body[previous_branch_start:previous_branch_end]

    assert "function itemHasCurrentUserAnnotation(item)" in script
    assert "function firstUnannotatedItemIndex()" in script
    assert "if (nextIndex < state.index)" in go_to_body
    assert "state.index = boundedIndex;" in previous_branch
    assert "renderCurrentItem();" in previous_branch
    assert "saveCurrentStageBeforePageChange" not in previous_branch


def test_v2_screening_forms_load_existing_user_annotation_values():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    render_start = script.index("function renderStageForm")
    render_end = script.index("function fineDefaultRecord", render_start)
    render_body = script[render_start:render_end]

    assert "const current = record.rough || {};" in render_body
    assert "const current = fineDefaultRecord(record);" in render_body
    assert "${mosField(current.mos)}" in render_body
    assert "${defectField(current.has_defect)}" in render_body
    assert "${issueCheckboxes(current.issues || [])}" in render_body
    assert 'const checked = selectedValue === score ? "checked" : "";' in script


def test_v2_save_button_refreshes_queue_without_skipping_or_wrapping():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    save_stage_start = script.index("async function saveStage")
    collect_payload_start = script.index("function collectCurrentStagePayload", save_stage_start)
    save_stage_body = script[save_stage_start:collect_payload_start]

    assert "function reloadCurrentStageAfterSave(preferredIndex)" in script
    assert "await reloadCurrentStageAfterSave(state.index);" in save_stage_body
    assert "const nextIndex = Math.min(state.index + 1" not in save_stage_body
    assert "await openStage(state.activeTask.id, state.stage);" not in save_stage_body


def test_v2_screening_auto_saves_when_paging_instead_of_save_buttons():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    rough_start = script.index('if (state.stage === "rough")')
    rough_end = script.index('if (state.stage === "fine")', rough_start)
    fine_end = script.index("const currentLabels", rough_end)
    screening_render = script[rough_start:fine_end]

    assert "保存粗筛" not in screening_render
    assert "保存精筛" not in screening_render
    assert "saveCurrentStageBeforePageChange" in script
    assert "goToItem(state.index + 1)" in script


def test_v2_frontend_supports_multi_annotator_assignment_and_round_progress():
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="roughAnnotatorCountInput"' in index_template
    assert 'id="fineAnnotatorCountInput"' in index_template
    assert 'annotator_count: Number($("roughAnnotatorCountInput").value || 1)' in script
    assert 'annotator_count: Number($("fineAnnotatorCountInput").value || 1)' in script
    assert "screeningProgressCells" in script
    assert "第 ${round.round} 人" in script
    assert "username: state.username" in script
    assert "goToItem(state.index - 1)" in script
    assert "await saveCurrentStageBeforePageChange()" in script
    assert "请选择 MOS 分后再翻页" in script


def test_v2_fine_form_defaults_to_rough_result_for_fast_acceptance():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "fineDefaultRecord(record)" in script
    assert "function fineDefaultRecord(record)" in script
    assert "return record.fine || record.rough || {};" in script
    assert "const current = fineDefaultRecord(record);" in script
    assert "goToItem(state.index + 1)" in script


def test_v2_frontend_preloads_next_three_preview_pages():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "const PRELOAD_FORWARD_PAGES = 3;" in script
    assert "const preloadedImages = new Map();" in script
    assert "function preparePreviewImage(image, previewSrc)" in script
    assert 'image.fetchPriority = "high";' in script
    assert 'preparePreviewImage($("srcImage"), item.image_urls.src);' in script
    assert 'preparePreviewImage($("dstImage"), item.image_urls.dst);' in script
    assert "function preloadStageNeighbors()" in script
    assert "function preloadNeighborItems(items, currentIndex)" in script
    assert "offset <= PRELOAD_FORWARD_PAGES" in script
    assert "preloadStageNeighbors();" in script
    assert "function preloadVisualizationNeighbors()" in script
    assert "preloadVisualizationNeighbors().catch((error) => console.warn(error));" in script
    assert "function preloadVisualizationPageImages(page)" in script


def test_v2_frontend_renders_collapsed_generation_prompt_markdown_below_images():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    visualize_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "visualize.html").read_text(encoding="utf-8")
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'generation_prompt_dir: $("generationPromptDirInput").value.trim(),' in script
    assert 'generation_prompt_dir: $("editGenerationPromptDirInput").value.trim(),' in script
    assert "function renderGenerationPromptDisclosure(item)" in script
    assert "function renderMarkdown(value)" in script
    assert "renderImagePrompt(item);" in script
    assert "renderVisualizationImagePrompt(item);" in script
    assert "imagePromptHost" in rate_template
    assert "srcPromptHost" not in rate_template
    assert "dstPromptHost" not in rate_template
    assert "visualizationPromptHost" in visualize_template
    assert "visualizationSrcPromptHost" not in visualize_template
    assert "visualizationDstPromptHost" not in visualize_template
    assert "generationPromptDirInput" in index_template
    assert "editGenerationPromptDirInput" in index_template
    assert ".promptDisclosure" in styles
    assert ".imagePromptHost" in styles
    assert "#ratingControls" in styles
    assert "#visualizationResultPanel" in styles
    assert "grid-column: 3;" in styles
    assert "grid-row: 1 / span 2;" in styles
    assert ".markdownBody" in styles


def test_v2_label_correction_uses_choice_bars_instead_of_text_inputs():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "function labelCorrectionPaths" in script
    assert "function labelOptionsForPath" in script
    assert "function labelChoiceInputs" in script
    assert "label.className = \"labelOption labelChoiceOption\"" in script
    assert "input.type = \"radio\"" in script
    assert "input.dataset.labelPath" in script
    assert "input.dataset.optionValue" in script
    assert "document.querySelectorAll(\".labelChoiceInput:checked\")" in script
    assert "JSON.parse(input.dataset.optionValue)" in script
    assert "labelPathInput" not in script
    assert "labelsJsonInput" not in script
    assert ".labelChoiceGroup" in styles
    assert ".labelChoiceGroup .labelOptions" in styles
    assert ".labelChoiceOption span" in styles
    assert "grid-template-columns: 1fr;" in styles
    assert '.labelOption input[type="radio"]' in styles


def test_v2_frontend_links_and_renders_stage_visualization_pages():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert '`/dataset/visualize/${task.id}?stage=rough`' in script
    assert '`/dataset/visualize/${task.id}?stage=fine`' in script
    assert '`/dataset/visualize/${task.id}?stage=sample`' in script
    assert '`/dataset/visualize/${task.id}?stage=label`' in script
    assert "openVisualizationPage" in script
    assert "reloadVisualizationResults" in script
    assert "renderVisualizationPage" in script
    assert "renderScreeningVisualization" in script
    assert "renderSampleVisualization" in script
    assert "renderLabelVisualization" in script


def test_v2_visualization_frontend_exposes_filter_controls():
    visualize_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "visualize.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="openVisualizationFilterBtn"' in visualize_template
    assert 'id="visualizationFilterOverlay"' in visualize_template
    assert 'id="visualizationFilterPanel"' in visualize_template
    assert 'id="visualizationFilterBody"' in visualize_template
    assert 'class="drawerOverlay hidden"' in visualize_template
    assert 'class="filterDrawer hidden"' in visualize_template
    assert 'class="panel visualizationFilterPanel hidden"' not in visualize_template
    assert "visualizationFilters" in script
    assert "visualizationFilterOptions" in script
    assert "renderVisualizationFilterPanel" in script
    assert "collectVisualizationFilters" in script
    assert "buildVisualizationFilterPayload" in script
    assert 'params.set("filters", JSON.stringify(buildVisualizationFilterPayload()))' in script
    assert '"MOS 分", visualizationFilterOptionsOrDefault(options.mos, [1, 2, 3, 4, 5]), "mos"' in script
    assert '"是否有质量问题"' in script
    assert '"标注者", options.annotators || [], "annotators"' in script
    assert 'renderVisualizationFilterGroup(dimension.name, dimension.options || [], "label", selected, path)' in script
    assert 'input.dataset.visualizationFilterType === "mos"' in script
    assert 'input.dataset.visualizationFilterType === "has_defect"' in script
    assert 'input.dataset.visualizationFilterType === "annotators"' in script
    assert 'input.dataset.visualizationFilterType === "label"' in script
    assert '$("visualizationFilterOverlay").classList.remove("hidden")' in script
    assert '$("visualizationFilterOverlay").classList.add("hidden")' in script


def test_v2_visualization_filter_options_use_v1_horizontal_bars():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'list.className = "labelOptions"' in script
    assert 'label.className = "labelOption"' in script
    assert 'list.className = "filterOptionList"' not in script
    assert 'label.className = "filterOption"' not in script
    assert '.labelOptions {' in styles
    assert '.labelOption input[type="checkbox"],\n.labelOption input[type="radio"] {' in styles
    assert '.labelOption input[type="checkbox"]:checked + span' in styles
    assert '.filterOptionList' not in styles
    assert '.filterOption {' not in styles


def test_v2_visualization_filter_bars_use_readable_fallback_options():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "visualizationFilterOptionsOrDefault(options.mos, [1, 2, 3, 4, 5])" in script
    assert "visualizationFilterOptionsOrDefault(options.has_defect, [false, true])" in script
    assert 'renderVisualizationFilterGroup("标注者", options.annotators || [], "annotators", state.visualizationFilters.annotators, null, "暂无标注者")' in script
    assert "function visualizationFilterOptionsOrDefault" in script


def test_v2_visualization_filter_bars_are_not_clipped_by_fieldset_layout():
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".filterGroup {\n  display: block;" in styles
    assert "min-height: auto;" in styles
    assert ".filterGroup legend {\n  display: block;" in styles
    assert ".labelOptions {\n  display: inline-flex;" in styles
    assert "min-height: 32px;" in styles


def test_v2_frontend_exposes_annotation_import_action():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-action="import"' in script
    assert "async function importTaskAnnotations" in script
    assert "导入 JSONL 文件路径" in script
    assert 'api(`/api/tasks/${taskId}/import`' in script
    assert 'if (action === "import") importTaskAnnotations(taskId)' in script


def test_v2_frontend_exposes_preview_cache_action():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-action="cache-previews"' in script
    assert "async function warmPreviewCache" in script
    assert 'api(`/api/tasks/${taskId}/preview-cache/jobs`, { method: "POST" })' in script
    assert 'api(`/api/tasks/${taskId}/preview-cache/jobs/${jobId}`)' in script
    assert 'if (action === "cache-previews") warmPreviewCache(taskId, target)' in script
    assert "缓存图片" in script


def test_v2_frontend_drives_dedicated_sample_page():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    sample_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "sample.html").read_text(encoding="utf-8")

    assert 'data-page="sample"' in sample_template
    assert 'id="selectAllSampleBtn"' in sample_template
    assert 'id="sampleBucketList"' in sample_template
    assert "openSamplePage" in script
    assert "reloadSampleBuckets" in script
    assert "renderSampleBuckets" in script
    assert "collectSampleSelections" in script
    assert "selectAllSampleBuckets" in script
    assert 'api(`/api/tasks/${state.taskId}/sample-buckets`' in script
    assert 'select_all: true' in script
    assert 'selections: collectSampleSelections()' in script
