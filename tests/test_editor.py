"""The molecular editor, driven through its one entry point with the raw bytes a terminal would send."""
import base64
import json
import math
import os
import random
import re
import sys
import zlib
from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from rdkit import Chem

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import editor  # noqa: E402
from app import app  # noqa: E402

COLS, ROWS, CW, CH = 120, 40, 10, 20
UI = editor.Layout(COLS, ROWS, CW, CH)
IMAGE = re.compile("\x1b_G(a=T[^;]*);(.*?)(?=\x1b\\[|\x1b_Ga=d|$)", re.S)
CHUNK = re.compile("\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\\\")
MOTION_FAR = f"\x1b[<35;{COLS - 2};{ROWS - 4}M"  # the pointer over a far corner of the canvas


class Session:
    def __init__(self, smiles=""):
        status, self.text, self.output = editor.step("", smiles, "", COLS, ROWS, CW, CH)
        assert status == editor.CONTINUE

    def send(self, raw):
        self.status, text, self.output = editor.step(self.text, "", raw, COLS, ROWS, CW, CH)
        if text:
            self.text = text
        return self

    @property
    def state(self):
        return editor.decode(self.text)

    @property
    def smiles(self):
        return editor.analyse(self.state)[0]

    def keys(self, *names):
        for name in names:
            self.send(name)
        return self

    def cell(self, x, y):
        """The screen position, counting from 1, of a point in the molecule's coordinates."""
        col, row = UI.to_cell(self.state, x, y)
        return round(col + 0.5), round(row + 0.5)

    def click(self, col, row, button=0):
        return self.send(f"\x1b[<{button};{col};{row}M\x1b[<{button};{col};{row}m")

    def click_at(self, x, y, button=0):
        return self.click(*self.cell(x, y), button)

    def hover(self, x, y):
        col, row = self.cell(x, y)
        return self.send(f"\x1b[<35;{col};{row}M")

    def atom(self, index):
        return self.state["atoms"][index]

    def click_atom(self, index, button=0):
        return self.click_at(self.atom(index)[2], self.atom(index)[3], button)

    def click_bond(self, first, second, button=0):
        a, b = self.atom(first), self.atom(second)
        return self.click_at((a[2] + b[2]) / 2, (a[3] + b[3]) / 2, button)

    def hover_atom(self, index):
        return self.hover(*self.atom(index)[2:4])

    def hover_bond(self, first, second):
        a, b = self.atom(first), self.atom(second)
        return self.hover((a[2] + b[2]) / 2, (a[3] + b[3]) / 2)

    def drag(self, start, end):
        self.send(f"\x1b[<0;{start[0]};{start[1]}M")
        self.send(f"\x1b[<32;{end[0]};{end[1]}M")
        return self.send(f"\x1b[<0;{end[0]};{end[1]}m")

    def tool(self, name):
        index = next(i for i, entry in enumerate(editor.PALETTE) if entry[0] == name)
        col = index % UI.per_row * UI.button_cols + 2
        row = index // UI.per_row * 2 + UI.top + 1
        return self.click(col, row)

    def menu(self, title, label):
        col = next(start for start, _, index in UI.titles if editor.MENUS[index][0] == title) + 2
        self.click(col, 1)
        return self.pick(label)

    def pick(self, label):
        """Click an item in the open menu."""
        state = self.state
        start_col, start_row, _ = editor._menu_box(state, UI)
        index = next(i for i, item in enumerate(editor._menu_items(state)) if item and item[0] == label)
        return self.click(start_col + 2, start_row + index + 1)

    def label(self, text):
        return self.send(text).send("\r")


def overvalent():
    """An oxygen with three bonds, left that way while drawing."""
    session = Session().click(60, 20)
    session.hover_atom(0).keys("o")
    for _ in range(2):
        session.click_atom(0)
    return session


def pictures(output):
    """Each image in some output, by its id."""
    found, current, data = {}, None, []
    for keys, chunk in CHUNK.findall(output):
        if "a=d" in keys:
            continue
        if "a=T" in keys:
            current, data = int(re.search(r"i=(\d+)", keys)[1]), []
        data.append(chunk)
        if "m=0" in keys:
            found[current] = Image.open(BytesIO(base64.b64decode("".join(data)))).convert("RGBA")
    return found


def layer(output, name):
    ids = [editor._IDS[name], editor._IDS[name] + 1]
    return next((image for ident, image in pictures(output).items() if ident in ids), None)


def canonical(smiles):
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


# drawing with the bond tool, which is what it starts with


def test_starts_empty_and_says_how_to_begin():
    session = Session()
    assert session.state["atoms"] == [] and "Click to draw a bond" in session.output and session.state["tool"] == "bond"


def test_a_click_in_empty_space_draws_a_whole_bond_from_there():
    session = Session().click(60, 20)
    assert session.smiles == "CC" and session.cell(*session.atom(0)[2:4]) == (60, 20)


def test_clicking_an_atom_grows_a_bond():
    assert Session().click(60, 20).click_atom(1).smiles == "CCC"


def test_a_zigzag_keeps_atoms_apart():
    session = Session().click(60, 20)
    for index in range(1, 8):
        session.click_atom(index)
    atoms = session.state["atoms"]
    assert min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i]) > 0.9


def test_clicking_a_bond_steps_through_its_orders():
    session = Session().click(60, 20)
    for expected in ("C=C", "C#C", "CC"):
        session.click_bond(0, 1)
        assert session.smiles == expected


def test_the_double_bond_tool_draws_and_sets_doubles():
    session = Session().tool("double").click(60, 20)
    assert session.smiles == "C=C"
    session.tool("single").click(90, 20).tool("double").click_bond(2, 3)
    assert session.smiles == "C=C.C=C"


def test_the_tool_stays_chosen():
    session = Session().tool("double").click(60, 20).click_atom(1)
    assert session.state["tool"] == "bond" and session.state["bond"] == editor.DOUBLE


def test_drag_from_an_atom_draws_a_bond_at_a_snapped_angle():
    session = Session().click(60, 20)
    start = session.cell(*session.atom(1)[2:4])
    session.drag(start, (start[0] + 5, start[1] + 1))
    a, b = session.atom(1), session.atom(2)
    angle = np.degrees(np.arctan2(b[3] - a[3], b[2] - a[2]))
    assert session.smiles == "CCC" and abs(angle / 30 - round(angle / 30)) < 1e-6
    assert abs(np.hypot(b[2] - a[2], b[3] - a[3]) - 1) < 1e-6


def test_a_pixel_pointer_snaps_to_finer_angles():
    session = Session().click(60, 20)
    x, y = session.atom(1)[2:4]
    col, row = UI.to_cell(session.state, x, y)
    px, py = int(col * CW), int(row * CH)
    # 15 degrees below the horizontal is a step only the finer snap has
    events = f"\x1b[<0;{px};{py}M\x1b[<32;{px + 100};{py + 27}M\x1b[<0;{px + 100};{py + 27}m"
    _, text, _ = editor.step(session.text, "", events, COLS, ROWS, CW, CH, pixels=True)
    atoms = editor.decode(text)["atoms"]
    angle = np.degrees(np.arctan2(atoms[2][3] - atoms[1][3], atoms[2][2] - atoms[1][2]))
    assert abs(angle + 15) < 1e-6


def test_drag_onto_another_atom_joins_them():
    session = Session().click(60, 20).click(90, 20)
    assert session.smiles == "CC.CC"
    session.drag(session.cell(*session.atom(1)[2:4]), session.cell(*session.atom(2)[2:4]))
    assert session.smiles == "CCCC" and len(session.state["atoms"]) == 4


def test_closing_a_ring_by_dragging():
    session = Session().click(60, 20)
    for index in range(1, 5):
        session.click_atom(index)
    session.drag(session.cell(*session.atom(5)[2:4]), session.cell(*session.atom(0)[2:4]))
    assert session.smiles == "C1CCCCC1" and len(session.state["atoms"]) == 6


def test_drag_from_empty_space_draws_a_bond_with_both_atoms():
    assert Session().drag((60, 20), (66, 20)).smiles == "CC"


def test_drag_shows_a_preview_and_changes_nothing_until_release():
    session = Session().click(60, 20)
    start = session.cell(*session.atom(1)[2:4])
    session.send(f"\x1b[<0;{start[0]};{start[1]}M")
    session.send(f"\x1b[<32;{start[0] + 8};{start[1]}M")
    assert len(session.state["atoms"]) == 2 and layer(session.output, "overlay") is not None
    assert layer(session.output, "structure") is None


def test_hovering_an_atom_previews_the_bond_a_click_would_add():
    session = Session().click(60, 20).hover_atom(1)
    assert any(mark[0] == "line" and mark[4] == 0.5 for mark in editor._marks(session.state, UI, []))
    assert len(session.state["atoms"]) == 2


def test_hovering_with_a_ring_tool_previews_the_ring_and_changes_nothing():
    session = Session().tool("benzene").send("\x1b[<35;60;20M")
    marks = editor._marks(session.state, UI, [])
    assert len([m for m in marks if m[0] == "line"]) == 6 and session.state["atoms"] == []


# the other tools


def test_ring_tool_in_empty_space_is_centred_on_the_click():
    session = Session().tool("benzene").click(60, 20)
    xs, ys = [a[2] for a in session.state["atoms"]], [a[3] for a in session.state["atoms"]]
    assert session.smiles == "c1ccccc1"
    assert session.cell(sum(xs) / 6, sum(ys) / 6) == (60, 20)


@pytest.mark.parametrize("name, sides", [("ring3", 3), ("ring4", 4), ("ring5", 5), ("ring6", 6), ("ring7", 7)])
def test_rings_are_regular_with_bonds_of_one(name, sides):
    state = Session().tool(name).click(60, 20).state
    by_id = {a[0]: a for a in state["atoms"]}
    lengths = [np.hypot(by_id[b[0]][2] - by_id[b[1]][2], by_id[b[0]][3] - by_id[b[1]][3]) for b in state["bonds"]]
    assert len(lengths) == sides and max(lengths) - min(lengths) < 1e-6 and abs(lengths[0] - 1) < 1e-6


def test_fusing_benzene_onto_a_bond_makes_naphthalene():
    session = Session().tool("benzene").click(60, 20)
    bond = session.state["bonds"][0]
    ids = [a[0] for a in session.state["atoms"]]
    session.click_bond(ids.index(bond[0]), ids.index(bond[1]))
    assert session.smiles == "c1ccc2ccccc2c1" and len(session.state["atoms"]) == 10


def test_a_fused_ring_goes_to_the_side_without_atoms():
    session = Session().tool("ring6").click(60, 20)
    centre = np.mean([[a[2], a[3]] for a in session.state["atoms"]], axis=0)
    session.click_bond(0, 1)
    new = session.state["atoms"][6:]
    assert len(new) == 4
    a, b = session.atom(0), session.atom(1)
    middle = np.array([(a[2] + b[2]) / 2, (a[3] + b[3]) / 2])
    assert all(np.dot(np.array([n[2], n[3]]) - middle, middle - centre) > 0 for n in new)


def test_three_fused_rings_reuse_the_atoms_they_meet():
    session = Session().tool("ring6").click(60, 20)
    session.click_bond(0, 1)
    session.click_bond(1, 2)
    atoms = session.state["atoms"]
    assert min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i]) > 0.5


def test_ring_tool_on_an_atom_shares_it():
    assert Session().click(60, 20).tool("benzene").click_atom(1).smiles == canonical("Cc1ccccc1")


def test_ring_tool_on_a_ring_atom_makes_a_spiro_link_and_alt_sprouts():
    assert Session("C1CCCCC1").tool("ring5").click_atom(0).smiles == canonical("C1CCC2(CC1)CCCC2")
    assert Session("C1CCCCC1").tool("ring5").click_atom(0, button=8).smiles == canonical("C1CCC(C2CCCC2)CC1")


def test_cyclopentadiene_has_its_double_bonds():
    assert Session().tool("cyclopentadiene").click(60, 20).smiles == "C1=CCC=C1"


def test_a_ring_still_lands_if_the_hand_wobbles():
    assert Session().tool("ring5").drag((60, 20), (66, 20)).smiles == "C1CCCC1"


def test_eraser_deletes_atoms_and_bonds():
    session = Session("CCC").tool("erase").click_atom(2)
    assert session.smiles == "CC"
    session.click_bond(0, 1)
    assert session.smiles == "C.C"


def test_eraser_takes_a_bond_down_one_order():
    session = Session("C#C").tool("erase").click_bond(0, 1)
    assert session.smiles == "C=C"
    assert session.click_bond(0, 1).smiles == "CC"
    assert session.click_bond(0, 1).smiles == "C.C"


def test_charge_tools():
    session = Session("CN").tool("plus")
    session.click_atom(1)
    assert session.smiles == "C[NH3+]"
    session.tool("minus").click_atom(1).click_atom(1)
    assert session.smiles == "C[NH-]"


def test_charge_tool_needs_an_atom():
    assert "Point at an atom" in Session().tool("plus").click(60, 20).output


def test_pan_tool_moves_the_view_not_the_atoms():
    session = Session("CC").tool("pan")
    x, y = session.state["view"][:2]
    atoms = session.state["atoms"]
    session.drag((100, 30), (90, 30))
    assert session.state["view"][0] == pytest.approx(x + 10 / session.state["view"][2]) and session.state["view"][1] == y
    assert session.state["atoms"] == atoms


def test_middle_button_pans_with_any_tool():
    session = Session("CC")
    x = session.state["view"][0]
    session.send("\x1b[<1;100;30M\x1b[<33;90;30M\x1b[<1;90;30m")
    assert session.state["view"][0] > x and session.smiles == "CC"


def test_wheel_zooms_about_the_pointer():
    session = Session().click(60, 20)
    before = session.cell(*session.atom(0)[2:4])
    zoom = session.state["view"][2]
    session.send("\x1b[<64;60;20M")
    assert session.state["view"][2] > zoom and session.cell(*session.atom(0)[2:4]) == before
    session.send("\x1b[<65;60;20M\x1b[<65;60;20M")
    assert session.state["view"][2] < zoom


# selecting and moving


def test_selection_tool_moves_an_atom_and_undo_puts_it_back():
    session = Session("CC").tool("select")
    before = session.atom(0)[2:4]
    start = session.cell(*before)
    session.drag(start, (start[0], start[1] + 5))
    assert session.atom(0)[2:4] != before and session.atom(1)[2:4] != session.atom(0)[2:4] and session.smiles == "CC"
    session.send("\x1a")
    assert session.atom(0)[2:4] == before


def test_selection_tool_moves_a_bond_with_both_ends():
    session = Session("CCC").tool("select")
    a0, a1, a2 = (session.atom(i)[2:4] for i in range(3))
    bond_mid = session.cell((a0[0] + a1[0]) / 2, (a0[1] + a1[1]) / 2)
    session.drag(bond_mid, (bond_mid[0] + 12, bond_mid[1]))
    assert session.atom(0)[2] > a0[0] and session.atom(1)[2] > a1[0] and session.atom(2)[2:4] == a2


def marquee_all(session):
    session.tool("select")
    return session.drag((UI.left + 2, UI.top + 2), (COLS - 2, UI.top + UI.canvas_rows - 2))


def test_dragging_across_empty_space_selects():
    session = marquee_all(Session("CC.CCC"))
    assert len(session.state["selected"]) == 5 and session.smiles == "CC.CCC"


def test_a_marquee_selects_only_what_it_covers():
    session = Session().click(40, 20).click(100, 20).tool("select")
    session.drag((30, 12), (60, 28))
    assert sorted(session.state["selected"]) == [session.atom(0)[0], session.atom(1)[0]]


def test_the_marquee_shows_while_dragging():
    session = Session("CC").tool("select").send(f"\x1b[<0;{UI.left + 2};3M").send(f"\x1b[<32;{COLS - 2};30M")
    assert any(mark[0] == "rect" for mark in editor._marks(session.state, UI, []))


def test_dragging_a_selection_moves_all_of_it():
    session = marquee_all(Session("CC.CCC"))
    before = [a[2:4] for a in session.state["atoms"]]
    start = session.cell(*session.atom(0)[2:4])
    session.drag(start, (start[0] + 6, start[1]))
    after = [a[2:4] for a in session.state["atoms"]]
    assert all(b[0] > a[0] and b[1] == pytest.approx(a[1]) for a, b in zip(before, after))
    assert after[0][0] - before[0][0] == pytest.approx(after[4][0] - before[4][0])
    session.send("\x1a")
    assert [a[2:4] for a in session.state["atoms"]] == before


def test_delete_removes_the_selection():
    assert marquee_all(Session("CC.CCC")).send("\x7f").smiles == ""


def test_select_all_key_and_arrows_nudge_it():
    session = Session("CC").send("\x01")
    assert len(session.state["selected"]) == 2
    x = session.atom(0)[2]
    assert session.send("\x1b[C").atom(0)[2] == pytest.approx(x + 0.25)


def test_a_click_clears_the_selection():
    session = marquee_all(Session("CC"))
    session.click(COLS - 3, ROWS - 4)
    assert session.state["selected"] == []


def test_selection_survives_undo_only_where_atoms_do():
    session = Session().click(60, 20).send("\x01").click(90, 20)
    session = session.send("\x1a")
    assert all(editor._atom(session.state, i) for i in session.state["selected"])


# keys act on the hotspot, as in the tables in chapter 5 of the ChemDraw 17.1 user guide


@pytest.mark.parametrize("key, symbol", [("c", "C"), ("n", "N"), ("w", "N"), ("o", "O"), ("q", "O"), ("s", "S"), ("S", "Si"), ("p", "P"), ("f", "F"), ("l", "Cl"),
                                         ("C", "Cl"), ("b", "Br"), ("B", "B"), ("i", "I"), ("h", "H"), ("k", "K"), ("N", "Na")])
def test_atom_labels_from_the_table(key, symbol):
    session = Session("CC").hover_atom(1).keys(key)
    assert session.atom(1)[1] == symbol and session.atom(0)[1] == "C"


@pytest.mark.parametrize("key, smiles", [("m", "CC"), ("e", "CCC"), ("A", "CC(C)=O"), ("P", "Cc1ccccc1"), ("E", "CC(=O)OC"), ("F", "CC(F)(F)F")])
def test_atom_groups_from_the_table(key, smiles):
    assert Session("CC").hover_atom(1).keys(key).smiles == canonical(smiles)


# the kinds of carbon that the table tells apart, each with the atom to press on
PRIMARY, SECONDARY, TERTIARY, AROMATIC = ("CC", 1), ("CCC", 1), ("CC(C)C", 1), ("c1ccccc1", 0)


@pytest.mark.parametrize("key, start, smiles", [
    ("1", PRIMARY, "CCC"), ("0", PRIMARY, "CCC"), ("1", AROMATIC, "Cc1ccccc1"),
    ("2", PRIMARY, "CC(C)=O"), ("2", SECONDARY, "CC(C)=O"), ("2", TERTIARY, "CC(=O)C(C)(C)C"), ("2", AROMATIC, "CC(=O)c1ccccc1"),
    ("3", PRIMARY, "Cc1ccccc1"), ("a", PRIMARY, "Cc1ccccc1"), ("3", SECONDARY, "CC(C)c1ccccc1"), ("3", AROMATIC, "c1ccc(-c2ccccc2)cc1"),
    ("6", PRIMARY, "CC1CCCCC1"), ("6", SECONDARY, "CC1(C)CCCCC1"), ("6", TERTIARY, "CC(C)(C)C1CCCCC1"), ("6", AROMATIC, "c1ccc(C2CCCCC2)cc1"),
    ("7", PRIMARY, "CC1CCCC1"), ("7", SECONDARY, "CC1(C)CCCC1"), ("7", AROMATIC, "c1ccc(C2CCCC2)cc1"),
    ("8", PRIMARY, "C=CC"), ("9", PRIMARY, "CC(C)C"), ("9", SECONDARY, "CC(C)(C)C"), ("9", TERTIARY, "CC(C)C(C)(C)C"), ("9", AROMATIC, "CC(C)c1ccccc1"),
    ("z", PRIMARY, "C#CCC"), ("z", AROMATIC, "C#Cc1ccccc1"),
])
def test_sprouting_from_each_kind_of_carbon(key, start, smiles):
    session = Session(start[0]).hover_atom(start[1]).keys(key)
    assert session.smiles == canonical(smiles)
    assert session.keys("\x1a").smiles == canonical(start[0])  # however many atoms it took, one undo


def smallest_angle(state, bonds=(2, 3)):
    """The smallest angle, in degrees, between two bonds at any atom of a drawing with as many bonds as one of `bonds`."""
    atoms = {a[0]: a for a in state["atoms"]}
    around = {}
    for first, second, _ in state["bonds"]:
        around.setdefault(first, []).append(second)
        around.setdefault(second, []).append(first)
    smallest = 360
    for ident, neighbours in around.items():
        here = atoms[ident]
        directions = sorted(math.degrees(math.atan2(atoms[n][3] - here[3], atoms[n][2] - here[2])) % 360 for n in neighbours)
        for i in range(len(directions) if len(directions) in bonds else 0):
            smallest = min(smallest, (directions[(i + 1) % len(directions)] - directions[i]) % 360)
    return smallest


@pytest.mark.parametrize("key", list("1234567890az"))
@pytest.mark.parametrize("start", [PRIMARY, SECONDARY, TERTIARY, AROMATIC], ids=["primary", "secondary", "tertiary", "aromatic"])
def test_sprouts_keep_their_angles_open(key, start):
    # what a sprout adds leaves the atoms at a chemist's angles: 120 degrees in a chain or a ring of six, 108 in a ring of five, and
    # never folded back onto the bonds that were there, as pressing 2 on the end of a chain once did. A fourth bond on a carbon that
    # already has three, or a ring or two methyls on one with two, can only go into the room between them, as in ChemDraw
    session = Session(start[0]).hover_atom(start[1]).keys(key)
    assert smallest_angle(session.state, bonds=(2, 3)) >= 100
    assert smallest_angle(session.state, bonds=(4,)) >= 55


@pytest.mark.parametrize("key, kind", [("4", editor.WEDGE), ("5", editor.HASH)])
def test_wedge_and_hash_sprout_from_the_hotspot(key, kind):
    session = Session("CC").hover_atom(1).keys(key)
    assert session.state["bonds"][-1] == [session.atom(1)[0], session.atom(2)[0], kind]
    session = Session("c1ccccc1").hover_atom(0).keys(key)
    assert session.state["bonds"][-1][2] == kind and len(session.state["atoms"]) == 8  # a new bond first, from a ring


def test_methylidene_may_leave_a_wrong_valence_on_purpose():
    assert Session(TERTIARY[0]).hover_atom(TERTIARY[1]).keys("8").smiles is None


def test_the_hotspot_goes_on_to_the_sprouted_atom():
    assert Session("CC").hover_atom(1).keys("1", "1", "1").smiles == "CCCCC"
    assert Session("CC").hover_atom(1).keys("1", "o").smiles == "CCO"


def test_repeated_zero_curls_into_a_ring_and_one_makes_a_zigzag():
    assert Session("CC").hover_atom(1).keys(*"00000").smiles == "C1CCCCC1"
    session = Session("CC").hover_atom(1).keys(*"11111")
    atoms = session.state["atoms"]
    assert session.smiles == "CCCCCCC" and min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i]) > 0.9


@pytest.mark.parametrize("key", list("3a67"))
def test_holding_a_ring_key_makes_one_ring(key):
    assert Chem.MolFromSmiles(Session("CC").hover_atom(1).send(key * 8).smiles).GetRingInfo().NumRings() == 1


@pytest.mark.parametrize("key", list("456789"))
def test_ring_keys_over_nothing_place_nothing_and_say_why(key):
    session = Session().keys(key, key, key)
    assert session.state["atoms"] == [] and "under the pointer" in session.state["message"]


def test_a_key_over_nothing_leaves_the_last_atom_alone():
    session = Session().click(60, 20).send(MOTION_FAR).keys("o")
    assert session.smiles == "CC" and "under the pointer" in session.output


def test_the_atom_just_drawn_has_the_hotspot_until_the_mouse_moves():
    assert Session().click(60, 20).keys("o").smiles == "CO"


def test_sprouting_leaves_the_tool_alone():
    session = Session("CC").tool("double").hover_atom(1).keys("6")
    assert session.state["tool"] == "bond" and session.state["bond"] == editor.DOUBLE


@pytest.mark.parametrize("key, smiles", [("1", "CC"), ("2", "C=C"), ("3", "C#C")])
def test_bond_order_keys(key, smiles):
    assert Session("C=C").hover_bond(0, 1).keys(key).smiles == smiles


def test_wedge_and_hash_keys_on_a_bond():
    def carbon_methyl(session):
        atoms = session.state["atoms"]
        carbon = next(i for i, a in enumerate(atoms) if a[1] == "C" and len(editor._neighbours(session.state, a[0])) == 3)
        return carbon, next(i for i, a in enumerate(atoms) if a[1] == "C" and i != carbon)
    wedged, hashed = Session("CC(N)O"), Session("CC(N)O")
    wedged.hover_bond(*carbon_methyl(wedged)).keys("w")
    hashed.hover_bond(*carbon_methyl(hashed)).keys("h")
    assert "@" in wedged.smiles and "@" in hashed.smiles and wedged.smiles != hashed.smiles


def test_pressing_wedge_again_turns_it_round():
    session = Session("CC").hover_bond(0, 1).keys("w")
    first = session.state["bonds"][0][:2]
    assert session.keys("w").state["bonds"][0][:2] == first[::-1]


@pytest.mark.parametrize("key, smiles", [("a", "c1ccc2ccccc2c1"), ("z", "C1=Cc2ccccc2C1"), ("v", "c1ccc2c(c1)C2"), ("4", "c1ccc2c(c1)CC2"), ("5", "c1ccc2c(c1)CCC2"),
                                         ("6", "c1ccc2c(c1)CCCC2"), ("7", "c1ccc2c(c1)CCCCC2"), ("8", "c1ccc2c(c1)CCCCCC2")])
def test_ring_keys_fuse_on_a_bond(key, smiles):
    assert Session("c1ccccc1").hover_bond(0, 1).keys(key).smiles == canonical(smiles)


def test_the_same_key_means_different_things_on_an_atom_and_a_bond():
    assert Session("CC").hover_atom(1).keys("3").smiles == canonical("Cc1ccccc1")
    assert Session("CC").hover_bond(0, 1).keys("3").smiles == "C#C"


@pytest.mark.parametrize("start, key", [(0, "'"), (1, "d"), (1, "b"), (1, "B"), (1, "H"), (1, "l"), (1, "c"), (1, "r"), (1, "f"), (1, "9")])
def test_chemdraw_keys_for_what_this_editor_lacks_say_so_and_change_nothing(start, key):
    session = Session("CCC")
    (session.hover_bond(0, 1) if start else session.hover_atom(0)).keys(key)
    assert session.smiles == "CCC" and "this editor doesn't" in session.state["message"]


@pytest.mark.parametrize("key, action", [("x", ("bond", editor.SINGLE)), ("t", ("tool", "text")), (" ", ("tool", "select")), ("j", ("ring", "benzene")),
                                         ("J", ("ring", "cyclopentadiene")), ("u", ("ring", 6)), ("U", ("ring", 5))])
def test_over_nothing_keys_choose_tools(key, action):
    session = Session("CC").send(MOTION_FAR).keys("\x1a" if key == "x" else key)
    if key == "x":
        session.tool("select").keys("x")
    assert editor._is_active(session.state, action) and session.smiles == "CC"


@pytest.mark.parametrize("key", list("TeEgGlLkK"))
def test_tools_this_editor_lacks_say_so(key):
    assert "this editor doesn't" in Session().keys(key).state["message"]


def test_u_then_a_click_makes_one_cyclohexane():
    assert Session().keys("u").click(60, 20).smiles == "C1CCCCC1"


@pytest.mark.parametrize("key", ["\x7f", "\x1b[3~", " "])
def test_keys_that_remove_a_label_leave_carbon(key):
    assert Session("CO").hover_atom(1).keys(key).smiles == "CC"
    assert Session("C[NH3+]").hover_atom(1).keys(key).smiles == "CC"


def test_delete_on_plain_carbon_or_a_bond_removes_it_and_space_does_not():
    assert Session("CCC").hover_atom(0).keys("\x7f").smiles == "CC"
    assert Session("CCC").hover_bond(0, 1).keys("\x7f").smiles == "C.CC"
    assert Session("CCC").hover_atom(0).keys(" ").smiles == "CCC"
    assert Session("CCC").hover_bond(0, 1).keys(" ").smiles == "CCC"


def test_delete_needs_something_to_act_on():
    assert "Point at" in Session().keys("\x7f").output


def test_charge_keys():
    assert Session("CN").hover_atom(1).keys("+").smiles == "C[NH3+]"
    assert Session("CO").hover_atom(1).keys("-").smiles == "C[O-]"


@pytest.mark.parametrize("key", ["\r", "="])
def test_enter_and_equals_open_the_label_box(key):
    assert Session("CC").hover_atom(0).keys(key).state["label"]


@pytest.mark.parametrize("key", ["/", "?"])
def test_properties_keys_open_the_menu_of_the_hotspot(key):
    assert Session("CC").hover_atom(0).keys(key).state["menu"][:2] == ["context", "atom"]
    assert Session("CC").hover_bond(0, 1).keys(key).state["menu"][:2] == ["context", "bond"]


def test_a_letter_labels_every_selected_atom():
    session = marquee_all(Session("CC(C)(C)C")).send(MOTION_FAR).keys("n")
    assert {a[1] for a in session.state["atoms"]} == {"N"}
    assert session.keys("\x1a").smiles == "CC(C)(C)C"


def test_shift_click_adds_to_the_selection_and_click_replaces_it():
    session = Session("CCC").tool("select")
    col, row = session.cell(*session.atom(0)[2:4])
    session.click(col, row)
    assert session.state["selected"] == [session.atom(0)[0]]
    col, row = session.cell(*session.atom(2)[2:4])
    session.click(col, row, button=4)
    assert session.state["selected"] == [session.atom(0)[0], session.atom(2)[0]]
    session.click(col, row)
    assert session.state["selected"] == [session.atom(2)[0]]


def test_the_hotspot_looks_as_in_chemdraw():
    atom = editor._marks(Session("CC").hover_atom(0).state, UI, [])
    assert [m[0] for m in atom if m[0] == "spot"] == ["spot"]
    bond = editor._marks(Session("CC").hover_bond(0, 1).state, UI, [])
    assert any(m[0] == "line" and m[3] == list(editor.HOTSPOT) for m in json.loads(json.dumps(bond)))


def test_a_key_prefers_the_pointer_to_the_arrow_choice():
    session = Session("CC").keys("\t")
    assert session.state["focus"][1] == session.atom(0)[0]
    session.hover_atom(1).keys("n")
    assert session.atom(1)[1] == "N" and session.atom(0)[1] == "C"


def test_moving_the_mouse_takes_the_hotspot_from_the_arrows():
    session = Session("CC").keys("\t").send(MOTION_FAR)
    assert session.state["focus"] is None and session.keys("n").smiles == "CC"


# without a mouse


def test_arrows_go_from_atom_to_bond_to_atom():
    session = Session("CCCCCCC").keys("\x1b[C")
    kinds = [session.state["focus"][0]]
    for _ in range(3):
        kinds.append(session.keys("\x1b[C").state["focus"][0])
    assert kinds == ["a", "b", "a", "b"]


def test_shift_arrows_go_from_atom_to_atom():
    session = Session("CCC").keys("\x1b[D")
    start = session.state["focus"]
    session.keys("\x1b[1;2C")
    assert session.state["focus"][0] == "a" and session.state["focus"] != start


def test_a_drawing_can_be_made_without_the_mouse():
    session = Session().keys("\r").send("C").send("\r").keys("\x1b[C", "1", "1", "o")
    assert session.smiles == "CCO"


def test_tab_visits_atoms_then_bonds_and_wraps():
    session = Session("CC")
    seen = [json.dumps(session.send("\t").state["focus"]) for _ in range(3)]
    assert len(set(seen)) == 3 and json.dumps(session.send("\t").state["focus"]) == seen[0]
    assert json.dumps(session.send("\x1b[Z").state["focus"]) == seen[-1]


def test_triple_bonds_are_straight():
    session = Session("CC").hover_atom(1).keys("z")
    (ax, ay), (bx, by), (cx, cy) = [(a[2], a[3]) for a in session.state["atoms"][1:4]]
    assert abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) < 1e-6


def test_undo_and_redo_step_through_every_change():
    session = Session("CC").hover_atom(1).keys("1", "n")
    session.send("\x1a")
    assert session.smiles == "CCC"
    session.send("\x1a")
    assert session.smiles == "CC"
    session.keys("\x19", "\x19")
    assert session.smiles == "CCN"
    assert "Nothing" in session.keys("\x19").output


def test_undo_gives_the_hotspot_back_to_the_atom_under_the_pointer():
    assert Session("CC").hover_atom(1).keys("1", "\x1a", "o").smiles == "CO"


def test_new_changes_clear_redo():
    assert Session("CC").hover_atom(1).keys("1", "\x1a", "1").state["redo"] == []


def test_undo_is_limited_so_the_state_stays_small():
    session = Session("CC").hover_atom(1)
    for _ in range(50):
        session.keys("1")
    assert len(session.state["undo"]) <= editor.MAX_UNDO


def test_esc_backs_out_one_level_at_a_time():
    session = Session().click(60, 20)
    assert session.keys("\x1b").state["focus"] is None and session.state["tool"] == "bond"
    assert session.keys("\x1b").state["tool"] == "select"


@pytest.mark.parametrize("key, zoom", [("\x1b[18~", 1.2), ("\x1b[19~", 1 / 1.2)])
def test_function_keys_zoom(key, zoom):
    assert Session().keys(key).state["view"][2] == pytest.approx(editor.DEFAULT_ZOOM * zoom)


def test_function_keys_fit_and_reset():
    session = Session("CCCCCCCC").keys("]", "]", "\x1b[15~")
    assert session.state["view"][2] == editor.DEFAULT_ZOOM
    assert session.keys("\x1b[17~").state["view"][2] <= editor.DEFAULT_ZOOM


def test_typing_a_smiles():
    session = Session().keys("/").send("CC(=O)O").send("\r")
    assert session.smiles == "CC(=O)O" and session.state["entry"] is None


def test_a_bad_smiles_stays_in_the_entry_with_a_message():
    session = Session().keys("/").send("C(").send("\r")
    assert session.state["entry"] == "C(" and session.state["message"] and session.state["message"][:30] in session.output


def test_esc_leaves_the_entry_without_changing_anything():
    session = Session("CC").keys("/").send("CCC").send("\x1b")
    assert session.smiles == "CC" and session.state["entry"] is None


def test_entry_takes_keys_as_text_not_commands():
    session = Session().keys("/").send("q").send("\x7f").send("n")
    assert session.status == editor.CONTINUE and session.state["entry"] == "n"


def test_zoom_keys_stay_in_range():
    session = Session()
    for _ in range(40):
        session.send("]")
    assert session.state["view"][2] == editor.ZOOM_RANGE[1]
    for _ in range(40):
        session.send("[")
    assert session.state["view"][2] == editor.ZOOM_RANGE[0]


def test_a_long_chain_keeps_the_new_atom_on_the_canvas():
    session = Session("CC").keys("]", "]", "]", "]").hover_atom(1)
    for _ in range(40):
        session.send("1")
    atom = next(a for a in session.state["atoms"] if a[0] == session.state["focus"][1])
    col, row = session.cell(atom[2], atom[3])
    assert UI.left < col <= COLS and UI.top < row <= UI.top + UI.canvas_rows


@pytest.mark.parametrize("key", ["\x03", "\x11"])
def test_quit_keys_finish_with_the_smiles(key):
    status, _, output = editor.step(Session("CCO").text, "", key, COLS, ROWS, CW, CH)
    assert (status, output) == (editor.QUIT, "CCO")


def test_quitting_an_empty_drawing_prints_nothing():
    assert editor.step(Session().text, "", "\x11", COLS, ROWS, CW, CH)[::2] == (editor.QUIT, "")


def test_quitting_an_invalid_drawing_keeps_it_open_and_says_why():
    session = overvalent().send("\x11")
    assert session.status == editor.CONTINUE and session.state["atoms"] and "valence" in session.output.lower()


def test_ctrl_c_leaves_an_invalid_drawing_and_says_why():
    status, _, output = editor.step(overvalent().text, "", "\x03", COLS, ROWS, CW, CH)
    assert status == editor.FAILED and "valence" in output.lower()


# the label box


def test_text_tool_on_an_atom_opens_a_box_there():
    session = Session("CC").tool("text").click_atom(0)
    assert session.state["label"]["target"] == ["a", session.atom(0)[0]]
    col, row = UI.to_cell(session.state, *session.atom(0)[2:4])
    assert f"\x1b[{int(row) + 1};" in session.output


def test_enter_opens_the_box_on_the_atom_under_the_pointer():
    assert Session("CC").hover_atom(0).keys("\r").state["label"]


@pytest.mark.parametrize("text, smiles", [("N", "CN"), ("Cl", "CCl"), ("cl", "CCl"), ("OH", "CO"), ("NH2", "CN"), ("OMe", "COC"), ("ome", "COC"),
                                          ("CF3", "CC(F)(F)F"), ("CO2H", "CC(=O)O"), ("COOH", "CC(=O)O"), ("CO2Me", "CC(=O)OC"), ("CN", "CC#N"),
                                          ("NO2", "C[N+](=O)[O-]"), ("Ac", "CC(C)=O"), ("Ph", "Cc1ccccc1"), ("Bn", "CCc1ccccc1"),
                                          ("tBu", "CC(C)(C)C"), ("Ts", "CS(=O)(=O)c1ccc(C)cc1"), ("Boc", "CC(=O)OC(C)(C)C"), ("Si", "C[SiH3]")])
def test_labels_become_real_atoms(text, smiles):
    session = Session("CC").hover_atom(1).keys("\r").label(text)
    assert session.smiles == canonical(smiles) and session.state["label"] is None


@pytest.mark.parametrize("text", ["Ac", "Pr", "Ts", "Cn"])
def test_a_group_wins_over_an_element_of_the_same_name(text):
    session = Session("CC").hover_atom(1).keys("\r").label(text)
    assert all(atom[1] in ("C", "H", "N", "O", "S") for atom in session.state["atoms"])


@pytest.mark.parametrize("text, smiles", [("OMe", "COc1ccccc1"), ("CF3", "FC(F)(F)c1ccccc1"), ("Ph", "c1ccc(-c2ccccc2)cc1")])
def test_a_group_on_a_ring_atom_becomes_a_substituent(text, smiles):
    session = Session().tool("benzene").click(60, 20).hover_atom(0).keys("\r").label(text)
    assert session.smiles == canonical(smiles) and session.atom(0)[1] == "C"


def test_a_group_key_on_a_ring_atom_becomes_a_substituent():
    assert Session().tool("benzene").click(60, 20).hover_atom(0).keys("F").smiles == canonical("FC(F)(F)c1ccccc1")


def test_an_element_on_a_ring_atom_replaces_it():
    assert Session().tool("benzene").click(60, 20).hover_atom(0).keys("n").smiles == "c1ccncc1"


def test_a_group_points_away_from_the_molecule_with_bonds_of_one():
    session = Session("CC").hover_atom(1).keys("\r").label("OMe")
    state = session.state
    by_id = {a[0]: a for a in state["atoms"]}
    for begin, end, _ in state["bonds"]:
        assert np.hypot(by_id[begin][2] - by_id[end][2], by_id[begin][3] - by_id[end][3]) == pytest.approx(1, abs=0.1)
    atoms = state["atoms"]
    assert min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i]) > 0.9


def test_a_label_in_empty_space_places_it_there():
    session = Session().tool("text").click(60, 20).label("Ph")
    assert session.smiles == "c1ccccc1"
    session = Session().tool("text").click(60, 20).label("N")
    assert session.smiles == "N" and session.cell(*session.atom(0)[2:4]) == (60, 20)


def test_an_unknown_label_stays_open_and_says_so():
    session = Session("CC").hover_atom(0).keys("\r").label("Zork")
    assert session.state["label"]["text"] == "Zork" and "isn't an element" in session.state["message"] and session.smiles == "CC"


def test_label_box_is_cancelled_by_esc_and_takes_keys_as_text():
    session = Session("CC").hover_atom(0).keys("\r").send("q").send("\x7f").send("x").send("\x1b")
    assert session.status == editor.CONTINUE and session.state["label"] is None and session.smiles == "CC"


def test_a_label_can_be_undone_in_one_step():
    assert Session("CC").hover_atom(1).keys("\r").label("Ph").keys("\x1a").smiles == "CC"


def test_labels_can_run_out_of_room_without_breaking():
    session = Session("C" * 198).hover_atom(0).keys("\r").label("Ts")
    assert len(session.state["atoms"]) <= editor.MAX_ATOMS and "at most" in session.output


# menus


def test_every_menu_title_opens_its_menu():
    for start, _, index in UI.titles:
        session = Session().click(start + 2, 1)
        assert session.state["menu"] == ["bar", index]
        assert editor.MENUS[index][1][0][0] in session.output


def test_menu_items_run():
    assert Session("CC").hover_atom(0).keys("\x7f").menu("Edit", "Undo").smiles == "CC"
    session = Session().menu("File", "Load SMILES...")
    assert session.state["entry"] == ""
    assert len(Session("CC").menu("Edit", "Select All").state["selected"]) == 2
    assert Session("CCC").keys("]").menu("View", "Reduce").state["view"][2] == pytest.approx(editor.DEFAULT_ZOOM)
    assert Session().menu("Help", "Keys and Mouse").state["help"]


def test_quit_is_in_the_file_menu():
    session = Session("CCO").menu("File", "Quit and print SMILES")
    assert session.status == editor.QUIT and session.output == "CCO"


def test_clean_up_is_in_the_structure_menu():
    assert Session("CCO").menu("Structure", "Clean Up").smiles == "CCO"


def test_moving_along_the_bar_switches_menus_and_clicking_away_closes():
    session = Session().click(3, 1)
    other = UI.titles[2]
    session.send(f"\x1b[<35;{other[0] + 2};1M")
    assert session.state["menu"] == ["bar", 2]
    session.click(COLS - 3, ROWS - 4)
    assert session.state["menu"] is None and session.state["atoms"] == []


def test_clicking_an_open_title_closes_it():
    assert Session().click(3, 1).click(3, 1).state["menu"] is None


def test_a_menu_covers_the_drawing_in_cells_with_a_background_and_clears_after():
    session = Session("CCO").click(3, 1)
    assert "\x1b[48;2;" in session.output and "Load SMILES" in session.output
    closed = session.send("\x1b").output
    assert "\x1b[2K" in closed and "Load SMILES" not in closed


def test_menus_work_from_the_keyboard():
    session = Session("CCO").keys("\x1b[21~")
    assert session.state["menu"] == ["bar", 0]
    session.keys("\x1b[C")
    assert session.state["menu"] == ["bar", 1]
    session.keys("\x1b[B", "\r")  # Undo, with nothing to undo
    assert session.state["menu"] is None and "Nothing" in session.output


def test_right_click_opens_the_menu_for_what_is_under_it():
    session = Session("CC")
    assert session.click_atom(0, button=2).state["menu"][:2] == ["context", "atom"]
    session = Session("CC")
    assert session.click_bond(0, 1, button=2).state["menu"][:2] == ["context", "bond"]
    assert Session("CC").click(COLS - 3, ROWS - 4, button=2).state["menu"][:2] == ["context", "empty"]


def test_right_click_changes_nothing_by_itself():
    assert Session("CC").click_atom(0, button=2).smiles == "CC"


def test_context_menu_items_act_on_what_was_clicked():
    assert Session("CC").click_atom(1, button=2).pick("N").smiles == "CN"
    assert Session("CC").click_atom(1, button=2).pick("Delete").smiles == "C"
    assert Session("CN").click_atom(1, button=2).pick("Charge +").smiles == "C[NH3+]"
    assert Session("CC").click_bond(0, 1, button=2).pick("Double").smiles == "C=C"
    assert Session("CC").click_bond(0, 1, button=2).pick("Delete").smiles == "C.C"
    assert Session("CC").click_atom(1, button=2).pick("Label...").state["label"]


def test_a_context_menu_near_the_edge_stays_on_the_screen():
    session = Session("CC").click(COLS, ROWS - 3, button=2)
    col, row, width = editor._menu_box(session.state, UI)
    assert col + width <= COLS and row + len(editor._menu_items(session.state)) <= ROWS


# the palette


def test_every_palette_button_chooses_its_tool_and_is_marked():
    for index, (name, action, _) in enumerate(editor.PALETTE):
        session = Session().tool(name)
        state = session.state
        assert editor._is_active(state, action)
        assert next(i for i, entry in enumerate(editor.PALETTE) if editor._is_active(state, entry[1])) == index


def test_hovering_a_button_names_it():
    col, row = UI.per_row * UI.button_cols - 1, UI.top + 1
    session = Session().send(f"\x1b[<35;{col};{row}M")
    assert session.state["tip"] == 1 and "Eraser" in session.output


def test_palette_is_an_image_and_only_sent_when_it_changes():
    session = Session()
    assert layer(session.output, "palette") is not None
    assert layer(session.click(60, 20).output, "palette") is None
    assert layer(session.tool("wedge").output, "palette") is not None


def test_active_button_looks_different():
    plain = np.asarray(layer(Session().output, "palette"))
    chosen = np.asarray(layer(Session().tool("benzene").output, "palette"))
    assert (plain != chosen).any()


def test_icons_are_all_different():
    names = editor._ICON_NAMES
    image = np.asarray(Image.open(BytesIO(editor.editor_icons.palette_image(names, 2, 40, 40, None, None, "dark"))))
    tiles = [image[i // 2 * 40:i // 2 * 40 + 40, i % 2 * 40:i % 2 * 40 + 40].tobytes() for i in range(len(names))]
    assert len(set(tiles)) == len(names) and all(np.frombuffer(t, np.uint8).any() for t in tiles)


@pytest.mark.parametrize("cols, rows", [(50, 14), (60, 20), (80, 24), (120, 40), (200, 60)])
def test_palette_fits_any_window(cols, rows):
    ui = editor.Layout(cols, rows)
    assert ui.palette_rows <= ui.canvas_rows and ui.left < cols and ui.canvas_cols >= 20
    spots = {(ui.palette_at(col, row)) for col in range(ui.left) for row in range(rows)}
    assert spots - {None} == set(range(len(editor.PALETTE)))


# help


def test_help_shows_and_hides_leaving_the_drawing_in_place():
    session = Session("CCO").keys("?")
    assert session.state["help"] and "Selection" in session.output and "a=T" not in session.output and "a=d" not in session.output
    assert not session.keys("x").state["help"] and session.smiles == "CCO"


def test_every_key_in_the_help_text_exists():
    tables = [editor._ATOM_ELEMENTS, editor._ATOM_GROUPS, editor._ATOM_SPROUTS, editor._BOND_KINDS, editor._BOND_RINGS, editor._GENERIC_TOOLS]
    for key in {key for table in tables for key in table} - {" "}:
        assert re.search(rf"(?<!\S){re.escape(key)}(?!\S)", editor.KEYS_HELP), key
    assert all(len(line) <= 110 for line in editor.KEYS_HELP.split("\n"))


def test_a_click_closes_help_without_drawing():
    assert Session().keys("?").click(60, 20).state["atoms"] == []


# what is sent


def test_hovering_sends_only_the_small_overlay():
    session = Session("CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1")
    structure_frame = session.output
    session.hover_atom(0)
    assert layer(session.output, "structure") is None and layer(session.output, "overlay") is not None
    assert len(session.output) < len(structure_frame) / 4


def test_hover_redraws_only_when_the_target_changes():
    session = Session().click(60, 20).send(MOTION_FAR)
    assert session.state["hover"] is None
    assert session.send(f"\x1b[<35;{COLS - 3};{ROWS - 4}M").output == ""
    col, row = session.cell(*session.atom(0)[2:4])
    assert session.send(f"\x1b[<35;{col};{row}M").state["hover"] and session.output
    assert session.send(f"\x1b[<35;{col};{row}M").output == ""


def test_leaving_an_atom_removes_the_overlay():
    session = Session("CC").hover_atom(0)
    output = session.send(MOTION_FAR).output
    assert "a=d,d=I" in output and "a=T" not in output


def test_moving_the_pointer_keeps_a_message():
    session = Session().keys("6")
    assert session.state["message"]
    assert session.send(MOTION_FAR).state["message"]


def test_pixel_reports_land_on_the_same_atom():
    session = Session().click(60, 20)
    col, row = session.cell(*session.atom(1)[2:4])
    x, y = (col - 1) * CW + CW // 2, (row - 1) * CH + CH // 2
    _, text, _ = editor.step(session.text, "", f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m", COLS, ROWS, CW, CH, pixels=True)
    assert editor.analyse(editor.decode(text))[0] == "CCC"


def test_images_are_stacked_under_cells_that_have_a_background():
    output = Session("CC").hover_atom(0).output + Session("CC").output
    for z in re.findall(r"z=(-?\d+)", output):
        assert int(z) < -1_073_741_824


def test_overlay_sits_above_the_molecule():
    assert editor._Z["overlay"] > editor._Z["structure"]


def test_atoms_are_drawn_where_clicks_find_them():
    session = Session().click(60, 20).hover_atom(1).keys("o").keys("\x1b")
    image = np.asarray(Image.open(BytesIO(editor.draw_structure(session.state, UI, "dark"))).convert("RGBA")).astype(int)
    assert image.shape[:2] == (UI.canvas_rows * CH, UI.canvas_cols * CW)
    red = (image[:, :, 0] > 200) & (image[:, :, 1] < 170) & (image[:, :, 3] > 200)
    ys, xs = np.nonzero(red)
    want = session.cell(*session.atom(1)[2:4])
    assert abs(xs.mean() / CW + UI.left + 1 - want[0]) < 2 and abs(ys.mean() / CH + UI.top + 1 - want[1]) < 1.5


def test_marks_are_drawn_on_the_atom_they_mark():
    session = Session("CC").hover_atom(0)
    image = np.asarray(layer(session.output, "overlay")).astype(int)
    ys, xs = np.nonzero(image[:, :, 3] > 40)
    want = session.cell(*session.atom(0)[2:4])
    assert abs(xs.mean() / CW + UI.left + 1 - want[0]) < 1.5 and abs(ys.mean() / CH + UI.top + 1 - want[1]) < 1


def test_mapping_holds_at_other_cell_sizes_and_zooms():
    for cell in [(8, 17), (12, 25), (20, 40)]:
        ui = editor.Layout(100, 40, *cell)
        s = editor.new_state()
        x, y = ui.to_model(s, 30.5, 18.5)
        _, text, _ = editor.step("", "", "\x1b[<0;31;19M\x1b[<0;31;19m", 100, 40, *cell)
        assert editor.decode(text)["atoms"][0][2:4] == pytest.approx([x, y])
        col, row = ui.to_cell(s, x, y)
        assert (col, row) == pytest.approx((30.5, 18.5))


def test_canvas_image_fills_the_canvas_and_keeps_the_cursor():
    keys = next(k for k, _ in CHUNK.findall(Session().output) if f"i={editor._IDS['structure']}" in k or f"i={editor._IDS['structure'] + 1}" in k)
    assert f"c={UI.canvas_cols}," in keys and f"r={UI.canvas_rows}," in keys and "C=1," in keys


def test_a_new_image_goes_up_before_the_old_comes_down():
    session = Session().click(60, 20)
    output = session.click_atom(1).output
    placed = [int(i) for i in re.findall(r"a=T[^;]*?i=(\d+)", output) if int(i) in (7001, 7002)]
    shown = ({7001, 7002} - {placed[0]}).pop() if placed else None  # what the screen showed: the other id
    assert placed and f"a=d,d=I,i={shown}," in output
    assert output.index(f"a=T,f=100,c=") < output.index(f"a=d,d=I,i={shown},")  # the shown picture goes only after the new is up
    # the new id is cleared just before, so no older placement of it can stay on the screen beside the new one
    assert output.index(f"a=d,d=I,i={placed[0]},") < output.index(f"i={placed[0]},", output.index(f"a=d,d=I,i={placed[0]},") + 1)


def test_frames_are_synchronised():
    output = Session().click(60, 20).output
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_a_wedge_is_drawn():
    plain = Session("CC(N)O")
    wedged = Session("CC(N)O").tool("wedge")
    atoms = wedged.state["atoms"]
    carbon = next(i for i, a in enumerate(atoms) if a[1] == "C" and len(editor._neighbours(wedged.state, a[0])) == 3)
    methyl = next(i for i, a in enumerate(atoms) if a[1] == "C" and i != carbon)
    wedged.click_bond(carbon, methyl)
    first = np.asarray(Image.open(BytesIO(editor.draw_structure(plain.state, UI, "dark"))))
    second = np.asarray(Image.open(BytesIO(editor.draw_structure(wedged.state, UI, "dark"))))
    assert "@" in wedged.smiles and (first != second).any()


def test_loading_keeps_stereochemistry():
    for smiles in ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O", "C/C=C/C", "C/C=C\\C", "c1ccccc1", "[NH4+].[Cl-]",
                   "CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1"]:
        assert Session(smiles).smiles == canonical(smiles)


def test_clean_up_keeps_the_molecule_including_its_stereo():
    for smiles in ["C[C@H](N)C(=O)O", "C/C=C/C", "CC(=O)Oc1ccccc1C(=O)O"]:
        assert Session(smiles).keys("\x0b").smiles == canonical(smiles)


def test_clean_up_refuses_an_invalid_drawing_and_can_be_undone():
    assert "valid" in overvalent().keys("\x0b").output
    assert Session("CC").keys("\x0b", "\x1a").smiles == "CC"


def test_an_invalid_drawing_is_still_drawn_with_a_red_box():
    session = overvalent()
    smiles, problem, wrong = editor.analyse(session.state)
    assert smiles is None and "valence" in problem.lower() and wrong == [0]
    assert "red boxes" in session.output
    marks = editor._marks(session.state, UI, wrong)
    assert any(mark[0] == "box" and mark[2] == list(editor.RED) for mark in json.loads(json.dumps(marks)))
    image = np.asarray(Image.open(BytesIO(editor.draw_overlay(session.state, UI, marks)))).astype(int)
    assert ((image[:, :, 0] > 180) & (image[:, :, 2] < 170) & (image[:, :, 3] > 60)).any()


def test_details_the_editor_cant_keep_are_mentioned():
    state = editor.new_state()
    assert editor.load_smiles(state, "[CH3:1]C") and "aren't kept" in state["message"]


def test_status_shows_formula_and_weight():
    output = Session("CCO").output
    assert "C2H6O" in output and "MW 46.1" in output


def test_light_theme_changes_the_ink():
    def ink(theme):
        _, _, output = editor.step("", "CCCC", "", COLS, ROWS, CW, CH, theme=theme)
        pixels = np.asarray(layer(output, "structure")).astype(int)
        return pixels[pixels[:, :, 3] == 255][:, :3].sum(axis=1).mean()
    assert ink("dark") > 400 > ink("light")


def test_a_huge_window_is_drawn_smaller():
    _, _, output = editor.step("", "CCCC", "", 400, 200, 40, 80)
    image = layer(output, "structure")
    assert image.width * image.height <= editor.MAX_PIXELS * 1.01


def test_a_small_window_says_so():
    status, text, output = editor.step("", "", "", 50, 8)
    assert status == editor.CONTINUE and "too small" in output and editor.decode(text)


def test_resizing_clears_the_screen_and_sends_every_image_again():
    session = Session().click(60, 20)
    _, _, output = editor.step(session.text, "", "", COLS + 10, ROWS, CW, CH)
    assert "\x1b[2J" in output and layer(output, "palette") is not None and layer(output, "structure") is not None


def test_ctrl_l_redraws():
    session = Session().click(60, 20)
    assert session.send("").output == ""
    output = session.send("\x0c").output
    assert "\x1b[2J" in output and layer(output, "palette") is not None


def test_too_many_atoms_is_a_message_not_an_error():
    session = Session("CC").hover_atom(1)
    for _ in range(editor.MAX_ATOMS + 3):
        session.send("1")
    assert len(session.state["atoms"]) == editor.MAX_ATOMS and "at most" in session.state["message"]
    session.send("a")
    assert len(session.state["atoms"]) == editor.MAX_ATOMS


# input


def test_input_split_across_reads_is_put_back_together():
    session = Session()
    session.send("\x1b[<0;60")
    assert session.state["partial"] and session.state["atoms"] == []
    session.send(";20M\x1b[<0;60;20m")
    assert session.state["partial"] == "" and session.smiles == "CC"


def test_a_lone_esc_is_the_esc_key():
    session = Session().click(60, 20)
    assert session.send("\x1b").state["focus"] is None


def test_unknown_sequences_are_ignored():
    session = Session("CC")
    session.send("\x1b[15~\x1b[1;5P\x1bOQ\x02\xff")
    assert session.smiles == "CC" and session.status == editor.CONTINUE


@pytest.mark.parametrize("key", ["\x1a", "\x7f"])
def test_the_atom_going_in_the_middle_of_a_drag_ends_the_drag(key):
    session = Session().click(60, 20).click(90, 20)
    col, row = session.cell(*session.atom(3)[2:4])
    session.send(f"\x1b[<35;{col};{row}M\x1b[<0;{col};{row}M\x1b[<32;{col + 8};{row}M")
    target = session.state["press"]["target"]
    assert target == ["a", session.atom(3)[0]]
    state = session.state
    state["hover"] = target  # the pointer was on the atom when the key came
    session.text = editor.encode(state)
    session.send(key).send(f"\x1b[<32;{col + 9};{row}M").send(f"\x1b[<0;{col + 9};{row}m")
    assert session.status == editor.CONTINUE and session.state["press"] is None


def test_a_state_pointing_at_missing_atoms_does_no_harm():
    state = Session("CC").state
    state.update(focus=["a", 999], hover=["a", 999], last=["b", 999, 998], selected=[999], menu_target=["a", 999])
    state["press"] = {"cell": [50, 20], "view": [0.0, 0.0, 6.0], "model": [0.0, 0.0], "last": [0.0, 0.0], "target": ["a", 999], "moved": True,
                      "mode": "draw", "ids": [999]}
    state["drag"] = [1.0, 1.0]
    status, _, _ = editor.step(editor.encode(state), "", "n+x\x1b[<32;50;20M", COLS, ROWS, CW, CH)
    assert status == editor.CONTINUE


def test_several_events_in_one_read_all_count():
    assert Session("CC").hover_atom(1).send("1111").smiles == "CCCCCC"


def test_state_stays_small_for_a_large_molecule():
    assert len(Session("CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1").hover_atom(0).keys(*["1"] * 40).text) < editor.MAX_STATE


def test_random_input_never_breaks_it():
    rng = random.Random(20261101)
    keys = list("abcdefghijklmnopqrstuvwxyzABCEFHJKLNPSTUXZ0123456789+-=./?'") + ["\r", " ", "\t", "\x1b", "\x7f", "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1b[1;2A",
                                                         "\x1b[Z", "\x1b[3~", "\x1b[21~", "\x1b[15~", "\x1b[17~", "\x1b[18~", "\x1b[19~", "[", "]", "\x0c", "\x01", "\x1a", "\x19", "\x0b"]
    for _ in range(6):
        session = Session(rng.choice(["", "CCO", "c1ccccc1C(=O)O", "C[C@H](N)C(=O)O"]))
        for _ in range(200):
            if rng.random() < 0.4:
                session.send(rng.choice(keys))
            else:
                col, row = rng.randint(1, COLS), rng.randint(1, ROWS)
                code = rng.choice([0, 0, 0, 1, 2, 4, 8, 32, 33, 35, 64, 65])
                session.send(f"\x1b[<{code};{col};{row}{rng.choice('MMm')}")
            editor.decode(session.text)
            if session.status != editor.CONTINUE:
                break


# what the client can't be trusted with


def pack(state):
    return base64.urlsafe_b64encode(zlib.compress(json.dumps(state).encode())).decode()


def good():
    return Session("CCO").keys("\t", "\x7f", "\x1a").state


@pytest.mark.parametrize("change", [
    lambda s: s.update(atoms="x"),
    lambda s: s["atoms"].append([99, "Xx", 0, 0, 0]),
    lambda s: s["atoms"].append([99, "C"]),
    lambda s: s["atoms"].append([s["atoms"][0][0], "C", 0, 0, 0]),
    lambda s: s["atoms"].append([s["next"], "C\n 1 2", 0, 0, 0]),
    lambda s: s["atoms"][0].__setitem__(2, 1e300),
    lambda s: s["atoms"][0].__setitem__(4, 99),
    lambda s: s["bonds"].append([1, 999, 1]),
    lambda s: s["bonds"].append([1, 1, 1]),
    lambda s: s["bonds"][0].__setitem__(2, 99),
    lambda s: s["bonds"].append(list(s["bonds"][0])),
    lambda s: s.update(atoms=[[i + 1, "C", 0, 0, 0] for i in range(editor.MAX_ATOMS + 1)], bonds=[], next=editor.MAX_ATOMS + 2),
    lambda s: s.update(tool="shell"),
    lambda s: s.update(menu=["bar", 99]),
    lambda s: s.update(menu=["context", "shell", 1, 1]),
    lambda s: s.update(menu=["context", "atom", 1e9, 1]),
    lambda s: s.update(selected="x"),
    lambda s: s.update(selected=[True, "x"]),
    lambda s: s.update(label={"target": None, "at": [0, 0], "text": "x" * 99}),
    lambda s: s.update(label={"target": ["z", 1], "at": [0, 0], "text": ""}),
    lambda s: s.update(pointer=[1e300, 0]),
    lambda s: s.update(flips={"palette": 7}),
    lambda s: s.update(shown={"elsewhere": "x"}),
    lambda s: s.update(bond=True),
    lambda s: s.update(ring=99),
    lambda s: s.update(view=[0, 0, 1e9]),
    lambda s: s.update(view=[0, 0]),
    lambda s: s.update(focus=["z", 1]),
    lambda s: s.update(press={"cell": [1], "view": [0, 0, 4], "model": [0, 0], "last": [0, 0], "target": None, "moved": False, "mode": "draw", "ids": []}),
    lambda s: s.update(press={"cell": [1, 1], "view": [0, 0, 4], "model": [0, 0], "last": [0, 0], "target": None, "moved": False, "mode": "shell", "ids": []}),
    lambda s: s.update(entry=5),
    lambda s: s.update(entry="x" * (editor.MAX_ENTRY + 1)),
    lambda s: s.update(undo=[[[], [], 1]] * (editor.MAX_UNDO + 1)),
    lambda s: s.update(undo=[[[[1, "Xx", 0, 0, 0]], [], 2]]),
    lambda s: s.pop("view"),
    lambda s: s.update(partial="\x1b" * 100),
])
def test_a_tampered_state_is_refused(change):
    state = good()
    change(state)
    with pytest.raises(editor.StateError):
        editor.decode(pack(state))


@pytest.mark.parametrize("text", ["!!!", "AAAA", base64.urlsafe_b64encode(b"not zlib").decode(), pack([1, 2]), pack("x"), pack(None), "x" * (editor.MAX_STATE + 1)])
def test_a_state_that_is_not_one_is_refused(text):
    with pytest.raises(editor.StateError):
        editor.decode(text)


def test_a_state_that_unpacks_hugely_is_refused():
    bomb = base64.urlsafe_b64encode(zlib.compress(b" " * 20_000_000)).decode()
    assert len(bomb) < editor.MAX_STATE
    with pytest.raises(editor.StateError):
        editor.decode(bomb)


# the endpoint


@pytest.fixture
def client():
    return app.test_client()


def form(**extra):
    return {"cols": str(COLS), "rows": str(ROWS), **extra}


def split(response):
    head, output = response.get_data(as_text=True).split("\n", 1)
    status, state = head.split(" ", 1)
    return int(status), state, output


def test_endpoint_runs_a_session(client):
    status, state, output = split(client.post("/api/v1/edit", data=form(smiles="CCO", cell_width="10", cell_height="20")))
    assert status == editor.CONTINUE and "a=T" in output
    status, state, output = split(client.post("/api/v1/edit", data=form(state=state, events="11")))  # Ctrl-Q
    assert (status, output) == (editor.QUIT, "CCO")


def test_endpoint_reports_a_bad_starting_smiles(client):
    status, _, output = split(client.post("/api/v1/edit", data=form(smiles="C(")))
    assert status == editor.FAILED and output


def test_endpoint_takes_any_bytes_as_events(client):
    response = client.post("/api/v1/edit", data=form(events=bytes(range(256)).hex()))
    assert response.status_code == 200


@pytest.mark.parametrize("change", [{"cols": "x"}, {"cols": "10"}, {"rows": "9999"}, {"cols": ""}, {"cell_width": "1"}, {"cell_height": "x"},
                                    {"theme": "blue"}, {"events": "zz"}, {"events": "abc"}, {"events": "00" * (editor.MAX_EVENTS + 1)},
                                    {"state": "nonsense"}])
def test_endpoint_refuses_bad_requests(client, change):
    response = client.post("/api/v1/edit", data=form(**change))
    assert response.status_code == 400 and response.get_data(as_text=True)


def test_endpoint_needs_the_size(client):
    assert client.post("/api/v1/edit", data={}).status_code == 400


def test_get_is_not_allowed(client):
    assert client.get("/api/v1/edit").status_code == 405


def test_a_compact_editor_has_a_strip_of_tools_over_the_drawing_and_no_menu_bar():
    box = (5, 0, 20, 100)  # row, col, rows, cols, counting the border
    ui = editor.Layout(120, 40, CW, CH, box=box, title="Edit", compact=True)
    assert (ui.top, ui.left, ui.palette_top, ui.palette_rows) == (2, 0, 0, 2) and ui.canvas_cols == 98
    assert [editor.PALETTE[i][0] for i in ui.tools] == list(editor.COMPACT_TOOLS)
    benzene = editor.COMPACT_TOOLS.index("benzene")
    assert ui.palette_at(benzene * ui.button_cols + 1, 1) == editor._ICON_NAMES.index("benzene")
    assert ui.title_at(2, 0) is None and editor.fits(ui)
    status, text, output = editor.step("", "CCO", "", 120, 40, CW, CH, box=box, title="Edit", compact=True)
    assert status == editor.CONTINUE and "File" not in output and "Done Ctrl-Q" in output
    # the strip's benzene, then a click in the drawing, then Done
    x, y = benzene * ui.button_cols + 2, 1 + 6 + 1  # 1-based cells: the box's border is row 5, the strip starts on row 6
    status, text, _ = editor.step(text, "", f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m\x1b[<0;20;20M\x1b[<0;20;20m", 120, 40, CW, CH, box=box, compact=True)
    status, _, smiles = editor.step(text, "", "\x11", 120, 40, CW, CH, box=box, compact=True)
    assert status == editor.QUIT and "c1ccccc1" in smiles and "CCO" in smiles.replace("OCC", "CCO")


def test_an_inline_editor_draws_over_the_picture_with_its_tools_floating_over_the_host_text():
    picture, place = (12, 5, 6, 20), (12, 3, 8, 44)  # the picture, and the drawing's place around it
    ui = editor.Layout(100, 40, CW, CH, inline=place)
    assert ui.inline and ui.hosted and (ui.top, ui.left, ui.canvas_rows, ui.canvas_cols) == place
    assert (ui.palette_top, ui.bar_row, ui.palette_left) == (9, 11, 3) and ui.strip_rows() == [9, 10, 11]  # above, the line next to it
    assert editor.fits(ui) and ui.palette_at(3 + 1, 9) == editor._ICON_NAMES.index("select")
    assert [name for name, _, _ in ui.buttons()] == ["tools", "cancel", "done", "smiles"] and ui.button_at(ui.buttons()[2][2] + 1, 11) == "done"
    status, text, output = editor.step("", "CCO", "", 100, 40, CW, CH, title="mol 1 · Ethanol", inline=place, at=picture)
    assert status == editor.CONTINUE and editor.HOST_REPAINT in output and "Done ^Q" in output and "mol 1 · Ethanol" in output
    assert "\x1b[13;6H" not in output.split(editor.HOST_REPAINT)[0]  # nothing of its own before the host has drawn the screen
    view = editor.decode(text)["view"]
    centre_col, centre_row = ui.to_cell(editor.decode(text), view[0], view[1])
    atoms = editor.decode(text)["atoms"]
    middle = ((min(a[2] for a in atoms) + max(a[2] for a in atoms)) / 2, (min(a[3] for a in atoms) + max(a[3] for a in atoms)) / 2)
    col, row = ui.to_cell(editor.decode(text), *middle)
    assert abs(col - (5 + 10)) < 0.01 and abs(row - (12 + 3)) < 0.01  # the molecule's middle is the picture's
    assert editor.decode(text)["view"][2] <= editor.graphics.BOND_CELLS  # and no larger than molterm drew it
    # F2 puts the tools away: the host is told, and the line alone stays, next to the drawing
    status, text, output = editor.step(text, "", "\x1bOQ", 100, 40, CW, CH, inline=place, at=picture)
    assert editor.HOST_TOOLS.format(0) in output and editor.HOST_REPAINT in output and "▴ tools F2" in output
    assert editor.decode(text)["strip"] is False
    hidden = editor.Layout(100, 40, CW, CH, inline=place, strip=False)
    assert hidden.strip_rows() == [11] and hidden.palette_rows == 0
    # a right click's menu may cover the host's text: closing it asks the host to draw it again
    status, text, output = editor.step(text, "", "\x1b[<2;20;15M\x1b[<2;20;15m", 100, 40, CW, CH, inline=place, at=picture)
    assert "Highlight" in output or "Undo" in output
    status, text, output = editor.step(text, "", "\x1b", 100, 40, CW, CH, inline=place, at=picture)
    assert editor.HOST_REPAINT in output
    status, _, smiles = editor.step(text, "", "\x11", 100, 40, CW, CH, inline=place, at=picture)
    assert status == editor.QUIT and smiles.strip() in ("CCO", "OCC")
    # beginning with the tools away, and a picture at the top of the screen: the strip goes below the drawing
    status, text, output = editor.step("", "CCO", "", 100, 40, CW, CH, inline=(1, 3, 8, 44), strip=False)
    assert status == editor.CONTINUE and editor.Layout(100, 40, CW, CH, inline=(1, 3, 8, 44), strip=False).bar_row == 0
    low = editor.Layout(100, 40, CW, CH, inline=(1, 3, 8, 44))
    assert (low.bar_row, low.palette_top) == (9, 10)


def test_callouts_draw_highlights_and_leave_the_notes_to_the_host():
    import callouts
    result = callouts.picture("CC(=O)Nc1ccc(O)cc1", 60, 16, mark={"smarts": "C(=O)N", "highlight": "yellow", "note": "amide"})
    assert result["marked"] == [1, 2, 3] and result["notes"][0]["atom"] == 1 and result["notes"][0]["text"] == "amide"
    assert "atomNote.amide" in result["smiles"] and "molterm_highlight.yellow" in result["smiles"]
    assert result["paint"] == {"smiles": "CC(=O)Nc1ccc(O)cc1", "marks": [[1, 7, "yellow"]]}  # C(=O)N, for the SMILES under the picture
    image = Image.open(BytesIO(base64.b64decode(result["png"])))
    assert image.size == (result["cols"] * editor.graphics.CELL_PIXELS, result["rows"] * 2 * editor.graphics.CELL_PIXELS)  # whole cells
    row = result["notes"][0]["row"]
    pixel = image.getpixel((image.width - 1, int((row + 0.5) * 2 * editor.graphics.CELL_PIXELS)))
    assert pixel[:3] == callouts.LEADER and pixel[3] == 255  # the leader reaches the right edge, in the middle of its row
    again = callouts.picture(result["smiles"] + "", 60, 16, mark={"atoms": [6], "note": "phenol"})
    assert sorted(n["text"] for n in again["notes"]) == ["amide", "phenol"]
    rows = sorted(n["row"] for n in again["notes"])
    assert rows[1] - rows[0] >= min(callouts.BUBBLE_ROWS, again["rows"] // 2)  # kept apart, for the bubbles
    assert callouts.picture(again["smiles"], 60, 16, clear=True)["notes"] == []
    assert result["notes"][0]["by"] == "agent" and result["notes"][0]["group"]  # an agent's, about all it highlighted


def test_a_highlighted_atom_keeps_its_elements_colour():
    # RDKit writes a highlighted atom's label in the default ink unless told otherwise, and on a dark theme that's pale and lost in
    # the highlight: a highlighted piperazine had no Ns
    import callouts
    import graphics
    nitrogen = tuple(round(c * 255) for c in graphics.palette("dark")[7])

    def blue(mark):
        png = base64.b64decode(callouts.picture("CN1CCN(C)CC1", 40, 16, "dark", mark=mark)["png"])
        pixels = np.asarray(Image.open(BytesIO(png)).convert("RGBA")).reshape(-1, 4).astype(int)
        return int((np.abs(pixels[:, :3] - nitrogen).max(axis=1) < 24).sum())

    plain = blue(None)
    assert plain > 0
    for colour in ("yellow", "pink"):
        assert blue({"smarts": "N1CCN(C)CC1", "highlight": colour}) > plain // 3


def test_callouts_edit_one_note_as_the_person_rewrote_it():
    import callouts
    noted = "CC(=O)Nc1ccc(O)cc1 |atomProp:1.atomNote.amide:1.molterm_flags.group:1.molterm_highlight.yellow:2.molterm_highlight.yellow|"
    result = callouts.picture(noted, 60, 16, mark={"edit": True, "atom": 1, "note": "the amide C", "by": "user"})
    note = result["notes"][0]
    assert note["text"] == "the amide C" and note["by"] == "user" and note["group"]  # still the group's, now the person's
    assert "molterm_highlight.yellow" in result["smiles"]
    bond = callouts.picture("CCO", 40, 10, mark={"edit": True, "bond": [1, 2], "note": "C-O"})
    assert bond["notes"][0]["bond"] == [1, 2] and bond["notes"][0]["by"] == "agent"
    gone = callouts.picture(result["smiles"], 60, 16, mark={"edit": True, "atom": 1, "note": ""})
    assert gone["notes"] == [] and "molterm_highlight.yellow" in gone["smiles"]  # the note off, its highlight kept
    for bad in ({"edit": True, "atom": 99}, {"edit": True, "bond": [0, 2]}, {"edit": True, "bond": "x"}):
        with pytest.raises(callouts.CalloutError):
            callouts.picture("CCO", 40, 10, mark=bad)


def test_callouts_draw_the_persons_own_notes_in_their_colour():
    import callouts
    result = callouts.picture("CCO |atomProp:2.atomNote.mine:2.molterm_flags.user|", 40, 10)
    note = result["notes"][0]
    assert note["by"] == "user" and not note["group"]
    image = Image.open(BytesIO(base64.b64decode(result["png"])))
    pixel = image.getpixel((image.width - 1, int((note["row"] + 0.5) * 2 * editor.graphics.CELL_PIXELS)))
    assert pixel[:3] == callouts.USER_LEADER
    assert callouts._rows([5, 5, 5], 6) == [0, 3, 5] or callouts._rows([5, 5, 5], 6)[-1] <= 5
    for bad in ({"atoms": [99]}, {"smarts": "["}, {"atoms": [0], "highlight": "plaid"}):
        with pytest.raises(callouts.CalloutError):
            callouts.picture("CCO", 60, 16, mark=bad)


def test_the_endpoints_edit_inline_and_draw_callouts(client):
    status, state, output = split(client.post("/api/v1/edit", data=form(smiles="CCO", inline="12,3,8,44", at="12,5,6,20", strip="0")))
    assert status == editor.CONTINUE and editor.HOST_REPAINT in output and "▴ tools F2" in output
    assert client.post("/api/v1/edit", data=form(smiles="CCO", inline="12,3")).status_code == 400
    answer = client.post("/api/v1/callouts", json={"input": "CCO", "cols": 40, "rows": 10, "mark": {"atoms": [2], "note": "OH"},
                                                   "background": "161615", "foreground": "f0efec"})
    assert answer.status_code == 200 and answer.json["notes"][0]["text"] == "OH" and answer.json["marked"] == [2]
    assert client.post("/api/v1/callouts", json={"input": "CCO", "mark": {"smarts": "["}}).json["error"]["code"] == "bad_request"
    assert client.post("/api/v1/callouts", json={"input": "not a molecule"}).status_code == 400
    assert client.post("/api/v1/callouts", data="x").status_code == 400


def test_an_inline_drawing_made_larger_keeps_the_molecule_where_it_was():
    place, picture = (12, 5, 8, 44), (12, 5, 6, 20)
    status, text, _ = editor.step("", "CCO", "", 100, 40, CW, CH, inline=place, at=picture)
    s = editor.decode(text)
    before = [editor.Layout(100, 40, CW, CH, inline=place).to_cell(s, a[2], a[3]) for a in s["atoms"]]
    larger = (12, 5, 14, 60)
    status, text, output = editor.step(text, "", "", 100, 40, CW, CH, inline=larger, at=picture)
    s = editor.decode(text)
    after = [editor.Layout(100, 40, CW, CH, inline=larger).to_cell(s, a[2], a[3]) for a in s["atoms"]]
    assert all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(before, after))
    assert editor.HOST_REPAINT in output and s["canvas"] == list(larger)


def test_an_inline_editor_tells_its_host_the_notes_for_bubbles_and_draws_their_leaders():
    import base64 as b64
    place = (12, 5, 10, 44)
    status, text, output = editor.step("", "CCO |atomProp:2.atomNote.the OH|", "", 100, 40, CW, CH, inline=place, at=(12, 5, 6, 20))
    told = json.loads(b64.b64decode(re.search(r"5523;notes=([A-Za-z0-9+/=]+)", output).group(1)))
    assert told["edge"] == 5 + 44 and [n["text"] for n in told["notes"]] == ["the OH"] and 12 <= told["notes"][0]["row"] < 22
    assert told["notes"][0]["by"] == "agent"
    assert output.index("5523;notes") < output.index(editor.HOST_REPAINT)  # told before the host draws
    # an unchanged drawing says nothing more; a note taken away is told, with a repaint for the host's bubbles
    status, text, output = editor.step(text, "", "", 100, 40, CW, CH, inline=place)
    assert "5523;notes" not in output
    s = editor.decode(text)
    s["notes"] = editor.annotate.empty()
    status, text, output = editor.step(editor.encode(s), "", "", 100, 40, CW, CH, inline=place)
    told = json.loads(b64.b64decode(re.search(r"5523;notes=([A-Za-z0-9+/=]+)", output).group(1)))
    assert told["notes"] == [] and editor.HOST_REPAINT in output


def test_a_note_is_written_in_its_bubble_inline_with_shift_enter_for_a_new_line():
    import base64 as b64
    place = (12, 5, 10, 44)
    status, text, output = editor.step("", "CCO", "", 100, 40, CW, CH, inline=place, at=(12, 5, 6, 20))
    s = editor.decode(text)
    s["prompt"] = {"targets": [["a", 2]], "text": "", "kind": "note"}  # Note... on the oxygen
    status, text, output = editor.step(editor.encode(s), "", "the OH\x1b[13;2uH-bond", 100, 40, CW, CH, inline=place)
    told = json.loads(b64.b64decode(re.findall(r"5523;notes=([A-Za-z0-9+/=]+)", output)[-1]))
    assert told["notes"] == [{"text": "the OH\nH-bond", "row": told["notes"][0]["row"], "by": "user", "draft": True}]
    assert "Shift+Enter a new line" in output and editor.HOST_REPAINT in output  # the bar says where it is typed, and the host draws it
    status, text, output = editor.step(text, "", "\x1b[27;2;13~ donor\r", 100, 40, CW, CH, inline=place)  # xterm's shift+enter too
    s = editor.decode(text)
    assert s["prompt"] is None and s["notes"]["atoms"] == [[2, "", "the OH\nH-bond\n donor"]]
    told = json.loads(b64.b64decode(re.findall(r"5523;notes=([A-Za-z0-9+/=]+)", output)[-1]))
    assert "draft" not in told["notes"][0] and told["notes"][0]["text"] == "the OH\nH-bond\n donor"
    assert editor.parse_events("\x1b[13;2u")[0] == [("key", "shift-enter")]


def _smiles_told(output):
    return [json.loads(base64.b64decode(found)) for found in re.findall(r"5523;smiles=([A-Za-z0-9+/=_-]+)", output)]


def test_the_smiles_is_painted_where_it_is_highlighted_and_its_checkbox_hides_it():
    yellow = ";".join(str(round(c * 255)) for c in editor.annotate.HIGHLIGHTS["yellow"])
    session = Session("CCO |atomProp:2.molterm_highlight.yellow|")
    assert f"CC\x1b[48;2;{yellow};38;2;0;0;0mO\x1b[0m" in session.output  # the status line's SMILES, its oxygen painted
    s = session.state
    editor._act(s, UI, ("smiles", None), None)  # View > Show SMILES, ticked until now
    assert not s["smiles_shown"] and editor._item_label(s, ("Show SMILES", "", ("smiles", None))) == "  Show SMILES"
    session.text = editor.encode(s)
    session.send("\x1b[A")  # a frame: the facts instead of the SMILES
    assert "C2H6O" in session.output and f"{yellow};38;2;0;0;0mO" not in session.output
    status, text, output = editor.step("", "CCO", "", COLS, ROWS, CW, CH, show_smiles=False)
    assert not editor.decode(text)["smiles_shown"]


def test_an_inline_editor_tells_its_host_the_smiles_and_its_checkbox_hides_it():
    place = (12, 5, 10, 44)
    status, text, output = editor.step("", "CCO |atomProp:2.molterm_highlight.green|", "", 100, 40, CW, CH, inline=place, at=(12, 5, 6, 20))
    assert _smiles_told(output)[-1] == {"shown": True, "smiles": "CCO", "marks": [[2, 3, "green"]]}
    assert "☑ SMILES" in output  # the checkbox on its line, ticked
    ui = editor.Layout(100, 40, CW, CH, inline=place)
    col = next(start for name, _, start in ui.buttons() if name == "smiles")
    status, text, output = editor.step(text, "", f"\x1b[<0;{col + 2};{ui.bar_row + 1}M\x1b[<0;{col + 2};{ui.bar_row + 1}m", 100, 40, CW, CH,
                                       inline=place)
    assert not editor.decode(text)["smiles_shown"] and _smiles_told(output)[-1]["shown"] is False and "☐ SMILES" in output
    assert editor.HOST_REPAINT in output  # the host draws its screen again, without the SMILES under the drawing


def test_a_box_editor_tells_its_host_when_the_smiles_is_hidden():
    box = (2, 0, 30, 100)
    status, text, output = editor.step("", "CCO", "", COLS, ROWS, CW, CH, box=box, compact=True)
    ui = editor.Layout(COLS, ROWS, CW, CH, box=box, compact=True)
    col = next(start for name, _, start in ui.buttons() if name == "smiles")
    row, col = ui.origin[0] + ui.rows + 1, ui.origin[1] + col + 2
    status, text, output = editor.step(text, "", f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m", COLS, ROWS, CW, CH, box=box, compact=True)
    assert _smiles_told(output) == [{"shown": False}] and "☐ SMILES" in output
