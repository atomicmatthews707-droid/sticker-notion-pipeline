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
                client.request,
                path=f"v1/data_sources/{ds_id}",
                method="PATCH",
                body={"properties": {prop_name: prop_schema}}
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
                        "relation": {"data_source_id": data_source_ids[target_db]}
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
        
        # Retrieve data source to map names to property IDs
        try:
            ds_res = _notion_request_with_retry(client.request, path=f"v1/data_sources/{ds_id}", method="GET")
            ds_props = ds_res.get("properties", {})
            name_to_id = {val["name"]: key for key, val in ds_props.items()}
        except Exception as e:
            skipped.append(f"Failed to fetch data source properties for {db['name']}: {str(e)}")
            name_to_id = {}

        for view in db.get("views", []):
            vtype = view.get("type")
            vname = view.get("name")
            
            body = {
                "database_id": db_id,
                "data_source_id": ds_id,
                "name": vname,
                "type": vtype
            }
            
            # Additional configuration for board/calendar
            if vtype == "board":
                gb = view.get("group_by_property")
                prop_id = name_to_id.get(gb)
                if not prop_id:
                    skipped.append(f"Skipping view '{vname}' because group_by_property '{gb}' ID not found.")
                    continue
                body["configuration"] = {"group_by": {"property_id": prop_id}}
            elif vtype == "calendar":
                dp = view.get("date_property")
                prop_id = name_to_id.get(dp)
                if not prop_id:
                    skipped.append(f"Skipping view '{vname}' because date_property '{dp}' ID not found.")
                    continue
                body["configuration"] = {"date_property_id": prop_id}

            try:
                view_res = _notion_request_with_retry(
                    client.request,
                    path="v1/views",
                    method="POST",
                    body=body
                )
                view_ids.append(view_res["id"])
            except Exception as e:
                skipped.append(f"Failed to create view '{vname}' in {db['name']}: {str(e)}")

    # 5. Sample Rows & Sub-pages
    for db in spec.get("databases", []):
        ds_id = data_source_ids[db["name"]]
        for row in db.get("sample_rows", []):
            row_props = {}
            for k, v in row.items():
                # We do simple property types mapping here for demonstration.
                # A robust implementation would look at property types from the spec.
                # In dry-run we just bypass, so this is minimal best-effort mapping for texts.
                row_props[k] = {"title": [{"text": {"content": str(v)}}]} if k.lower() == "name" or k.lower() == "title" else {"rich_text": [{"text": {"content": str(v)}}]}
                
            try:
                _notion_request_with_retry(
                    client.pages.create,
                    parent={"type": "data_source_id", "data_source_id": ds_id},
                    properties=row_props
                )
            except Exception as e:
                skipped.append(f"Sample row error in {db['name']}: {str(e)}")

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
