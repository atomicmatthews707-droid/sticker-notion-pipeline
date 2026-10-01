import os
import re

from engine.llm import generate_json

SIMPLE_TYPES = {
    "title", "rich_text", "number", "select", "multi_select",
    "date", "checkbox", "url", "email", "phone_number",
}
COMPUTED_TYPES = {"relation", "formula", "rollup"}
PROPERTY_TYPES = SIMPLE_TYPES | COMPUTED_TYPES
VIEW_TYPES = {"table", "board", "list", "calendar", "timeline", "gallery"}
BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list",
    "numbered_list", "to_do", "callout", "quote", "divider",
}
GROUPABLE_TYPES = {"select", "status", "multi_select"}
ROLLUP_FUNCTIONS = {
    "count", "count_values", "empty", "not_empty", "unique", "show_unique",
    "percent_empty", "percent_not_empty", "sum", "average", "median", "min",
    "max", "range", "show_original", "checked", "unchecked", "percent_checked",
    "earliest_date", "latest_date", "date_range",
}
_PROP_REF = re.compile(r'prop\(\s*"([^"]+)"\s*\)')


def _validate_sections(sections, where: str, errors: list) -> None:
    if not isinstance(sections, list):
        errors.append(f"{where}: 'sections' must be a list.")
        return
    for s in sections:
        if not isinstance(s, dict) or s.get("type") not in BLOCK_TYPES:
            errors.append(f"{where}: invalid section {s!r}; type must be one of {sorted(BLOCK_TYPES)}.")


def _validate_spec(spec: dict) -> list[str]:
    """Collect every problem with a spec; an empty list means it is buildable."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["Spec must be a JSON object."]

    databases = spec.get("databases", [])
    if not isinstance(databases, list):
        return ["'databases' must be a list."]

    db_props: dict[str, dict[str, str]] = {}
    seen_dbs: set = set()
    for db in databases:
        name = db.get("name")
        if not name:
            errors.append("Every database needs a non-empty 'name'.")
            continue
        if name in seen_dbs:
            errors.append(f"Database name '{name}' is duplicated.")
        seen_dbs.add(name)
        types: dict[str, str] = {}
        for prop in db.get("properties", []):
            pname = prop.get("name")
            if pname in types:
                errors.append(f"Property name conflict in db '{name}': '{pname}' is duplicated.")
            types[pname] = prop.get("type")
        db_props[name] = types

    for db in databases:
        dbname = db.get("name")
        if not dbname:
            continue
        props = db_props[dbname]

        for prop in db.get("properties", []):
            pname, ptype = prop.get("name"), prop.get("type")
            if not pname:
                errors.append(f"Property without a name in db '{dbname}'.")
            if ptype not in PROPERTY_TYPES:
                errors.append(f"Property '{pname}' in db '{dbname}' has unsupported type '{ptype}'.")
            elif ptype == "relation":
                target = prop.get("target_db")
                if target not in db_props:
                    errors.append(f"Relation target_db '{target}' not found in spec databases.")
            elif ptype == "formula":
                expr = prop.get("expression")
                if not expr or not isinstance(expr, str):
                    errors.append("Formula must have a non-empty string 'expression'.")
                else:
                    for ref in _PROP_REF.findall(expr):
                        if ref not in props:
                            errors.append(
                                f"Formula '{pname}' in db '{dbname}' references unknown property '{ref}'."
                            )
            elif ptype == "rollup":
                rel = prop.get("relation_name")
                if props.get(rel) != "relation":
                    errors.append(f"Rollup '{pname}' in db '{dbname}': relation_name '{rel}' is not a relation property.")
                else:
                    rel_prop = next(p for p in db["properties"] if p.get("name") == rel)
                    target_props = db_props.get(rel_prop.get("target_db"), {})
                    if prop.get("rollup_property_name") not in target_props:
                        errors.append(
                            f"Rollup '{pname}' in db '{dbname}': rollup_property_name "
                            f"'{prop.get('rollup_property_name')}' not found in '{rel_prop.get('target_db')}'."
                        )
                if prop.get("function") not in ROLLUP_FUNCTIONS:
                    errors.append(f"Rollup '{pname}' in db '{dbname}': unsupported function '{prop.get('function')}'.")

        if list(props.values()).count("title") != 1:
            errors.append(f"Database '{dbname}' must have exactly one 'title' property.")

        for view in db.get("views", []):
            vtype, vname = view.get("type"), view.get("name")
            if vtype not in VIEW_TYPES:
                errors.append(f"View '{vname}' in db '{dbname}' has unsupported type '{vtype}'.")
            elif vtype == "board":
                gb = view.get("group_by_property")
                if not gb or gb not in props:
                    errors.append(f"Board view group_by_property '{gb}' does not exist in db '{dbname}'.")
                elif props[gb] not in GROUPABLE_TYPES:
                    errors.append(f"Board view group_by_property '{gb}' must be select, status, or multi_select.")
            elif vtype in ("calendar", "timeline"):
                dp = view.get("date_property")
                if not dp or dp not in props:
                    errors.append(f"{vtype.title()} view date_property '{dp}' does not exist in db '{dbname}'.")
                elif props[dp] != "date":
                    errors.append(f"{vtype.title()} view date_property '{dp}' must be a date property.")
            flt = view.get("filter")
            if flt is not None and (not isinstance(flt, dict) or flt.get("property") not in props):
                errors.append(f"View '{vname}' filter must be an object whose 'property' exists in db '{dbname}'.")
            for srt in view.get("sorts", []) or []:
                if not isinstance(srt, dict) or srt.get("property") not in props:
                    errors.append(f"View '{vname}' sort property '{srt.get('property') if isinstance(srt, dict) else srt}' not in db '{dbname}'.")

        for row in db.get("sample_rows", []):
            for key in row:
                if key not in props:
                    errors.append(f"Sample row key '{key}' is not a property of db '{dbname}'.")

    if "root_page" in spec:
        _validate_sections(spec["root_page"].get("sections", []), "root_page", errors)
    for sp in spec.get("sub_pages", []):
        _validate_sections(sp.get("sections", []), f"sub_page '{sp.get('title')}'", errors)

    return errors


def _mock_spec(niche: str) -> dict:
    return {
        "template_name": f"{niche} Template",
        "databases": [
            {
                "name": "Tasks",
                "properties": [
                    {"name": "Name", "type": "title"},
                    {"name": "Status", "type": "select"},
                    {"name": "Due Date", "type": "date"},
                    {"name": "Is Overdue", "type": "formula", "expression": 'prop("Due Date") < now()'},
                ],
                "views": [
                    {"name": "Board View", "type": "board", "group_by_property": "Status"}
                ],
                "sample_rows": [
                    {"Name": "Task 1", "Status": "To Do", "Due Date": "2026-10-01"}
                ],
            }
        ],
        "root_page": {
            "title": f"{niche} Dashboard",
            "sections": [{"type": "paragraph", "text": "Welcome to your template."}],
        },
        "sub_pages": [],
    }


def generate_spec(niche: str, dry_run: bool = False) -> dict:
    """Generate the Notion template specification."""
    if dry_run:
        spec = _mock_spec(niche)
        errs = _validate_spec(spec)
        if errs:
            raise ValueError(f"Mock Spec validation failed: {errs}")
        return spec

    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "spec_generator.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    return generate_json(
        system_prompt,
        f"Generate a Notion template spec for the niche: {niche}. Output ONLY valid raw JSON.",
        validate=_validate_spec,
    )
