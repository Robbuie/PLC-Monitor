"""Reading tag lists from Rockwell L5X and CSV exports and plain lists."""

import pytest

from app.engine.tagimport import TagImportError, read

L5X = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="33.00" TargetName="Line3" TargetType="Controller">
<Controller Use="Target" Name="Line3" ProcessorType="1756-L83E">
<DataTypes/>
<Tags>
<Tag Name="Part_Present" TagType="Base" DataType="BOOL" Radix="Decimal">
<Description><![CDATA[Part at the
vision station]]></Description>
</Tag>
<Tag Name="Speeds" TagType="Base" DataType="REAL" Dimensions="10"/>
<Tag Name="Recipe" TagType="Base" DataType="RecipeUDT"/>
<Tag Name="Go_Alias" TagType="Alias" AliasFor="Local:1:I.Data.0"/>
</Tags>
<Programs>
<Program Name="MainProgram">
<Tags>
<Tag Name="Counter" TagType="Base" DataType="DINT"/>
</Tags>
</Program>
</Programs>
</Controller>
</RSLogix5000Content>
"""

ROCKWELL_CSV = '''remark,"CSV-Import-Export"
remark,"Date = Mon Nov 09 10:00:00 2026"
0.3
TYPE,SCOPE,NAME,DESCRIPTION,DATATYPE,SPECIFIER,ATTRIBUTES
TAG,,Part_Present,"Part at station",BOOL,,"(RADIX := Decimal)"
TAG,MainProgram,Counter,"",DINT,,"(RADIX := Decimal)"
ALIAS,,Go_Alias,"",,"Local:1:I.Data.0",""
COMMENT,,Part_Present,"a comment row",,,
'''


def test_l5x_controller_and_program_tags(tmp_path):
    path = tmp_path / "Line3.L5X"
    path.write_text(L5X, encoding="utf-8")
    entries = {e.tag: e for e in read(str(path))}
    assert list(entries) == ["Part_Present", "Speeds", "Recipe", "Go_Alias",
                             "Program:MainProgram.Counter"]
    assert entries["Part_Present"].dtype == "BOOL"
    assert entries["Part_Present"].description == "Part at the vision station"
    assert entries["Speeds"].dtype == "REAL[10]" and not entries["Speeds"].basic
    assert not entries["Recipe"].basic
    assert entries["Program:MainProgram.Counter"].scope == "MainProgram"
    assert entries["Program:MainProgram.Counter"].basic


def test_rockwell_tag_csv(tmp_path):
    path = tmp_path / "tags.csv"
    path.write_bytes(b"\xef\xbb\xbf" + ROCKWELL_CSV.encode("utf-8"))
    entries = read(str(path))
    assert [(e.tag, e.dtype) for e in entries] == [
        ("Part_Present", "BOOL"), ("Program:MainProgram.Counter", "DINT"), ("Go_Alias", "alias")]


def test_a_plain_list_one_per_line(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("D100\nD100.5\n\nM200\nD100\n", encoding="utf-8")
    assert [e.tag for e in read(str(path))] == ["D100", "D100.5", "M200"]


def test_a_spreadsheet_with_headers_semicolons_and_cp1252(tmp_path):
    path = tmp_path / "s7.csv"
    path.write_bytes("Kommentar;Address;Data type\nTür offen;M0.0;Bool\nDrehzahl;DB10,REAL4;Real\n"
                     .encode("cp1252"))
    entries = read(str(path))
    assert [(e.tag, e.dtype, e.description) for e in entries] == [
        ("M0.0", "Bool", ""), ("DB10,REAL4", "Real", "")]


def test_device_comment_export(tmp_path):
    path = tmp_path / "comments.csv"
    path.write_text("Device Name,Comment\nX0,Start button\nD100,Part count\n", encoding="utf-8")
    entries = read(str(path))
    assert [(e.tag, e.description) for e in entries] == [("X0", "Start button"),
                                                         ("D100", "Part count")]


def test_empty_and_broken_files(tmp_path):
    empty = tmp_path / "e.csv"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(TagImportError):
        read(str(empty))
    broken = tmp_path / "b.L5X"
    broken.write_text("<RSLogix5000Content><Controller>", encoding="utf-8")
    with pytest.raises(TagImportError):
        read(str(broken))
    with pytest.raises(TagImportError):
        read(str(tmp_path / "missing.csv"))
