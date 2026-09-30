import os
import json
from anthropic import Anthropic

def _validate_spec(spec: dict) -> list[str]:
    # AI Handoff: Collect all errors. Implement checks described in HANDOFF.md task 3.
    errors = []
    
    db_names = {db.get("name") for db in spec.get("databases", [])}
    
    for db in spec.get("databases", []):
        props = {p.get("name"): p.get("type") for p in db.get("properties", [])}
        
        # Check for property name conflicts (duplicate names in single DB)
        seen_props = set()
        for prop in db.get("properties", []):
            pname = prop.get("name")
            if pname in seen_props:
                errors.append(f"Property name conflict in db '{db.get('name')}': '{pname}' is duplicated.")
            seen_props.add(pname)

            ptype = prop.get("type")
            if ptype == "relation":
                target = prop.get("target_db")
                if target not in db_names:
                    errors.append(f"Relation target_db '{target}' not found in spec databases.")
            elif ptype == "formula":
                expr = prop.get("expression")
                if not expr or not isinstance(expr, str):
                    errors.append("Formula must have a non-empty string 'expression'.")
        
        # Check views
        for view in db.get("views", []):
            vtype = view.get("type")
            if vtype == "board":
                gb = view.get("group_by_property")
                if not gb or gb not in props:
                    errors.append(f"Board view group_by_property '{gb}' does not exist in db '{db.get('name')}'.")
                elif props[gb] not in ["select", "status", "multi_select"]:
                    errors.append(f"Board view group_by_property '{gb}' must be select, status, or multi_select.")
            elif vtype == "calendar":
                dp = view.get("date_property")
                if not dp or dp not in props:
                    errors.append(f"Calendar view date_property '{dp}' does not exist in db '{db.get('name')}'.")
                elif props[dp] != "date":
                    errors.append(f"Calendar view date_property '{dp}' must be a date property.")
    
    return errors

def generate_spec(niche: str, dry_run: bool = False) -> dict:
    """Generate the Notion template specification."""
    # AI Handoff: Dry run skips actual Anthropic API call and returns a valid mock.
    if dry_run:
        spec = {
            "template_name": f"{niche} Template",
            "databases": [
                {
                    "name": "Tasks",
                    "properties": [
                        {"name": "Name", "type": "title"},
                        {"name": "Status", "type": "select"},
                        {"name": "Due Date", "type": "date"},
                        {"name": "Is Overdue", "type": "formula", "expression": "prop(\"Due Date\") < now()"}
                    ],
                    "views": [
                        {"name": "Board View", "type": "board", "group_by_property": "Status"}
                    ],
                    "sample_rows": [
                        {"Name": "Task 1", "Status": "To Do", "Due Date": "2026-10-01"}
                    ]
                }
            ],
            "root_page": {
                "title": f"{niche} Dashboard",
                "sections": [{"type": "paragraph", "text": "Welcome to your template."}]
            },
            "sub_pages": []
        }
        errs = _validate_spec(spec)
        if errs:
            raise ValueError(f"Mock Spec validation failed: {errs}")
        return spec

    # Live mode
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    # claude-sonnet-5-5 is the current model as of 2026-09-30; .env.example sets this
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "spec_generator.txt")
    with open(prompt_path, "r") as f:
        system_prompt = f.read()
        
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Generate a Notion template spec for the niche: {niche}. Output ONLY valid raw JSON."}
        ]
    )
    
    # Extract text from response blocks (handling ThinkingBlock from extended thinking models)
    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text" or hasattr(block, "text"):
            text += block.text
    text = text.strip()

    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    
    spec = json.loads(text.strip())
    
    errors = _validate_spec(spec)
    if errors:
        raise ValueError(f"Spec validation failed: {errors}")
        
    return spec
