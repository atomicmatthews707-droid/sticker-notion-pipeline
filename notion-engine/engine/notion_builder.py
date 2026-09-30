import time
import json
from typing import Callable, Any

def _notion_request_with_retry(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    # AI Handoff: Honor Notion's 429/529 retry_after limits. 
    # Wrapper function for all API calls.
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            status = getattr(e, 'status', None)
            if status in [429, 529] and attempt < max_retries - 1:
                body = getattr(e, 'body', {})
                retry_after = body.get('additional_data', {}).get('retry_after', 1)
                print(f"Rate limited. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                raise

def _map_block_type(btype: str) -> str:
    # AI Handoff: Fixed the bulleted_list and numbered_list bug per HANDOFF.md task 2.
    if btype == "bulleted_list": return "bulleted_list_item"
    if btype == "numbered_list": return "numbered_list_item"
    if btype in ["paragraph", "heading_1"]: return btype
    return "paragraph"  # fallback

def _to_block(section: dict) -> dict:
    # AI Handoff: Helper to build standard Notion block objects.
    btype = _map_block_type(section.get("type", "paragraph"))
    return {
        "object": "block",
        "type": btype,
        btype: {
            "rich_text": [{"text": {"content": section.get("text", "")}}]
        }
    }

def build_template(spec: dict, token: str, parent_page_id: str) -> dict:
    # AI Handoff: The core 5-pass build as mandated in HANDOFF.md task 2.
    from notion_client import Client
    
    # Pass 1: Set correct notion_version for data_sources and views.
    client = Client(auth=token, notion_version="2026-03-11")
    
    skipped = []
    database_ids = {}    # mapping name -> db_id
    data_source_ids = {} # mapping name -> ds_id
    view_ids = []
    
    # 1. Root page
    root_page_res = _notion_request_with_retry(
        client.pages.create,
        parent={"type": "page_id", "page_id": parent_page_id},
        properties={"title": {"title": [{"text": {"content": spec.get("root_page", {}).get("title", "Root Page")}}]}}
    )
    root_id = root_page_res["id"]
    root_url = root_page_res.get("url", "")
    
    # Add sections to root page via append blocks (chunked if needed, though this is simple here)
    sections = spec.get("root_page", {}).get("sections", [])
    if sections:
        blocks = [_to_block(s) for s in sections]
        for i in range(0, len(blocks), 100):
            _notion_request_with_retry(
                client.blocks.children.append,
                block_id=root_id,
                children=blocks[i:i+100]
            )

    # 2. Databases (Simple properties first)
    SIMPLE_TYPES = {"title", "rich_text", "number", "select", "multi_select", "date", "checkbox", "url", "email", "phone_number"}
    
    for db in spec.get("databases", []):
        name = db["name"]
        props_to_create = {}
        for p in db.get("properties", []):
            ptype = p["type"]
            if ptype in SIMPLE_TYPES:
                props_to_create[p["name"]] = {ptype: {}}
                
        # Must place under initial_data_source.properties as per new API spec
        db_res = _notion_request_with_retry(
            client.databases.create,
            parent={"type": "page_id", "page_id": root_id},
            title=[{"text": {"content": name}}],
            initial_data_source={"properties": props_to_create}
        )
        database_ids[name] = db_res["id"]
        # Extract the auto-created data source ID
        data_source_id = db_res.get("data_sources", [{}])[0].get("id")
        data_source_ids[name] = data_source_id

    # 3. Relations, Formulas, Rollups
    # We do these separately since they depend on relations or data source IDs
    
    def update_ds_prop(ds_id: str, db_name: str, prop_name: str, prop_schema: dict):
        try:
            _notion_request_with_retry(
                client.data_sources.update,
                data_source_id=ds_id,
                properties={prop_name: prop_schema}
            )
        except Exception as e:
            # AI Handoff: Log validation_error and skip instead of aborting build.
            skipped.append(f"Validation error on {db_name}.{prop_name}: {str(e)}")

    # a) Relations
    for db in spec.get("databases", []):
        ds_id = data_source_ids[db["name"]]
        for p in db.get("properties", []):
            if p["type"] == "relation":
                target_db = p.get("target_db")
                if target_db in data_source_ids:
                    update_ds_prop(ds_id, db["name"], p["name"], {
                        "relation": {
                            "data_source_id": data_source_ids[target_db],
                            "single_property": {}
                        }
                    })

    # b) Formulas
    for db in spec.get("databases", []):
        ds_id = data_source_ids[db["name"]]
        for p in db.get("properties", []):
            if p["type"] == "formula":
                update_ds_prop(ds_id, db["name"], p["name"], {
                    "formula": {"expression": p.get("expression")}
                })

    # c) Rollups
    for db in spec.get("databases", []):
        ds_id = data_source_ids[db["name"]]
        for p in db.get("properties", []):
            if p["type"] == "rollup":
                update_ds_prop(ds_id, db["name"], p["name"], {
                    "rollup": {
                        "relation_property_name": p.get("relation_name"),
                        "rollup_property_name": p.get("rollup_property_name"),
                        "function": p.get("function")
                    }
                })

    # 4. Views
    for db in spec.get("databases", []):
        ds_id = data_source_ids[db["name"]]
        db_id = database_ids[db["name"]]
        
        # Retrieve data source to map names to property IDs and types
        try:
            ds_res = _notion_request_with_retry(client.data_sources.retrieve, data_source_id=ds_id)
            ds_props = ds_res.get("properties", {})
            # Map property name to its actual ID (stored in val['id']) and type
            name_to_info = {
                val["name"]: {"id": val.get("id", key), "type": val.get("type", "select")}
                for key, val in ds_props.items()
            }
        except Exception as e:
            skipped.append(f"Failed to fetch data source properties for {db['name']}: {str(e)}")
            name_to_info = {}

        for view in db.get("views", []):
            vtype = view.get("type")
            vname = view.get("name")
            
            kwargs = {
                "database_id": db_id,
                "data_source_id": ds_id,
                "name": vname,
                "type": vtype
            }
            
            # Additional configuration verified against live 2026-03-11 Notion API
            if vtype == "board":
                gb = view.get("group_by_property")
                prop_info = name_to_info.get(gb)
                if not prop_info:
                    skipped.append(f"Skipping view '{vname}' because group_by_property '{gb}' not found.")
                    continue
                kwargs["configuration"] = {
                    "type": "board",
                    "group_by": {
                        "type": prop_info["type"],
                        "property_id": prop_info["id"],
                        "sort": {"type": "manual"}
                    }
                }
            elif vtype == "calendar":
                dp = view.get("date_property")
                prop_info = name_to_info.get(dp)
                if not prop_info:
                    skipped.append(f"Skipping view '{vname}' because date_property '{dp}' not found.")
                    continue
                kwargs["configuration"] = {
                    "type": "calendar",
                    "date_property_id": prop_info["id"]
                }

            try:
                view_res = _notion_request_with_retry(
                    client.views.create,
                    **kwargs
                )
                view_ids.append(view_res["id"])
            except Exception as e:
                skipped.append(f"Failed to create view '{vname}' in {db['name']}: {str(e)}")

    # 5. Sample Rows & Sub-pages
    # Track page IDs for relation resolution: {db_name: {row_title: page_id}}
    created_page_ids = {}

    for db in spec.get("databases", []):
        db_name = db["name"]
        ds_id = data_source_ids[db_name]
        prop_type_map = {p["name"]: p["type"] for p in db.get("properties", [])}
        created_page_ids[db_name] = {}

        for row in db.get("sample_rows", []):
            row_props = {}
            row_title = ""
            for k, v in row.items():
                ptype = prop_type_map.get(k)
                if ptype == "title" or (not ptype and k.lower() in ("name", "title")):
                    row_props[k] = {"title": [{"text": {"content": str(v)}}]}
                    row_title = str(v)
                elif ptype == "rich_text":
                    row_props[k] = {"rich_text": [{"text": {"content": str(v)}}]}
                elif ptype == "select":
                    row_props[k] = {"select": {"name": str(v)}}
                elif ptype == "multi_select":
                    vals = v if isinstance(v, list) else [v]
                    row_props[k] = {"multi_select": [{"name": str(x)} for x in vals]}
                elif ptype == "number":
                    num_val = float(v) if "." in str(v) else int(v)
                    row_props[k] = {"number": num_val}
                elif ptype == "checkbox":
                    row_props[k] = {"checkbox": bool(v)}
                elif ptype == "date":
                    row_props[k] = {"date": {"start": str(v)}}
                elif ptype == "email":
                    row_props[k] = {"email": str(v)}
                elif ptype == "url":
                    row_props[k] = {"url": str(v)}
                elif ptype == "phone_number":
                    row_props[k] = {"phone_number": str(v)}
                elif ptype == "relation":
                    # Attempt to resolve relation from previously created pages in other databases
                    matched_id = None
                    for tdb, pages in created_page_ids.items():
                        if str(v) in pages:
                            matched_id = pages[str(v)]
                            break
                    if matched_id:
                        row_props[k] = {"relation": [{"id": matched_id}]}
                elif ptype in ("formula", "rollup"):
                    # Formulas and rollups are computed automatically by Notion — do not set directly
                    continue
                else:
                    row_props[k] = {"rich_text": [{"text": {"content": str(v)}}]}
                
            try:
                page_res = _notion_request_with_retry(
                    client.pages.create,
                    parent={"type": "data_source_id", "data_source_id": ds_id},
                    properties=row_props
                )
                if row_title and page_res and "id" in page_res:
                    created_page_ids[db_name][row_title] = page_res["id"]
            except Exception as e:
                skipped.append(f"Sample row error in {db_name}: {str(e)}")

    for sp in spec.get("sub_pages", []):
        try:
            sp_res = _notion_request_with_retry(
                client.pages.create,
                parent={"type": "page_id", "page_id": root_id},
                properties={"title": {"title": [{"text": {"content": sp.get("title", "Sub-page")}}]}}
            )
            sp_id = sp_res["id"]
            sections = sp.get("sections", [])
            if sections:
                blocks = [_to_block(s) for s in sections]
                for i in range(0, len(blocks), 100):
                    _notion_request_with_retry(
                        client.blocks.children.append,
                        block_id=sp_id,
                        children=blocks[i:i+100]
                    )
        except Exception as e:
            skipped.append(f"Sub-page error {sp.get('title')}: {str(e)}")
            
    return {
        "root_url": root_url,
        "root_id": root_id,
        "database_ids": list(database_ids.values()),
        "data_source_ids": list(data_source_ids.values()),
        "view_ids": view_ids,
        "skipped": skipped
    }
