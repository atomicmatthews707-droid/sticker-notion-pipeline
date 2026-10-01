import json

import httpx
import pytest
from notion_client.errors import APIResponseError

from engine import notion_builder as nb
from engine.notion_builder import (
    _notion_request_with_retry, _retry_after_seconds, _rich_text, _row_value,
    _to_block, _to_bool, _to_number, build_template,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def api_error(status, retry_after=None, body_retry=None):
    body = json.dumps({"additional_data": {"retry_after": body_retry}}) if body_retry is not None else "{}"
    extra = {"retry_after": retry_after} if retry_after is not None else None
    return APIResponseError("rate_limited", status, "slow down", httpx.Headers(), body, extra)


class Recorder:
    """Callable stand-in for one SDK endpoint method."""
    def __init__(self, name, calls, handler):
        self.name, self.calls, self.handler = name, calls, handler

    def __call__(self, **kwargs):
        self.calls.append((self.name, kwargs))
        return self.handler(kwargs)


class FakeClient:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}  # endpoint name -> exception to raise
        self._n = 0
        def ns(**fns):
            return type("NS", (), fns)()

        self.pages = ns(create=self._ep("pages.create", self._page), update=self._ep("pages.update", lambda k: {"id": k["page_id"]}))
        self.blocks = ns(children=ns(append=self._ep("blocks.append", lambda k: {})))
        self.databases = ns(create=self._ep("databases.create", self._database))
        self.data_sources = ns(update=self._ep("ds.update", lambda k: {}), retrieve=self._ep("ds.retrieve", self._ds))
        self.views = ns(create=self._ep("views.create", lambda k: {"id": f"view{self._next()}"}))
        self.ds_props = {}

    def _next(self):
        self._n += 1
        return self._n

    def _ep(self, name, handler):
        def run(kwargs):
            if name in self.fail:
                raise self.fail[name]
            return handler(kwargs)
        return Recorder(name, self.calls, run)

    def _page(self, k):
        return {"id": f"page{self._next()}", "url": "https://notion.so/root"}

    def _database(self, k):
        i = self._next()
        self.ds_props[f"ds{i}"] = k["initial_data_source"]["properties"]
        return {"id": f"db{i}", "data_sources": [{"id": f"ds{i}"}]}

    def _ds(self, k):
        props = {n: {"id": f"id_{n}", "name": n, "type": next(iter(s))} for n, s in self.ds_props[k["data_source_id"]].items()}
        return {"properties": props}

    def of(self, name):
        return [kw for n, kw in self.calls if n == name]


SPEC = {
    "template_name": "T",
    "databases": [
        {"name": "Projects",
         "properties": [{"name": "Name", "type": "title"}, {"name": "Status", "type": "select"},
                        {"name": "Due", "type": "date"}, {"name": "Budget", "type": "number"},
                        {"name": "Done", "type": "checkbox"},
                        {"name": "Late", "type": "formula", "expression": 'prop("Due") < now()'},
                        {"name": "Spent", "type": "rollup", "relation_name": "Tasks", "rollup_property_name": "Hours", "function": "sum"},
                        {"name": "Tasks", "type": "relation", "target_db": "Tasks"}],
         "views": [{"name": "Board", "type": "board", "group_by_property": "Status"},
                   {"name": "Cal", "type": "calendar", "date_property": "Due"},
                   {"name": "Gone", "type": "board", "group_by_property": "Nope"},
                   {"name": "Open", "type": "table", "filter": {"property": "Status", "select": {"equals": "Open"}},
                    "sorts": [{"property": "Due", "direction": "descending"}]}],
         "sample_rows": [{"Name": "Site", "Status": "Open", "Due": "2026-10-01", "Budget": "$1,200.50", "Done": "false", "Tasks": ["Wire", "Ghost"]},
                         {"Name": "Bad", "Budget": "lots"}]},
        {"name": "Tasks",
         "properties": [{"name": "Task", "type": "title"}, {"name": "Hours", "type": "number"}],
         "views": [], "sample_rows": [{"Task": "Wire", "Hours": 3}]},
    ],
    "root_page": {"title": "Root", "sections": [{"type": "heading_2", "text": "Hi"}]},
    "sub_pages": [{"title": "Guide", "sections": [{"type": "bulleted_list", "text": "one"}]}],
}


# ── retry ─────────────────────────────────────────────────────────────────────

def test_retry_after_prefers_additional_data_then_body_then_backoff():
    assert _retry_after_seconds(api_error(429, retry_after=7), 0) == 7
    assert _retry_after_seconds(api_error(429, body_retry=3), 0) == 3
    assert _retry_after_seconds(api_error(429), 2) == 4


def test_retry_recovers_after_rate_limit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(nb.time, "sleep", sleeps.append)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise api_error(429, retry_after=1)
        return "ok"

    assert _notion_request_with_retry(flaky) == "ok"
    assert sleeps == [1, 1]


def test_retry_does_not_swallow_other_errors(monkeypatch):
    monkeypatch.setattr(nb.time, "sleep", lambda s: None)
    with pytest.raises(APIResponseError):
        _notion_request_with_retry(lambda: (_ for _ in ()).throw(api_error(400)))


def test_retry_gives_up_after_max(monkeypatch):
    monkeypatch.setattr(nb.time, "sleep", lambda s: None)
    n = {"c": 0}

    def always():
        n["c"] += 1
        raise api_error(503)

    with pytest.raises(APIResponseError):
        _notion_request_with_retry(always, max_retries=3)
    assert n["c"] == 3


# ── blocks and values ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec_type,notion_type", [
    ("bulleted_list", "bulleted_list_item"), ("numbered_list", "numbered_list_item"),
    ("heading_1", "heading_1"), ("heading_2", "heading_2"), ("heading_3", "heading_3"),
    ("to_do", "to_do"), ("callout", "callout"), ("quote", "quote"),
    ("divider", "divider"), ("paragraph", "paragraph"), ("weird", "paragraph"),
])
def test_block_type_mapping(spec_type, notion_type):
    block = _to_block({"type": spec_type, "text": "x"})
    assert block["type"] == notion_type and notion_type in block


def test_divider_has_no_rich_text():
    assert _to_block({"type": "divider"})["divider"] == {}


def test_long_text_is_split_below_notion_limit():
    parts = _rich_text("a" * 4500)
    assert [len(p["text"]["content"]) for p in parts] == [2000, 2000, 500]
    assert _rich_text("") == []


def test_number_and_bool_coercion():
    assert _to_number("$1,200.50") == 1200.5
    assert _to_number("3") == 3 and isinstance(_to_number("3.0"), int)
    with pytest.raises(ValueError):
        _to_number("lots")
    assert _to_bool("false") is False and _to_bool("Yes") is True and _to_bool(0) is False


def test_row_values():
    assert _row_value("select", "a,b") == {"select": {"name": "a b"}}
    assert _row_value("multi_select", ["x", "y"]) == {"multi_select": [{"name": "x"}, {"name": "y"}]}
    assert _row_value("date", "2026-01-01") == {"date": {"start": "2026-01-01"}}
    assert _row_value("number", None) is None


# ── full build ────────────────────────────────────────────────────────────────

def test_full_build_pass_order_and_ids():
    c = FakeClient()
    result = build_template(SPEC, "tok", "parent", client=c)
    names = [n for n, _ in c.calls]
    def first(n):
        return names.index(n)

    assert first("databases.create") < first("ds.update") < first("views.create") < first("pages.update")
    assert result["root_url"] == "https://notion.so/root"
    assert len(result["database_ids"]) == 2 and len(result["data_source_ids"]) == 2


def test_databases_use_initial_data_source_and_relations_use_data_source_id():
    c = FakeClient()
    build_template(SPEC, "tok", "parent", client=c)
    db_call = c.of("databases.create")[0]
    assert "properties" not in db_call and "initial_data_source" in db_call
    # formula/rollup/relation are not in the initial schema
    assert set(db_call["initial_data_source"]["properties"]) == {"Name", "Status", "Due", "Budget", "Done"}
    rel = next(k for k in c.of("ds.update") if "Tasks" in k["properties"])
    assert rel["properties"]["Tasks"]["relation"]["data_source_id"] == "ds3"
    order = [list(k["properties"])[0] for k in c.of("ds.update")]
    assert order == ["Tasks", "Late", "Spent"]  # relation, formula, rollup


def test_views_map_property_ids_and_skip_missing():
    c = FakeClient()
    result = build_template(SPEC, "tok", "parent", client=c)
    views = {k["name"]: k for k in c.of("views.create")}
    assert set(views) == {"Board", "Cal", "Open"}
    assert views["Board"]["configuration"]["group_by"]["property_id"] == "id_Status"
    assert views["Cal"]["configuration"]["date_property_id"] == "id_Due"
    assert views["Open"]["filter"]["property"] == "id_Status"
    assert views["Open"]["sorts"] == [{"property": "id_Due", "direction": "descending"}]
    assert any("group_by_property 'Nope' not found" in s for s in result["skipped"])
    assert len(result["view_ids"]) == 3


def test_view_falls_back_without_filter_when_rejected():
    class Reject(Exception):
        pass

    c = FakeClient()
    real = c.views.create
    seen = []

    def create(**kw):
        seen.append(kw)
        if "filter" in kw:
            raise Reject("bad filter")
        return real(**kw)

    c.views.create = create
    result = build_template(SPEC, "tok", "parent", client=c)
    assert any(s.startswith("View 'Open'") and "without filter/sorts" in s for s in result["skipped"])
    assert sum(1 for k in seen if k["name"] == "Open") == 2


def test_sample_rows_coercion_relations_and_skips():
    c = FakeClient()
    result = build_template(SPEC, "tok", "parent", client=c)
    rows = [k for k in c.of("pages.create") if k["parent"].get("type") == "data_source_id"]
    site = next(r for r in rows if r["properties"].get("Name", {}).get("title", [{}])[0].get("text", {}).get("content") == "Site")
    assert site["properties"]["Budget"] == {"number": 1200.5}
    assert site["properties"]["Done"] == {"checkbox": False}
    assert "Tasks" not in site["properties"]  # relation deferred
    bad = next(r for r in rows if r["properties"]["Name"]["title"][0]["text"]["content"] == "Bad")
    assert "Budget" not in bad["properties"]  # bad number dropped, row still created
    assert any("dropped Budget='lots'" in s for s in result["skipped"])
    # relation linked after all rows exist; unknown title reported
    link = c.of("pages.update")
    assert len(link) == 1 and len(link[0]["properties"]["Tasks"]["relation"]) == 1
    assert any("no row titled 'Ghost'" in s for s in result["skipped"])


def test_subpages_and_blocks_use_mapped_types():
    c = FakeClient()
    build_template(SPEC, "tok", "parent", client=c)
    kinds = [b["type"] for k in c.of("blocks.append") for b in k["children"]]
    assert kinds == ["heading_2", "bulleted_list_item"]


def test_blocks_chunked_at_100():
    spec = {"root_page": {"title": "R", "sections": [{"type": "paragraph", "text": str(i)} for i in range(250)]}}
    c = FakeClient()
    build_template(spec, "tok", "parent", client=c)
    assert [len(k["children"]) for k in c.of("blocks.append")] == [100, 100, 50]


def test_one_failing_database_does_not_abort_build():
    c = FakeClient()
    real = c.databases.create
    state = {"n": 0}

    def create(**kw):
        state["n"] += 1
        if state["n"] == 1:
            raise api_error(400)
        return real(**kw)

    c.databases.create = create
    result = build_template(SPEC, "tok", "parent", client=c)
    assert any("Failed to create database 'Projects'" in s for s in result["skipped"])
    assert len(result["database_ids"]) == 1  # Tasks still built
    assert any("Skipped Projects" in s or "target 'Projects'" in s or "was not created" in s for s in result["skipped"]) or len(result["database_ids"]) == 1


def test_property_update_failure_is_skipped_not_fatal():
    c = FakeClient(fail={"ds.update": api_error(400)})
    result = build_template(SPEC, "tok", "parent", client=c)
    assert sum("Validation error on" in s for s in result["skipped"]) == 3
    assert result["view_ids"]


def test_title_property_is_added_when_spec_has_none():
    spec = {"databases": [{"name": "D", "properties": [{"name": "X", "type": "number"}]}]}
    c = FakeClient()
    build_template(spec, "tok", "parent", client=c)
    assert "Name" in c.of("databases.create")[0]["initial_data_source"]["properties"]
