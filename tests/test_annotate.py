"""Annotations (highlights, notes, atom numbers, stereo labels) and copying as SMILES, SVG and PNG, in the editor and the list."""
import base64
import os
import re
import sys
from io import BytesIO

import pytest
from PIL import Image
from rdkit import Chem
from rdkit.Chem import rdDepictor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import annotate  # noqa: E402
import board  # noqa: E402
import editor  # noqa: E402

COLS, ROWS, CW, CH = 120, 40, 10, 20
UI = editor.Layout(COLS, ROWS, CW, CH)
OSC52 = re.compile("\x1b\\]52;c;([A-Za-z0-9+/=]*)\x1b\\\\")
WDATA = re.compile("\x1b\\]5522;type=wdata:mime=([A-Za-z0-9+/=]+);([A-Za-z0-9+/=]*)\x1b\\\\")
NOTED = {"atoms": [[1, "yellow", "acid: here; or|there, & so"], [9, "", "OH"]], "bonds": [[1, 3, "blue", "amide"]], "show": ["numbers", "cip"]}


def copied(output):
    """What a terminal with Kitty's clipboard protocol ends up with: {MIME type: bytes}, after the OSC 52 text."""
    found = {}
    texts = OSC52.findall(output)
    if texts:
        found["text/plain"] = base64.b64decode(texts[-1])
    if "\x1b]5522;type=write\x1b\\" in output:
        found = {}
        parts = {}
        for mime, chunk in WDATA.findall(output):
            parts.setdefault(base64.b64decode(mime).decode(), []).append(chunk)
        found = {mime: base64.b64decode("".join(chunks)) for mime, chunks in parts.items()}
        assert output.rstrip().endswith("\x1b]5522;type=wdata\x1b\\") or "\x1b]5522;type=wdata\x1b\\" in output
    return found


# The notes as text


def test_notes_round_trip_through_cxsmiles_that_rdkit_reads():
    text = annotate.write("CC(=O)Nc1ccc(O)cc1", NOTED)
    assert text.startswith("CC(=O)Nc1ccc(O)cc1 |atomProp:")
    mol = Chem.MolFromSmiles(text)
    assert mol.GetAtomWithIdx(1).GetProp("atomNote") == "acid: here; or|there, & so"
    assert annotate.split(text) == ("CC(=O)Nc1ccc(O)cc1", NOTED)


def test_a_note_keeps_its_lines_through_cxsmiles():
    notes = {"atoms": [[2, "", "the OH\nH-bond donor"]], "bonds": [], "show": []}
    text = annotate.write("CCO", notes)
    assert text == "CCO |atomProp:2.atomNote.the OH&#10;H-bond donor|"
    assert Chem.MolFromSmiles(text).GetAtomWithIdx(2).GetProp("atomNote") == "the OH\nH-bond donor"  # RDKit reads it as one note
    assert annotate.split(text)[1] == notes
    assert annotate.clean_note("\n a\x1b\r\nb  \n\n") == "a\nb"  # line breaks kept, blank ends and control characters not
    assert annotate.clean_note("\n".join("abcdefgh")).count("\n") == annotate.MAX_NOTE_LINES - 1


def test_a_smiles_without_notes_is_written_as_it_was():
    assert annotate.write("OCC", annotate.empty()) == "OCC"
    assert annotate.split("OCC") == ("OCC", annotate.empty())


def test_notes_on_atoms_or_bonds_that_are_not_there_are_dropped_when_read():
    assert annotate.split("CCO |atomProp:0.molterm_bond_2_note.x:5.atomNote.y|")[1] == annotate.empty()


@pytest.mark.parametrize("bad", [{"atoms": [[0, "red", ""]], "bonds": [], "show": []}, {"atoms": [[0, "", ""]], "bonds": [], "show": []},
                                 {"atoms": [], "bonds": [[2, 1, "blue", ""]], "show": []}, {"atoms": [], "bonds": [], "show": ["x"]},
                                 {"atoms": [[0, "", "a\x1bb"]], "bonds": [], "show": []}, {"atoms": [[0, "", "x" * (annotate.MAX_NOTE + 1)]], "bonds": [], "show": []},
                                 {"atoms": [[0, "", "a\n\nb\n"]], "bonds": [], "show": []},
                                 {"atoms": [[0, "", "a"], [0, "", "b"]], "bonds": [], "show": []}, {"atoms": []}, [], None])
def test_bad_notes_are_refused(bad):
    with pytest.raises(annotate.NoteError):
        annotate.check(bad)


def test_exports_draw_the_notes_on_white():
    mol = Chem.MolFromSmiles("C[C@H](N)C(=O)O")
    rdDepictor.Compute2DCoords(mol)
    plain = annotate.export(mol, annotate.empty(), "png")
    marked = annotate.export(mol, {"atoms": [[1, "green", "alpha"]], "bonds": [[0, 1, "pink", ""]], "show": ["cip", "numbers"]}, "png")
    image = Image.open(BytesIO(marked)).convert("RGB")
    assert image.size == annotate.EXPORT_SIZE and image.getpixel((2, 2)) == (255, 255, 255)
    assert marked != plain
    svg = annotate.export(mol, {"atoms": [[1, "", "alpha"]], "bonds": [], "show": []}, "svg")
    assert svg.startswith("<?xml") and "class='note'" in svg


def test_the_clipboard_sequence_gives_osc_52_text_then_every_type_in_whole_chunks():
    mol = Chem.MolFromSmiles("CCO")
    rdDepictor.Compute2DCoords(mol)
    sequence = annotate.clipboard_sequence(annotate.clipboard("CCO", mol, annotate.empty(), "all"))
    assert sequence.index("\x1b]52;") < sequence.index("\x1b]5522;type=write")
    assert all(len(chunk) <= 5460 and len(chunk) % 4 == 0 for _, chunk in WDATA.findall(sequence)[:-1])
    got = copied(sequence)
    assert got["text/plain"] == b"CCO" and got["image/png"].startswith(b"\x89PNG") and got["image/svg+xml"].startswith(b"<?xml")
    only_text = annotate.clipboard_sequence(annotate.clipboard("CCO", mol, annotate.empty(), "smiles"))
    assert "5522" not in only_text and copied(only_text) == {"text/plain": b"CCO"}


# The editor


class Session:
    def __init__(self, smiles=""):
        _, self.text, self.output = editor.step("", smiles, "", COLS, ROWS, CW, CH)

    def send(self, raw):
        self.status, text, self.output = editor.step(self.text, "", raw, COLS, ROWS, CW, CH)
        if text:
            self.text = text
        return self

    @property
    def state(self):
        return editor.decode(self.text)

    def cell(self, x, y):
        col, row = UI.to_cell(self.state, x, y)
        return round(col + 0.5), round(row + 0.5)

    def atom_cell(self, ident):
        atom = editor._atom(self.state, ident)
        return self.cell(atom[2], atom[3])

    def bond_cell(self, first, second):
        a, b = editor._atom(self.state, first), editor._atom(self.state, second)
        return self.cell((a[2] + b[2]) / 2, (a[3] + b[3]) / 2)

    def right_click(self, col, row):
        return self.send(f"\x1b[<2;{col};{row}M\x1b[<2;{col};{row}m")

    def choose(self, label):
        """Click an item of the open menu by its label."""
        s = self.state
        start_col, start_row, _ = editor._menu_box(s, UI)
        items = editor._menu_items(s, UI)
        index = next(i for i, item in enumerate(items) if item and editor._item_label(s, item).strip() == label)
        col, row = start_col + 2, start_row + index + 1
        return self.send(f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m")

    def menu_bar(self, title):
        start = next(start for start, _, index in UI.titles if editor.MENUS[index][0] == title)
        return self.send(f"\x1b[<0;{start + 2};1M\x1b[<0;{start + 2};1m")


def test_an_atom_can_be_highlighted_and_noted_from_its_menu():
    session = Session("CCO")
    session.right_click(*session.atom_cell(3)).choose("Highlight")
    assert session.state["notes"]["atoms"] == [[3, "yellow", ""]]
    session.right_click(*session.atom_cell(3)).choose("Note...")
    assert "Note:" in session.output
    session.send("hydroxyl").send("\r")
    assert session.state["notes"]["atoms"] == [[3, "yellow", "hydroxyl"]] and session.state["prompt"] is None
    assert session.state["notes"]["flags"] == {"a3": "user"}  # the person's own note
    assert editor.annotated(session.state) == "CCO |atomProp:2.atomNote.hydroxyl:2.molterm_flags.user:2.molterm_highlight.yellow|"  # the O, in RDKit's order


def test_a_note_says_who_wrote_it_and_whether_it_is_about_a_group_through_cxsmiles():
    notes = {**NOTED, "flags": {"a1": "user group", "b1-3": "user"}}
    annotate.check(notes)
    text = annotate.write("CC(=O)Nc1ccc(O)cc1", notes)
    assert "molterm_flags.user group" in text and "molterm_bond_3_flags.user" in text
    _, read = annotate.split(text)
    assert read == notes and annotate.flags(read, [9]) == set()
    moved = annotate.renumber(read, {1: 5, 3: 7, 9: 2})
    assert moved["flags"] == {"a5": "user group", "b5-7": "user"}
    assert "flags" not in annotate.renumber(NOTED, {1: 1, 3: 3, 9: 9})  # none, and no key for them
    for bad in ({"a1": "admin"}, {"x1": "user"}, ["user"]):
        with pytest.raises(annotate.NoteError):
            annotate.check({**NOTED, "flags": bad})
    assert annotate.group(NOTED, [(1, 2), (1, 3), (3, 4)], [1]) == [1]  # its neighbours aren't highlighted
    ring = {"atoms": [[i, "green", ""] for i in (1, 2, 3)] + [[4, "blue", ""]], "bonds": [], "show": []}
    assert sorted(annotate.group(ring, [(1, 2), (2, 3), (3, 4)], [2])) == [1, 2, 3] and annotate.group(ring, [], [5]) == [5]


def test_an_atom_in_a_highlighted_group_is_noted_alone_or_for_the_group():
    session = Session("CC(=O)Nc1ccc(O)cc1")
    s = session.state
    s["selected"] = [5, 6, 7, 8, 10, 11]  # the ring
    session.text = editor.encode(s)
    session.right_click(*session.atom_cell(6)).choose("Highlight Blue")
    s = session.state
    s["selected"] = []
    session.text = editor.encode(s)
    session.right_click(*session.atom_cell(6))
    labels = [editor._item_label(session.state, item).strip() for item in editor._menu_items(session.state) if item]
    assert "Note This Atom..." in labels and "Note Highlighted Group..." in labels and "Note..." not in labels
    session.choose("Note Highlighted Group...")
    assert "the highlighted group" in session.output
    session.send("the ring\r")
    s = session.state
    assert [entry for entry in s["notes"]["atoms"] if entry[2]] == [[6, "blue", "the ring"]] and s["notes"]["flags"] == {"a6": "user group"}
    session.right_click(*session.atom_cell(8)).choose("Note This Atom...")
    session.send("a CH\r")
    assert session.state["notes"]["flags"] == {"a6": "user group", "a8": "user"}
    # inline, the group's note points at the middle of the ring, the atom's at the atom
    ui = editor.Layout(COLS, ROWS, CW, CH, inline=(2, 2, 30, 80))
    found = {text: point for text, point, _, _ in editor.inline_notes(session.state, ui)}
    ring = [a for a in session.state["atoms"] if a[0] in (5, 6, 7, 8, 10, 11)]
    assert found["the ring"] == pytest.approx((sum(a[2] for a in ring) / 6, sum(a[3] for a in ring) / 6))
    atom = next(a for a in session.state["atoms"] if a[0] == 8)
    assert found["a CH"] == pytest.approx((atom[2], atom[3]))
    # an atom outside any group keeps the one item
    session.right_click(*session.atom_cell(1))
    assert "Note..." in [editor._item_label(session.state, item).strip() for item in editor._menu_items(session.state) if item]


def test_a_bond_gets_its_own_annotation():
    session = Session("CCO")
    session.right_click(*session.bond_cell(1, 2)).choose("Highlight")
    assert session.state["notes"]["bonds"] == [[1, 2, "yellow", ""]]
    session.right_click(*session.bond_cell(1, 2)).choose("Clear Annotations")
    assert session.state["notes"]["bonds"] == []


def test_a_selection_has_its_own_menu_for_copying_and_annotating():
    session = Session("CC(=O)Nc1ccc(O)cc1")
    session.send("\x01")  # select all
    session.right_click(*session.atom_cell(1))
    assert session.state["menu"][1] == "selection"
    session.choose("Highlight Green")
    notes = session.state["notes"]
    assert len(notes["atoms"]) == 11 and len(notes["bonds"]) == 11 and all(entry[-2] == "green" for entry in notes["atoms"] + notes["bonds"])
    session.right_click(*session.atom_cell(1)).choose("Copy Selection as SMILES, SVG and PNG")
    got = copied(session.output)
    assert got["text/plain"].startswith(b"CC(=O)Nc1ccc(O)cc1 |atomProp:") and got["image/png"].startswith(b"\x89PNG")


def test_copying_part_of_a_drawing_copies_that_part():
    session = Session("CC(=O)Nc1ccc(O)cc1")
    s = session.state
    s["selected"] = [1, 2, 3]  # the acetyl group
    session.text = editor.encode(s)
    session.right_click(*session.atom_cell(1)).choose("Copy Selection as SMILES")
    assert copied(session.output)["text/plain"] == b"CC=O"
    assert "The selection" in session.output


def test_a_note_on_several_atoms_and_an_empty_note_takes_it_off():
    session = Session("CCO")
    s = session.state
    s["selected"] = [1, 2]
    session.text = editor.encode(s)
    session.right_click(*session.atom_cell(1)).choose("Note...")
    assert "these 3 atoms and bonds" in session.output
    session.send("x\r")
    assert [entry[-1] for entry in session.state["notes"]["atoms"] + session.state["notes"]["bonds"]] == ["x", "x", "x"]
    session.right_click(*session.atom_cell(1)).choose("Note...")
    session.send("\x7f\r")
    assert annotate.is_empty(session.state["notes"])


def test_esc_or_a_click_cancels_a_note():
    session = Session("CCO")
    session.right_click(*session.atom_cell(1)).choose("Note...")
    session.send("abc").send("\x1b")
    assert session.state["prompt"] is None and annotate.is_empty(session.state["notes"])


def test_the_annotate_menu_acts_on_the_selection_and_says_so_without_one():
    session = Session("CCO")
    session.menu_bar("Annotate").choose("Highlight Blue")
    assert "Select atoms first" in session.output
    session.send("\x01").menu_bar("Annotate").choose("Highlight Blue")
    assert len(session.state["notes"]["atoms"]) == 3
    session.menu_bar("Annotate").choose("Show Atom Numbers")
    assert session.state["notes"]["show"] == ["numbers"]
    session.menu_bar("Annotate")
    assert "✓ Show Atom Numbers" in session.output
    session.choose("Clear All Annotations")
    assert annotate.is_empty(session.state["notes"])


def test_annotations_are_drawn_and_undone():
    session = Session("CCO")
    plain = session.output
    session.right_click(*session.atom_cell(2)).choose("Highlight")
    assert "a=T" in session.output  # the structure is drawn again
    session.send("\x1a")
    assert annotate.is_empty(session.state["notes"])
    session.send("\x19")
    assert session.state["notes"]["atoms"] == [[2, "yellow", ""]]
    assert plain


def test_deleting_an_atom_takes_its_notes_and_its_bonds_with_it():
    session = Session("CCO")
    s = session.state
    s["notes"] = {"atoms": [[3, "pink", "o"]], "bonds": [[2, 3, "", "b"]], "show": []}
    session.text = editor.encode(s)
    session.right_click(*session.atom_cell(3)).choose("Delete")
    assert session.state["notes"] == annotate.empty()


def test_clean_up_keeps_the_notes_on_the_same_atoms():
    session = Session("OCC")
    s = session.state
    s["notes"] = {"atoms": [[1, "", "the O"]], "bonds": [[2, 3, "blue", ""]], "show": []}
    s["atoms"][0][2] += 3.0  # out of place, for Clean Up to put back
    session.text = editor.encode(s)
    session.send("\x0b")
    after = session.state
    noted = editor._atom(after, after["notes"]["atoms"][0][0])
    assert noted[1] == "O" and after["notes"]["bonds"][0][2] == "blue"
    first, second = after["notes"]["bonds"][0][:2]
    assert {editor._atom(after, first)[1], editor._atom(after, second)[1]} == {"C"}


def test_loading_cxsmiles_brings_its_notes_and_quitting_prints_them():
    text = annotate.write("CC(=O)Nc1ccc(O)cc1", NOTED)
    session = Session(text)
    assert len(session.state["notes"]["atoms"]) == 2 and session.state["notes"]["show"] == ["numbers", "cip"]
    status, _, output = editor.step(session.text, "", "\x11", COLS, ROWS, CW, CH)
    assert status == editor.QUIT
    smiles, notes = annotate.split(output)
    assert smiles == "CC(=O)Nc1ccc(O)cc1" and notes == NOTED


def test_a_state_with_notes_on_missing_atoms_or_with_something_to_copy_is_handled():
    s = Session("CCO").state
    s["notes"] = {"atoms": [[9, "pink", ""]], "bonds": [], "show": []}
    with pytest.raises(editor.StateError):
        editor.decode(editor.encode(s))
    s["notes"] = annotate.empty()
    s["clipboard"] = "\x1b]0;evil\x07"
    assert "clipboard" not in editor.decode(editor.encode(s))  # only ever made here


def test_the_terminals_answer_to_a_copy_is_not_read_as_keys():
    session = Session("CCO")
    before = session.state
    for answer in ("\x1b]5522;type=write:status=DONE\x1b\\", "\x1b]5522;type=write:status=EPERM\x07", "\x1b]11;rgb:0000/0000/0000\x1b\\"):
        session.send(answer)
        after = session.state
        assert after["atoms"] == before["atoms"] and after["tool"] == before["tool"] and after["menu"] is None
    assert editor.parse_events("\x1b]5522;type=write:status=DONE\x1b\\") == ([("clipboard", "DONE")], "")
    assert editor.parse_events("\x1b]5522;type=wri") == ([], "\x1b]5522;type=wri")
    session.send("\x1b]5522;type=write:status=DONE\x1b\\")
    assert "pictures and all" in session.output


# The list


def item_cell(session, index):
    grid = board.Grid(session.cols, session.rows)
    row, col = grid.tile(index, session.state["top"])
    return col + 5, row + 3


class List:
    def __init__(self, items, cols=130, rows=44):
        self.cols, self.rows, self.text = cols, rows, ""
        self.status, self.text, self.output = board.step("", items, "", cols, rows, CW, CH)

    def send(self, raw):
        self.status, text, self.output = board.step(self.text, "", raw, self.cols, self.rows, CW, CH)
        if text:
            self.text = text
        return self

    @property
    def state(self):
        return editor.decode(self.text, board._check_state)

    def right_click(self, index):
        col, row = item_cell(self, index)
        return self.send(f"\x1b[<2;{col + 1};{row + 1}M\x1b[<2;{col + 1};{row + 1}m")

    def choose(self, label):
        b = self.state
        grid = board.Grid(self.cols, self.rows)
        col, row, _ = board._menu_place(b, grid)
        index = next(i for i, item in enumerate(board._menu_items(b, b["menu"]["index"])) if item and item[0].strip() == label)
        return self.send(f"\x1b[<35;{col + 2};{row + index + 1}M\x1b[<0;{col + 2};{row + index + 1}M\x1b[<0;{col + 2};{row + index + 1}m")


def test_the_list_reads_and_prints_cxsmiles_with_names():
    text = annotate.write("CC(=O)Nc1ccc(O)cc1", NOTED)
    session = List(f"{text} Paracetamol\nro:CCO ethanol, plain")
    items = session.state["items"]
    assert items[0][0] == "Paracetamol" and items[0][1] == "CC(=O)Nc1ccc(O)cc1" and items[0][4] == NOTED
    assert items[1][0] == "ethanol, plain" and not items[1][2]
    session.send("q")
    assert session.output.split("\n") == [f"{text} Paracetamol", "CCO ethanol, plain"]


def test_a_bad_cxsmiles_extension_is_named():
    assert List("CCO |atomProp:0.molterm_highlight.red| x").state["items"][0][4] == annotate.empty()  # an unknown colour is left out
    status, _, output = board.step("", "CCO |junk junk| y", "", 130, 44)
    assert status == editor.FAILED and "CCO |junk junk|" in output
    assert List("CCO |atomProp:7.atomNote.a| y").state["items"][0][4] == annotate.empty()  # on an atom that isn't there


def test_thumbnails_show_the_annotations():
    plain = board._thumbnail("CCO", '{"atoms": [], "bonds": [], "show": []}', 28, 8, CW, CH, "mono")
    marked = board._thumbnail("CCO", '{"atoms": [[0, "yellow", "hi"]], "bonds": [], "show": []}', 28, 8, CW, CH, "mono")
    assert plain != marked


def test_right_click_on_a_tile_opens_its_menu_and_copies_in_each_format():
    session = List("CCO ethanol\nro:c1ccccc1 benzene")
    session.right_click(0)
    assert session.state["menu"]["index"] == 0 and "Copy SMILES, SVG and PNG" in session.output and "Edit" in session.output
    session.choose("Copy PNG")
    got = copied(session.output)
    assert got["text/plain"] == b"CCO" and got["image/png"].startswith(b"\x89PNG") and session.state["menu"] is None
    session.right_click(1)
    assert "Edit" not in [item[0] for item in board._menu_items(session.state, 1) if item]  # read-only, but it can still be copied
    session.choose("Copy SVG")
    got = copied(session.output)
    assert got["text/plain"].startswith(b"<?xml") and got["image/svg+xml"] == got["text/plain"]


def test_edit_in_the_menu_opens_the_editor_with_the_notes():
    text = annotate.write("CCO", {"atoms": [[0, "pink", "end"]], "bonds": [], "show": []})
    session = List(f"{text} ethanol")
    session.right_click(0).choose("Edit")
    drawing = session.state["editing"]["state"]
    assert drawing["notes"]["atoms"] == [[1, "pink", "end"]]
    session.send("\x11")  # Done, with nothing changed
    assert session.state["items"][0][4]["atoms"] == [[0, "pink", "end"]] and "No change" in session.output


def test_an_annotation_made_in_the_editor_comes_back_to_the_list():
    session = List("OCC ethanol")
    session.right_click(0).choose("Edit")
    b = session.state
    drawing = b["editing"]["state"]
    drawing["notes"]["atoms"] = [[1, "green", "the O"]]
    session.text = board._encode(b)
    session.send("\x11")
    item = session.state["items"][0]
    assert item[3] and item[1] == "CCO" and item[4]["atoms"] == [[2, "green", "the O"]]  # written in RDKit's order, the O last


def test_the_list_menu_toggles_numbers_and_clears_annotations():
    session = List("CCO ethanol")
    session.right_click(0).choose("Show Stereo Labels")
    assert session.state["items"][0][4]["show"] == ["cip"] and session.state["items"][0][3]
    session.right_click(0)
    assert "✓ Show Stereo Labels" in session.output
    session.choose("Clear Annotations")
    assert annotate.is_empty(session.state["items"][0][4])


def test_the_list_menu_works_from_the_keyboard():
    session = List("CCO ethanol")
    session.send("\x1b[C").send("/")
    assert session.state["menu"]["index"] == 0
    session.send("\x1b[B").send("\x1b[B").send("\x1b[B").send("\r")  # Edit, then a line, then Copy SMILES and Copy SVG
    assert copied(session.output)["text/plain"].startswith(b"<?xml")
    session.send("/").send("x")
    assert session.state["menu"] is None


def test_a_click_outside_the_menu_closes_it_and_a_right_click_elsewhere_moves_it():
    session = List("CCO a\nCCN b\nCCC c")
    session.right_click(0)
    session.right_click(2)
    assert session.state["menu"]["index"] == 2
    session.send("\x1b[<0;1;1M\x1b[<0;1;1m")
    assert session.state["menu"] is None and session.state["editing"] is None


def test_the_list_takes_the_terminals_answer_to_a_copy():
    session = List("CCO a")
    session.send("\x1b]5522;type=write:status=DONE\x1b\\")
    assert "pictures and all" in session.output and session.status == editor.CONTINUE


def test_a_long_menu_stays_above_the_status_lines():
    s = Session("CCO").state
    s["menu"], s["menu_target"] = ["context", "atom", 50, UI.rows - 3], ["a", 3]  # opened near the bottom
    _, row, _ = editor._menu_box(s, UI)
    assert row + len(editor.CONTEXT_MENUS["atom"]) <= UI.rows - editor.STATUS_ROWS


def test_where_a_smiles_writes_each_atom_and_bond():
    smiles = "C%12CC%12Cl.[NH3+]C(=O)c1ccccc1/C=C/Br"
    atoms, bonds = annotate.smiles_parts(smiles)
    mol = Chem.MolFromSmiles(smiles)
    assert len(atoms) == mol.GetNumAtoms()
    assert [smiles[a:b] for a, b in atoms][:6] == ["C", "C", "C", "Cl", "[NH3+]", "C"]
    pairs = {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in mol.GetBonds()}
    assert set(bonds) == pairs  # every bond, and nothing else
    assert [smiles[a:b] for a, b in bonds[(0, 2)]] == ["%12", "%12"] and [smiles[a:b] for a, b in bonds[(5, 6)]] == ["="]
    assert [smiles[a:b] for a, b in bonds[(7, 12)]] == ["1", "1"] and bonds[(4, 5)] == []


def test_a_highlighted_group_is_painted_in_the_smiles_as_one():
    smiles = "CC(C)(C)OC(=O)N"  # Boc-NH2: the tert-butyl highlighted, with its bonds
    notes = {"atoms": [[i, "blue", ""] for i in range(4)], "bonds": [[0, 1, "blue", ""], [1, 2, "blue", ""], [1, 3, "blue", ""]], "show": []}
    painted = annotate.smiles_paint(smiles + " |atomProp:0.atomNote.tBu|", notes)
    assert painted == {"smiles": smiles, "marks": [[0, 8, "blue"]]}  # CC(C)(C), its parentheses too
    notes = {"atoms": [[4, "pink", ""], [5, "pink", ""], [6, "pink", ""]], "bonds": [[4, 5, "pink", ""], [5, 6, "pink", ""]], "show": []}
    painted = annotate.smiles_paint(smiles, notes)
    assert [smiles[a:b] for a, b, _ in painted["marks"]] == ["OC(=O)"]  # the ester: its double bond and its branch's parentheses
    notes["bonds"] = [[4, 5, "pink", ""]]  # without the C=O bond, the = isn't painted, nor the branch
    assert [smiles[a:b] for a, b, _ in annotate.smiles_paint(smiles, notes)["marks"]] == ["OC", "O"]
    assert annotate.smiles_paint("CCO", annotate.empty()) == {"smiles": "CCO", "marks": []}
