import copy

from engine.spec_generator import _mock_spec, _validate_spec


def valid_spec():
    return {
        "template_name": "T",
        "databases": [
            {
                "name": "Projects",
                "properties": [
                    {"name": "Name", "type": "title"},
                    {"name": "Status", "type": "select"},
                    {"name": "Due", "type": "date"},
                    {"name": "Budget", "type": "number"},
                ],
                "views": [],
                "sample_rows": [],
            },
            {
                "name": "Tasks",
                "properties": [
                    {"name": "Task", "type": "title"},
                    {"name": "Project", "type": "relation", "target_db": "Projects"},
                ],
                "views": [],
                "sample_rows": [],
            },
        ],
    }


def errors_for(mutator):
    spec = copy.deepcopy(valid_spec())
    mutator(spec)
    return _validate_spec(spec)


def test_valid_spec_and_mock_pass():
    assert _validate_spec(valid_spec()) == []
    assert _validate_spec(_mock_spec("x")) == []


def test_board_group_by_must_be_groupable():
    errs = errors_for(lambda s: s["databases"][0].__setitem__(
        "views", [{"name": "B", "type": "board", "group_by_property": "Budget"}]))
    assert len(errs) == 1 and "must be select, status, or multi_select" in errs[0]


def test_board_group_by_must_exist():
    errs = errors_for(lambda s: s["databases"][0].__setitem__(
        "views", [{"name": "B", "type": "board", "group_by_property": "Nope"}]))
    assert any("does not exist" in e for e in errs)


def test_calendar_and_timeline_need_a_date_property():
    views = [{"name": "C", "type": "calendar", "date_property": "Status"},
             {"name": "T", "type": "timeline", "date_property": "Due"}]
    errs = errors_for(lambda s: s["databases"][0].__setitem__("views", views))
    assert len(errs) == 1 and "must be a date property" in errs[0]


def test_relation_target_must_exist():
    errs = errors_for(lambda s: s["databases"][1]["properties"][1].__setitem__("target_db", "Missing"))
    assert any("not found in spec databases" in e for e in errs)


def test_exactly_one_title_property():
    errs = errors_for(lambda s: s["databases"][0]["properties"].pop(0))
    assert any("exactly one 'title'" in e for e in errs)


def test_duplicate_property_and_database_names():
    def mutate(s):
        s["databases"][0]["properties"].append({"name": "Status", "type": "select"})
        s["databases"].append({"name": "Projects", "properties": [{"name": "N", "type": "title"}]})
    errs = errors_for(mutate)
    assert any("Property name conflict" in e for e in errs)
    assert any("duplicated" in e for e in errs)


def test_formula_references_must_exist():
    def mutate(s):
        s["databases"][0]["properties"].append(
            {"name": "F", "type": "formula", "expression": 'prop("Missing") + prop("Budget")'})
    errs = errors_for(mutate)
    assert len(errs) == 1 and "unknown property 'Missing'" in errs[0]


def test_rollup_must_reference_relation_and_target_property():
    def mutate(s):
        s["databases"][1]["properties"].append({
            "name": "R", "type": "rollup", "relation_name": "Project",
            "rollup_property_name": "Nope", "function": "sum"})
    errs = errors_for(mutate)
    assert any("rollup_property_name" in e for e in errs)


def test_unsupported_types_and_functions():
    def mutate(s):
        s["databases"][0]["properties"].append({"name": "X", "type": "status"})
        s["databases"][0]["views"] = [{"name": "V", "type": "map"}]
    errs = errors_for(mutate)
    assert any("unsupported type 'status'" in e for e in errs)
    assert any("unsupported type 'map'" in e for e in errs)


def test_sample_row_keys_must_be_properties():
    errs = errors_for(lambda s: s["databases"][0].__setitem__("sample_rows", [{"Name": "a", "Ghost": 1}]))
    assert any("Ghost" in e for e in errs)


def test_view_filter_and_sort_properties_must_exist():
    views = [{"name": "V", "type": "table", "filter": {"property": "Ghost", "select": {"equals": "x"}},
              "sorts": [{"property": "Ghost2", "direction": "ascending"}]}]
    errs = errors_for(lambda s: s["databases"][0].__setitem__("views", views))
    assert len(errs) == 2


def test_section_types_are_checked():
    spec = valid_spec()
    spec["root_page"] = {"title": "R", "sections": [{"type": "marquee", "text": "x"}]}
    assert any("invalid section" in e for e in _validate_spec(spec))


def test_non_dict_spec():
    assert _validate_spec([]) == ["Spec must be a JSON object."]
