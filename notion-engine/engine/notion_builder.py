import json
import time
from typing import Any, Callable, Optional

NOTION_VERSION = "2026-03-11"
MAX_TEXT_LEN = 2000       # Notion's per-rich_text-object limit
MAX_BLOCKS_PER_REQUEST = 100
SIMPLE_TYPES = {
    "title", "rich_text", "number", "select", "multi_select",
    "date", "checkbox", "url", "email", "phone_number",
}
_RETRY_STATUSES = {429, 502, 503, 504, 529}


# ── Retry ─────────────────────────────────────────────────────────────────────

def _retry_after_seconds(error: Exception, attempt: int) -> float:
    """Seconds to wait before retrying: Notion's retry_after, then Retry-After, then backoff."""
    extra = getattr(error, "additional_data", None)
    if isinstance(extra, dict) and extra.get("retry_after") is not None:
        try:
            return float(extra["retry_after"])
        except (TypeError, ValueError):
            pass
    body = getattr(error, "body", None)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    if isinstance(body, dict):
        value = (body.get("additional_data") or {}).get("retry_after")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    headers = getattr(error, "headers", None)
    if headers is not None:
        try:
            return float(headers.get("Retry-After"))
        except (TypeError, ValueError):
            pass
    return float(min(2 ** attempt, 30))


def _notion_request_with_retry(fn: Callable, *args: Any, max_retries: int = 5, **kwargs: Any) -> Any:
    """Call a Notion SDK function, honouring 429/5xx retry-after hints."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            status = getattr(e, "status", None)
            if status in _RETRY_STATUSES and attempt < max_retries - 1:
                wait = _retry_after_seconds(e, attempt)
                print(f"Notion returned {status}. Retrying in {wait:g}s.")
                time.sleep(wait)
            else:
                raise


# ── Blocks ────────────────────────────────────────────────────────────────────

_BLOCK_TYPE_MAP = {
    "paragraph": "paragraph",
    "heading_1": "heading_1",
    "heading_2": "heading_2",
    "heading_3": "heading_3",
    "bulleted_list": "bulleted_list_item",
    "bulleted_list_item": "bulleted_list_item",
    "numbered_list": "numbered_list_item",
    "numbered_list_item": "numbered_list_item",
    "to_do": "to_do",
    "callout": "callout",
    "quote": "quote",
    "divider": "divider",
}


def _map_block_type(btype: str) -> str:
    return _BLOCK_TYPE_MAP.get(btype, "paragraph")


def _rich_text(text: str) -> list:
    """Split text into <=2000-char rich_text objects (Notion rejects longer ones)."""
    text = str(text)
    if not text:
        return []
    return [
        {"type": "text", "text": {"content": text[i : i + MAX_TEXT_LEN]}}
        for i in range(0, len(text), MAX_TEXT_LEN)
    ]


def _to_block(section: dict) -> dict:
    """Convert a spec section into a Notion block object."""
    btype = _map_block_type(section.get("type", "paragraph"))
    block: dict = {"object": "block", "type": btype}
    if btype == "divider":
        block["divider"] = {}
    elif btype == "to_do":
        block["to_do"] = {"rich_text": _rich_text(section.get("text", "")), "checked": bool(section.get("checked", False))}
    else:
        block[btype] = {"rich_text": _rich_text(section.get("text", ""))}
    return block


def _append_blocks(client: Any, page_id: str, sections: list) -> None:
    blocks = [_to_block(s) for s in sections]
    for i in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST):
        _notion_request_with_retry(
            client.blocks.children.append,
            block_id=page_id,
            children=blocks[i : i + MAX_BLOCKS_PER_REQUEST],
        )


# ── Sample-row value conversion ───────────────────────────────────────────────

def _to_number(v: Any) -> float | int:
    if isinstance(v, bool):
        raise ValueError(f"not a number: {v!r}")
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "").lstrip("$€£")
    f = float(s)
    return int(f) if f.is_integer() else f


def _to_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "checked", "x")
    return bool(v)


def _row_value(ptype: str, v: Any) -> Optional[dict]:
    """Convert one spec value to a Notion property value, or None to omit it."""
    if v is None or v == "":
        return None
    if ptype == "title":
        return {"title": _rich_text(v)}
    if ptype == "rich_text":
        return {"rich_text": _rich_text(v)}
    if ptype == "select":
        return {"select": {"name": str(v).replace(",", " ")}}
    if ptype == "multi_select":
        vals = v if isinstance(v, list) else [v]
        return {"multi_select": [{"name": str(x).replace(",", " ")} for x in vals]}
    if ptype == "number":
        return {"number": _to_number(v)}
    if ptype == "checkbox":
        return {"checkbox": _to_bool(v)}
    if ptype == "date":
        return {"date": {"start": str(v)}}
    if ptype in ("email", "url", "phone_number"):
        return {ptype: str(v)}
    return {"rich_text": _rich_text(v)}


# ── Views ─────────────────────────────────────────────────────────────────────

def _map_filter(flt: dict, name_to_info: dict) -> Optional[dict]:
    info = name_to_info.get(flt.get("property"))
    if not info:
        return None
    mapped = dict(flt)
    mapped["property"] = info["id"]
    return mapped


def _view_kwargs(view: dict, db_id: str, ds_id: str, name_to_info: dict, skipped: list) -> Optional[dict]:
    """Build the POST /v1/views body from a spec view, or None to skip it."""
    vtype, vname = view.get("type"), view.get("name")
    kwargs: dict = {"database_id": db_id, "data_source_id": ds_id, "name": vname, "type": vtype}

    if vtype == "board":
        gb = view.get("group_by_property")
        info = name_to_info.get(gb)
        if not info:
            skipped.append(f"Skipping view '{vname}' because group_by_property '{gb}' not found.")
            return None
        kwargs["configuration"] = {
            "type": "board",
            "group_by": {"type": info["type"], "property_id": info["id"], "sort": {"type": "manual"}},
        }
    elif vtype in ("calendar", "timeline"):
        dp = view.get("date_property")
        info = name_to_info.get(dp)
        if not info:
            skipped.append(f"Skipping view '{vname}' because date_property '{dp}' not found.")
            return None
        kwargs["configuration"] = {"type": vtype, "date_property_id": info["id"]}

    if view.get("filter"):
        mapped = _map_filter(view["filter"], name_to_info)
        if mapped:
            kwargs["filter"] = mapped
        else:
            skipped.append(f"View '{vname}': dropped filter on unknown property '{view['filter'].get('property')}'.")
    sorts = []
    for s in view.get("sorts") or []:
        info = name_to_info.get(s.get("property"))
        if info:
            sorts.append({"property": info["id"], "direction": s.get("direction", "ascending")})
        else:
            skipped.append(f"View '{vname}': dropped sort on unknown property '{s.get('property')}'.")
    if sorts:
        kwargs["sorts"] = sorts
    return kwargs


# ── Build ─────────────────────────────────────────────────────────────────────

def build_template(spec: dict, token: str, parent_page_id: str, client: Any = None) -> dict:
    """Five-pass build of a template spec in Notion. Never aborts after the root page exists."""
    if client is None:
        from notion_client import Client

        client = Client(auth=token, notion_version=NOTION_VERSION)

    skipped: list = []
    database_ids: dict = {}     # db name -> database id
    data_source_ids: dict = {}  # db name -> data source id
    view_ids: list = []
    databases = spec.get("databases", [])
    spec_title = spec.get("root_page", {}).get("title") or spec.get("template_name", "Root Page")

    # Pass 1: root page
    root = _notion_request_with_retry(
        client.pages.create,
        parent={"type": "page_id", "page_id": parent_page_id},
        properties={"title": {"title": _rich_text(spec_title)}},
    )
    root_id, root_url = root["id"], root.get("url", "")
    sections = spec.get("root_page", {}).get("sections", [])
    if sections:
        try:
            _append_blocks(client, root_id, sections)
        except Exception as e:
            skipped.append(f"Root page content error: {e}")

    # Pass 2: databases with every simple property
    for db in databases:
        name = db["name"]
        props = {
            p["name"]: {p["type"]: {}}
            for p in db.get("properties", [])
            if p.get("type") in SIMPLE_TYPES
        }
        if not any("title" in v for v in props.values()):
            props["Name"] = {"title": {}}
        try:
            res = _notion_request_with_retry(
                client.databases.create,
                parent={"type": "page_id", "page_id": root_id},
                title=_rich_text(name),
                initial_data_source={"properties": props},
            )
            database_ids[name] = res["id"]
            sources = res.get("data_sources") or []
            if sources:
                data_source_ids[name] = sources[0]["id"]
            else:
                skipped.append(f"Database '{name}' was created but returned no data source.")
        except Exception as e:
            skipped.append(f"Failed to create database '{name}': {e}")

    def update_prop(db_name: str, prop_name: str, schema: dict) -> None:
        ds_id = data_source_ids.get(db_name)
        if not ds_id:
            skipped.append(f"Skipped {db_name}.{prop_name}: database was not created.")
            return
        try:
            _notion_request_with_retry(
                client.data_sources.update, data_source_id=ds_id, properties={prop_name: schema}
            )
        except Exception as e:
            skipped.append(f"Validation error on {db_name}.{prop_name}: {e}")

    # Pass 3: relations, then formulas, then rollups (each depends on the previous)
    for db in databases:
        for p in db.get("properties", []):
            if p.get("type") != "relation":
                continue
            target = p.get("target_db")
            if target not in data_source_ids:
                skipped.append(f"Relation {db['name']}.{p['name']}: target '{target}' was not created.")
                continue
            update_prop(db["name"], p["name"], {
                "relation": {"data_source_id": data_source_ids[target], "single_property": {}}
            })
    for db in databases:
        for p in db.get("properties", []):
            if p.get("type") == "formula":
                update_prop(db["name"], p["name"], {"formula": {"expression": p.get("expression")}})
    for db in databases:
        for p in db.get("properties", []):
            if p.get("type") == "rollup":
                update_prop(db["name"], p["name"], {"rollup": {
                    "relation_property_name": p.get("relation_name"),
                    "rollup_property_name": p.get("rollup_property_name"),
                    "function": p.get("function"),
                }})

    # Pass 4: views (map property names to IDs first)
    for db in databases:
        name = db["name"]
        if name not in data_source_ids:
            continue
        try:
            ds = _notion_request_with_retry(client.data_sources.retrieve, data_source_id=data_source_ids[name])
            name_to_info = {
                v["name"]: {"id": v.get("id", k), "type": v.get("type", "select")}
                for k, v in ds.get("properties", {}).items()
            }
        except Exception as e:
            skipped.append(f"Failed to fetch data source properties for {name}: {e}")
            name_to_info = {}

        for view in db.get("views", []):
            kwargs = _view_kwargs(view, database_ids[name], data_source_ids[name], name_to_info, skipped)
            if kwargs is None:
                continue
            try:
                view_ids.append(_notion_request_with_retry(client.views.create, **kwargs)["id"])
            except Exception as e:
                # Retry once without optional filter/sorts so a bad filter cannot cost the whole view.
                if "filter" in kwargs or "sorts" in kwargs:
                    bare = {k: v for k, v in kwargs.items() if k not in ("filter", "sorts")}
                    try:
                        view_ids.append(_notion_request_with_retry(client.views.create, **bare)["id"])
                        skipped.append(f"View '{view.get('name')}' in {name} created without filter/sorts: {e}")
                        continue
                    except Exception as e2:
                        e = e2
                skipped.append(f"Failed to create view '{view.get('name')}' in {name}: {e}")

    # Pass 5a: sample rows (relations deferred until every row exists)
    created: dict = {}        # db name -> {row title: page id}
    pending_relations: list = []  # (page_id, property name, target db, [titles])
    for db in databases:
        name = db["name"]
        created[name] = {}
        ds_id = data_source_ids.get(name)
        if not ds_id:
            continue
        ptypes = {p["name"]: p["type"] for p in db.get("properties", [])}
        targets = {p["name"]: p.get("target_db") for p in db.get("properties", []) if p.get("type") == "relation"}
        title_prop = next((n for n, t in ptypes.items() if t == "title"), "Name")

        for row in db.get("sample_rows", []):
            row_props: dict = {}
            row_title = str(row.get(title_prop, ""))
            rels: list = []
            for key, value in row.items():
                ptype = ptypes.get(key)
                if ptype in ("formula", "rollup") or ptype is None:
                    continue
                if ptype == "relation":
                    titles = value if isinstance(value, list) else [value]
                    rels.append((key, targets.get(key), [str(t) for t in titles if t]))
                    continue
                try:
                    converted = _row_value(ptype, value)
                except (ValueError, TypeError) as e:
                    skipped.append(f"Sample row '{row_title}' in {name}: dropped {key}={value!r} ({e})")
                    continue
                if converted is not None:
                    row_props[key] = converted
            try:
                page = _notion_request_with_retry(
                    client.pages.create,
                    parent={"type": "data_source_id", "data_source_id": ds_id},
                    properties=row_props,
                )
                if row_title and "id" in page:
                    created[name][row_title] = page["id"]
                for key, target, titles in rels:
                    pending_relations.append((page["id"], key, target, titles))
            except Exception as e:
                skipped.append(f"Sample row error in {name} ('{row_title}'): {e}")

    # Pass 5b: link relations now that every target row exists
    for page_id, key, target, titles in pending_relations:
        ids = [created.get(target, {}).get(t) for t in titles]
        missing = [t for t, i in zip(titles, ids) if not i]
        for t in missing:
            skipped.append(f"Relation '{key}': no row titled '{t}' in '{target}'.")
        ids = [i for i in ids if i]
        if not ids:
            continue
        try:
            _notion_request_with_retry(
                client.pages.update, page_id=page_id,
                properties={key: {"relation": [{"id": i} for i in ids]}},
            )
        except Exception as e:
            skipped.append(f"Failed to link relation '{key}': {e}")

    # Pass 5c: sub-pages
    for sp in spec.get("sub_pages", []):
        try:
            res = _notion_request_with_retry(
                client.pages.create,
                parent={"type": "page_id", "page_id": root_id},
                properties={"title": {"title": _rich_text(sp.get("title", "Sub-page"))}},
            )
            if sp.get("sections"):
                _append_blocks(client, res["id"], sp["sections"])
        except Exception as e:
            skipped.append(f"Sub-page error {sp.get('title')}: {e}")

    return {
        "root_url": root_url,
        "root_id": root_id,
        "database_ids": list(database_ids.values()),
        "data_source_ids": list(data_source_ids.values()),
        "view_ids": view_ids,
        "skipped": skipped,
    }
