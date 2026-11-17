"""Reading a controller's tag list for the browser: the listings decoded, the
tree they make, and search. No controller here -- the bytes are built to the
layout libplctag's list_tags_logix tool reads, and pycomm3's tag database is
shaped as LogixDriver.tags returns it."""

import struct

import pytest

from app.engine import tagbrowse as tb
from app.engine.tagbrowse import BrowseError


# ---------------------------------------------------------------- builders

def tag_entry(name: str, typ: int, dims=(0, 0, 0), instance=1, size=4) -> bytes:
    raw = name.encode()
    return struct.pack("<IHHIIIH", instance, typ, size, *dims, len(raw)) + raw


def udt_blob(udt_id: int, name: str, fields, size=12, semicolon=True) -> bytes:
    head = struct.pack("<HIIHH", udt_id, 0, size, len(fields), 0)
    body = b"".join(struct.pack("<HHI", meta, typ, off) for _n, meta, typ, off in fields)
    title = (name + ";n12345678") if semicolon else name
    names = b"".join(n.encode() + b"\x00" for n, *_ in fields)
    return head + body + title.encode() + b"\x00" + names


DINT, BOOL, REAL, SINT, DWORD = 0xC4, 0xC1, 0xCA, 0xC2, 0xD3
STRUCT = 0x8000
ONE_DIM, TWO_DIM = 0x2000, 0x4000

UDTS = {
    0x123: udt_blob(0x123, "Station", [
        ("ZZZZZZZZZZStation0", 0, 0xC2, 0),         # hidden bit host
        ("Ready", 0, BOOL, 0),
        ("Count", 0, DINT, 4),
        ("Cycle", 0, STRUCT | 0x0F83, 8),            # a TIMER
        ("Faults", 1, DWORD, 20),                    # BOOL[32], one word
        ("Name", 0, STRUCT | 0x0FCE, 24),            # a STRING
        ("Temps", 4, REAL, 112),
    ]),
    0xF83: udt_blob(0xF83, "TIMER", [
        ("CTL", 0, DINT, 0), ("PRE", 0, DINT, 4), ("ACC", 0, DINT, 8),
        ("EN", 31, BOOL, 0), ("TT", 30, BOOL, 0), ("DN", 29, BOOL, 0),
    ]),
    0xFCE: udt_blob(0xFCE, "STRING", [("LEN", 0, DINT, 0), ("DATA", 82, SINT, 4)]),
}


def fetch(i):
    return tb.parse_udt(UDTS[i])


def controller_listing() -> bytes:
    return b"".join([
        tag_entry("Part_Present", BOOL),
        tag_entry("Speeds", REAL | ONE_DIM, (10, 0, 0)),
        tag_entry("Line", STRUCT | ONE_DIM | 0x123, (3, 0, 0)),
        tag_entry("Grid", DINT | TWO_DIM, (2, 3, 0)),
        tag_entry("Big", DINT | ONE_DIM, (1000, 0, 0)),
        tag_entry("Flags", DWORD | ONE_DIM, (64, 0, 0)),
        tag_entry("Program:MainProgram", 0x1068),
        tag_entry("Routine:Main", 0x1068),
        tag_entry("Map:Local", 0x1068),
        tag_entry("__Hidden", DINT),
        tag_entry("Sys", DINT | 0x1000),
        tag_entry("Local:1:I", STRUCT | 0x123),
    ])


@pytest.fixture
def db():
    controller = tb.parse_tag_list(controller_listing())
    programs = {p: tb.parse_tag_list(tag_entry("Counter", DINT) + tag_entry("Tmr", STRUCT | 0xF83))
                for p in tb.program_names(controller)}
    return tb.assemble(controller, programs, fetch, "test")


def find(nodes, label):
    return next(n for n in nodes if n.label == label)


# ------------------------------------------------------------- the listings

def test_tag_list_decodes_names_types_and_dims():
    tags = tb.parse_tag_list(controller_listing())
    speeds = next(t for t in tags if t.name == "Speeds")
    assert speeds.num_dims == 1 and speeds.dims[0] == 10 and not speeds.is_struct
    line = next(t for t in tags if t.name == "Line")
    assert line.is_struct and line.udt_id == 0x123
    assert next(t for t in tags if t.name == "Sys").is_system


def test_tag_list_cut_short_says_so():
    with pytest.raises(BrowseError):
        tb.parse_tag_list(tag_entry("Speeds", REAL)[:-2])


def test_udt_template_names_members_and_hides_hosts():
    raw = tb.parse_udt(UDTS[0x123])
    assert raw.name == "Station" and raw.id == 0x123
    assert [f.name for f in raw.fields][:3] == ["ZZZZZZZZZZStation0", "Ready", "Count"]


def test_predefined_type_without_semicolon_takes_first_string_as_name():
    raw = tb.parse_udt(udt_blob(0xF83, "TIMER", [("PRE", 0, DINT, 0), ("ACC", 0, DINT, 4)],
                                semicolon=False))
    assert raw.name == "TIMER" and [f.name for f in raw.fields] == ["PRE", "ACC"]


# ------------------------------------------------------------------ the tree

def test_system_routine_and_hidden_tags_are_left_out(db):
    names = {t.name for t in db.tags}
    assert {"Part_Present", "Speeds", "Line", "Local:1:I"} <= names
    assert not names & {"Program:MainProgram", "Routine:Main", "Map:Local", "__Hidden", "Sys"}


def test_scopes_are_controller_programs_then_io(db):
    top = tb.tree(db)
    assert [n.label for n in top] == ["Controller tags", "Program:MainProgram", "I/O modules"]
    program = top[1].children()
    assert [n.path for n in program] == ["Program:MainProgram.Counter", "Program:MainProgram.Tmr"]
    assert [n.label for n in program] == ["Counter", "Tmr"]


def test_udt_array_opens_to_members_with_readable_paths(db):
    line = find(tb.tree(db)[0].children(), "Line")
    assert line.kind == "array" and line.type_label == "Station[3]"
    second = line.children()[1]
    assert second.path == "Line[1]" and second.kind == "struct"
    members = {m.label: m for m in second.children()}
    assert list(members) == ["Ready", "Count", "Cycle", "Faults", "Name", "Temps"]
    assert members["Ready"].readable and members["Ready"].path == "Line[1].Ready"
    cycle = {m.label: m for m in members["Cycle"].children()}
    assert list(cycle) == ["PRE", "ACC", "EN", "TT", "DN"]           # CTL hidden on a TIMER
    assert cycle["ACC"].path == "Line[1].Cycle.ACC" and cycle["ACC"].dtype == "DINT"


def test_string_is_one_value_read_as_string(db):
    station = find(tb.tree(db)[0].children(), "Line").children()[0]
    name = find(station.children(), "Name")
    assert name.readable and name.dtype == "STRING" and name.entry_dtype == "STRING"


def test_bool_arrays_open_to_bits(db):
    station = find(tb.tree(db)[0].children(), "Line").children()[0]
    faults = find(station.children(), "Faults")
    assert faults.type_label == "BOOL[32]"
    assert faults.children()[31].path == "Line[0].Faults[31]"
    flags = find(tb.tree(db)[0].children(), "Flags")
    assert flags.type_label == "BOOL[64]" and len(flags.children()) == 64


def test_two_dimensional_array_reads_as_tag_i_j(db):
    grid = find(tb.tree(db)[0].children(), "Grid")
    assert grid.type_label == "DINT[2,3]"
    row = grid.children()[1]
    assert row.kind == "array" and row.label == "[1]"
    assert [c.path for c in row.children()] == ["Grid[1,0]", "Grid[1,1]", "Grid[1,2]"]


def test_long_array_opens_in_pages(db):
    big = find(tb.tree(db)[0].children(), "Big")
    pages = big.children()
    assert len(pages) == 1000 // tb.PAGE and pages[0].label == f"[0..{tb.PAGE - 1}]"
    last = pages[-1].children()
    assert last[-1].path == "Big[999]" and last[-1].readable


# ------------------------------------------------------------------ search

def test_search_finds_members_inside_structures(db):
    found, cut = tb.search(db, "acc")
    assert not cut
    assert "Program:MainProgram.Tmr.ACC" in [n.path for n in found]
    # Line is an array: returned as the array to open, not as every element.
    assert "Line" in [n.path for n in found]
    assert not any(n.path.startswith("Line[") for n in found)


def test_search_limit(db):
    found, cut = tb.search(db, "", limit=1)
    assert found == [] and not cut
    found, cut = tb.search(db, "a", limit=2)
    assert len(found) == 2 and cut


# ------------------------------------------------------------- from pycomm3

def pycomm3_tags():
    timer = {"name": "TIMER", "attributes": ["PRE", "ACC", "EN", "DN"], "internal_tags": {
        "PRE": {"data_type_name": "DINT", "tag_type": "atomic", "array": 0},
        "ACC": {"data_type_name": "DINT", "tag_type": "atomic", "array": 0},
        "EN": {"data_type_name": "BOOL", "tag_type": "atomic", "bit": 31},
        "DN": {"data_type_name": "BOOL", "tag_type": "atomic", "bit": 29}}}
    string = {"name": "STRING", "string": 82, "attributes": ["LEN", "DATA"], "internal_tags": {}}
    station = {"name": "Station", "attributes": ["Cycle", "Vin", "Bits"], "internal_tags": {
        "Cycle": {"data_type_name": "TIMER", "tag_type": "struct", "data_type": timer},
        "Vin": {"data_type_name": "STRING", "tag_type": "struct", "data_type": string},
        "Bits": {"data_type_name": "DWORD", "tag_type": "atomic", "array": 2}}}
    return {
        "Speed": {"tag_type": "atomic", "data_type_name": "REAL", "data_type": "REAL",
                  "dim": 0, "dimensions": [0, 0, 0]},
        "Stations": {"tag_type": "struct", "data_type_name": "Station", "data_type": station,
                     "dim": 1, "dimensions": [4, 0, 0]},
        "Program:Main.Count": {"tag_type": "atomic", "data_type_name": "DINT",
                               "data_type": "DINT", "dim": 0, "dimensions": [0, 0, 0]},
        "Local:2:I": {"tag_type": "struct", "data_type_name": "Station", "data_type": station,
                      "dim": 0, "dimensions": [0, 0, 0]},
    }


def test_pycomm3_database_makes_the_same_tree():
    db = tb.from_pycomm3(pycomm3_tags(), ["Main", "Idle"])
    top = tb.tree(db)
    assert [n.label for n in top] == ["Controller tags", "Program:Idle", "Program:Main",
                                      "I/O modules"]
    stations = find(top[0].children(), "Stations")
    assert stations.type_label == "Station[4]"
    members = {m.label: m for m in stations.children()[2].children()}
    assert members["Vin"].readable and members["Vin"].entry_dtype == "STRING"
    assert members["Bits"].type_label == "BOOL[64]"
    acc = find(members["Cycle"].children(), "ACC")
    assert acc.path == "Stations[2].Cycle.ACC" and acc.readable
    assert [n.path for n in top[2].children()] == ["Program:Main.Count"]
    assert top[1].children() == [] and top[1].type_label == "0 tags"


def test_pycomm3_recursive_type_does_not_loop():
    node = {"name": "Node", "attributes": ["Next", "V"], "internal_tags": {}}
    node["internal_tags"]["Next"] = {"data_type_name": "Node", "tag_type": "struct",
                                     "data_type": node}
    node["internal_tags"]["V"] = {"data_type_name": "DINT", "tag_type": "atomic"}
    db = tb.from_pycomm3({"Head": {"tag_type": "struct", "data_type": node, "dim": 0}})
    found, _ = tb.search(db, "zzz")
    assert found == []


def test_pccc_controllers_are_refused_with_a_reason():
    from app.engine.libplctag_driver import LibPlcTagConfig
    cfg = LibPlcTagConfig("10.0.0.5", "", "slc500", "ab-pccc")
    with pytest.raises(BrowseError, match="N7:0"):
        tb.browse_libplctag(cfg)
