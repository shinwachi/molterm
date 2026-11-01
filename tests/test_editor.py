"""The molecular editor, driven through its one entry point with the raw bytes a terminal would send."""
import base64
import json
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
PLACE = re.compile("\x1b_G(a=T[^;]*);")
CHUNK = re.compile("\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\\\")


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
        vx, vy, zoom = self.state["view"]
        return (round((x - vx) * zoom + UI.canvas_cols / 2 + 0.5),
                round((vy - y) * zoom * CW / CH + UI.canvas_rows / 2 + UI.top + 0.5))

    def click(self, col, row, button=0):
        return self.send(f"\x1b[<{button};{col};{row}M\x1b[<{button};{col};{row}m")

    def click_at(self, x, y, button=0):
        return self.click(*self.cell(x, y), button)

    def atom(self, index):
        return self.state["atoms"][index]

    def focus_on(self, index):
        ident = self.atom(index)[0]
        self.send("\x1b")
        while self.state["focus"] != ["a", ident]:
            self.send("\t")
        return self

    def click_atom(self, index, button=0):
        return self.click_at(self.atom(index)[2], self.atom(index)[3], button)

    def click_bond(self, first, second, button=0):
        a, b = self.atom(first), self.atom(second)
        return self.click_at((a[2] + b[2]) / 2, (a[3] + b[3]) / 2, button)

    def drag(self, start, end):
        self.send(f"\x1b[<0;{start[0]};{start[1]}M")
        self.send(f"\x1b[<32;{end[0]};{end[1]}M")
        return self.send(f"\x1b[<0;{end[0]};{end[1]}m")

    def button(self, label):
        row, col, width, _, _ = next(b for b in UI.buttons if b[3] == label)
        return self.click(col + 2, row + 1)


def overvalent():
    """An oxygen with three bonds, left that way while drawing."""
    session = Session().keys("o").button("-")
    for _ in range(3):
        session.click_atom(0)
    return session


def picture(output):
    data = "".join(m[2] for m in CHUNK.finditer(output))
    return Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")


def canonical(smiles):
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


# with the keyboard


def test_starts_empty_and_says_how_to_begin():
    session = Session()
    assert session.state["atoms"] == [] and "Click anywhere" in session.output


def test_enter_builds_a_chain():
    assert Session().keys("\r", "\r", "\r", "\r").smiles == "CCCC"


def test_a_zigzag_keeps_atoms_apart():
    atoms = Session().keys(*["\r"] * 8).state["atoms"]
    assert min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i]) > 0.9


def test_keys_build_acetic_acid():
    session = Session().keys("\r", "\r", "\r", "o", "=")
    assert session.smiles == "CC=O"
    session.focus_on(1).send("\r")  # the atom tool is still on oxygen, so the next atom is one
    assert session.smiles == "CC(=O)O"


def test_a_bond_key_changes_only_the_bond_just_drawn():
    session = Session().keys("\r", "\r", "=", "\r")
    assert session.smiles == "C=CC"


def test_a_bond_key_with_nothing_to_change_sets_the_tool():
    session = Session().keys("=", "\r", "\r")
    assert session.smiles == "C=C"


def test_triple_bonds_are_straight():
    session = Session().keys("#", "\r", "\r", "\r")
    (ax, ay), (bx, by), (cx, cy) = [(a[2], a[3]) for a in session.state["atoms"][:3]]
    assert abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) < 1e-6


def test_element_keys_set_the_chosen_atom():
    for key, symbol in [("n", "N"), ("o", "O"), ("s", "S"), ("p", "P"), ("f", "F"), ("l", "Cl"), ("b", "Br"), ("i", "I")]:
        assert Session().keys("\r", key).atom(0)[1] == symbol


def test_the_first_key_can_place_an_element():
    assert Session().keys("n").smiles == "N"


def test_digit_keys_add_rings():
    for key, size in [("3", 3), ("4", 4), ("5", 5), ("6", 6), ("7", 7)]:
        mol = Chem.MolFromSmiles(Session().keys(key).smiles)
        assert [len(r) for r in mol.GetRingInfo().AtomRings()] == [size]


def test_a_makes_benzene():
    assert Session().keys("a").smiles == "c1ccccc1"


def test_a_ring_attaches_to_a_chosen_atom_that_has_neighbours():
    assert Session().keys("\r", "\r", "a").smiles == canonical("CCc1ccccc1")


def test_a_ring_includes_a_lone_chosen_atom():
    assert Session().keys("\r", "6").smiles == "C1CCCCC1"


def test_arrows_move_between_atoms():
    session = Session().keys("\r", "\r")
    start = session.state["focus"]
    session.send("\x1b[D")
    assert session.state["focus"] != start


def test_tab_visits_atoms_then_bonds_and_wraps():
    session = Session().keys("\r", "\r", "\x1b")
    seen = [json.dumps(session.send("\t").state["focus"]) for _ in range(3)]
    assert len(set(seen)) == 3 and json.dumps(session.send("\t").state["focus"]) == seen[0]
    assert json.dumps(session.send("\x1b[Z").state["focus"]) == seen[-1]


def test_delete_keys_remove_the_chosen_atom_and_its_bonds():
    for key in ("x", "\x7f", "\x1b[3~"):
        session = Session().keys("\r", "\r", "\r", key)
        assert session.smiles == "CC"


def test_delete_needs_a_choice():
    assert "Choose" in Session().keys("x").output


def test_charge_keys():
    assert Session().keys("n", "+").smiles == "[NH4+]"
    assert Session().keys("o", "~").smiles == "[OH-]"


def test_undo_and_redo_step_through_every_change():
    session = Session().keys("\r", "\r", "n")
    session.send("u")
    assert session.smiles == "CC"
    session.send("u")
    assert session.smiles == "C"
    session.keys("r", "r")
    assert session.smiles == "CN"
    assert "Nothing" in session.keys("r").output


def test_new_changes_clear_redo():
    session = Session().keys("\r", "\r", "u", "\r", "r")
    assert session.state["redo"] == []


def test_undo_is_limited_so_the_state_stays_small():
    session = Session()
    for _ in range(50):
        session.keys("\r", "x")
    assert len(session.state["undo"]) <= editor.MAX_UNDO


def test_esc_starts_a_new_piece():
    assert Session().keys("\r", "\r", "\x1b", "\r").smiles == "C.CC"


def test_typing_a_smiles():
    session = Session().keys("/").send("CC(=O)O").send("\r")
    assert session.smiles == "CC(=O)O" and session.state["entry"] is None


def test_a_bad_smiles_stays_in_the_entry_with_a_message():
    session = Session().keys("/").send("C(").send("\r")
    assert session.state["entry"] == "C(" and session.state["message"]


def test_esc_leaves_the_entry_without_changing_anything():
    session = Session().keys("\r", "/").send("CCC").send("\x1b")
    assert session.smiles == "C" and session.state["entry"] is None


def test_entry_takes_keys_as_text_not_commands():
    session = Session().keys("/").send("q").send("\x7f").send("n")
    assert session.status == editor.CONTINUE and session.state["entry"] == "n"


def test_help_shows_and_hides():
    session = Session().keys("?")
    assert session.state["help"] and "Drag from an atom" in session.output and "a=T" not in session.output
    assert not session.keys("x").state["help"]


def test_every_key_in_the_help_text_exists():
    for key in "c n o s p f i h l b - = # w d 3 4 5 6 7 a + ~ x m e u r t v / ? q".split():
        assert key in editor.KEYS_HELP
    for key in "cnospfih":
        assert key in editor._ELEMENT_KEYS


def test_leaving_help_clears_its_text():
    session = Session().keys("\r", "?")
    assert "\x1b[2J" in session.keys("?").output


def test_zoom_keys_stay_in_range():
    session = Session()
    for _ in range(40):
        session.send("]")
    assert session.state["view"][2] == editor.ZOOM_RANGE[1]
    for _ in range(40):
        session.send("[")
    assert session.state["view"][2] == editor.ZOOM_RANGE[0]


def test_a_long_chain_keeps_the_new_atom_on_the_canvas():
    session = Session().keys("]", "]", "]", "]")
    for _ in range(40):
        session.send("\r")
    atom = next(a for a in session.state["atoms"] if a[0] == session.state["focus"][1])
    col, row = session.cell(atom[2], atom[3])
    assert 1 <= col <= COLS and UI.top < row <= UI.top + UI.canvas_rows


def test_ctrl_c_and_q_finish_with_the_smiles():
    for key in ("q", "\x03"):
        status, state, output = editor.step(Session("CCO").text, "", key, COLS, ROWS, CW, CH)
        assert (status, output) == (editor.QUIT, "CCO")


def test_quitting_an_empty_drawing_prints_nothing():
    assert editor.step(Session().text, "", "q", COLS, ROWS, CW, CH)[::2] == (editor.QUIT, "")


def test_quitting_an_invalid_drawing_says_so():
    status, _, output = editor.step(overvalent().text, "", "q", COLS, ROWS, CW, CH)
    assert status == editor.FAILED and "valence" in output.lower()


# with the mouse


def test_click_in_empty_space_places_an_atom_where_clicked():
    session = Session().click(40, 20)
    atom = session.atom(0)
    assert session.cell(atom[2], atom[3]) == (40, 20)


def test_atom_tool_places_and_sets_elements():
    session = Session().button("N").click(40, 20)
    assert session.smiles == "N"
    session.button("O").click_atom(0)
    assert session.smiles == "O"


def test_bond_tool_click_on_an_atom_grows():
    assert Session().click(40, 20).click_atom(0).smiles == "CC"


def test_clicking_a_bond_steps_through_its_orders():
    session = Session().click(40, 20).click_atom(0)
    for expected in ("C=C", "C#C", "CC"):
        session.click_bond(0, 1)
        assert session.smiles == expected


def test_double_bond_tool_sets_a_clicked_bond():
    session = Session().click(40, 20).click_atom(0).button("=").click_bond(0, 1)
    assert session.smiles == "C=C"


def test_drag_from_an_atom_draws_a_bond_at_a_snapped_angle():
    session = Session().click(40, 20)
    start = session.cell(session.atom(0)[2], session.atom(0)[3])
    session.drag(start, (start[0] + 3, start[1] - 1))
    a, b = session.atom(0), session.atom(1)
    angle = np.degrees(np.arctan2(b[3] - a[3], b[2] - a[2]))
    assert session.smiles == "CC" and abs(angle / 30 - round(angle / 30)) < 1e-6
    assert abs(np.hypot(b[2] - a[2], b[3] - a[3]) - 1) < 1e-6


def test_drag_onto_another_atom_joins_them():
    session = Session().click(40, 20).click(60, 20)
    assert session.smiles == "C.C"
    session.drag(session.cell(*session.atom(0)[2:4]), session.cell(*session.atom(1)[2:4]))
    assert session.smiles == "CC" and len(session.state["atoms"]) == 2


def test_closing_a_ring_by_dragging():
    session = Session().keys(*["\r"] * 6)
    assert session.smiles == "CCCCCC"
    session.drag(session.cell(*session.atom(5)[2:4]), session.cell(*session.atom(0)[2:4]))
    assert session.smiles == "C1CCCCC1" and len(session.state["atoms"]) == 6


def test_drag_from_empty_space_draws_a_bond_with_both_atoms():
    assert Session().drag((40, 20), (46, 20)).smiles == "CC"


def test_atom_tool_drag_ends_in_its_element():
    session = Session().button("O").click(40, 20).button("N")
    session.drag(session.cell(*session.atom(0)[2:4]), (50, 20))
    assert session.smiles == "NO"


def test_drag_shows_a_preview_and_changes_nothing_until_release():
    session = Session().click(40, 20).send("\x1b[<35;100;30M")
    start = session.cell(*session.atom(0)[2:4])
    before = np.asarray(picture(session.output))
    session.send(f"\x1b[<0;{start[0]};{start[1]}M")
    session.send(f"\x1b[<32;{start[0] + 8};{start[1]}M")
    assert len(session.state["atoms"]) == 1
    assert (np.asarray(picture(session.output)) != before).any()


def test_right_click_deletes_in_any_tool():
    session = Session().click(40, 20).click_atom(0).click_atom(1, button=2)
    assert session.smiles == "C"
    session = Session().click(40, 20).click_atom(0).click_bond(0, 1, button=2)
    assert session.smiles == "C.C"


def test_erase_tool_deletes_atoms_and_bonds():
    session = Session("CCC").button("Erase").click_atom(2)
    assert session.smiles == "CC"
    session.click_bond(0, 1)
    assert session.smiles == "C.C"


def test_ring_tool_in_empty_space_is_centred_on_the_click():
    session = Session().button("Benzene").click(60, 20)
    xs, ys = [a[2] for a in session.state["atoms"]], [a[3] for a in session.state["atoms"]]
    assert session.smiles == "c1ccccc1"
    assert session.cell(sum(xs) / 6, sum(ys) / 6) == (60, 20)


@pytest.mark.parametrize("label, sides", [("3", 3), ("4", 4), ("5", 5), ("6", 6), ("7", 7)])
def test_rings_are_regular_with_bonds_of_one(label, sides):
    session = Session().button(label).click(60, 20)
    state = session.state
    by_id = {a[0]: a for a in state["atoms"]}
    lengths = [np.hypot(by_id[b[0]][2] - by_id[b[1]][2], by_id[b[0]][3] - by_id[b[1]][3]) for b in state["bonds"]]
    assert len(lengths) == sides and max(lengths) - min(lengths) < 1e-6 and abs(lengths[0] - 1) < 1e-6


def test_fusing_benzene_onto_a_bond_makes_naphthalene():
    session = Session().button("Benzene").click(60, 20)
    bond = session.state["bonds"][0]
    ids = [a[0] for a in session.state["atoms"]]
    session.click_bond(ids.index(bond[0]), ids.index(bond[1]))
    assert session.smiles == "c1ccc2ccccc2c1" and len(session.state["atoms"]) == 10


def test_a_fused_ring_goes_to_the_side_without_atoms():
    session = Session().button("6").click(60, 20)
    centre = np.mean([[a[2], a[3]] for a in session.state["atoms"]], axis=0)
    session.click_bond(0, 1)
    new = session.state["atoms"][6:]
    assert len(new) == 4
    a, b = session.atom(0), session.atom(1)
    middle = np.array([(a[2] + b[2]) / 2, (a[3] + b[3]) / 2])
    assert all(np.dot(np.array([n[2], n[3]]) - middle, middle - centre) > 0 for n in new)


def test_three_fused_rings_reuse_the_atoms_they_meet():
    # phenalene-like closure: a new ring whose corners land on existing atoms must not stack atoms on them
    session = Session().button("6").click(60, 20)
    session.click_bond(0, 1)
    session.click_bond(1, 2)
    atoms = session.state["atoms"]
    closest = min(np.hypot(a[2] - b[2], a[3] - b[3]) for i, a in enumerate(atoms) for b in atoms[:i])
    assert closest > 0.5


def test_ring_tool_on_an_atom_attaches_it():
    session = Session().click(40, 20).click_atom(0).button("Benzene").click_atom(1)
    assert session.smiles == canonical("CCc1ccccc1")


def test_move_tool_moves_an_atom_and_undo_puts_it_back():
    session = Session("CC").button("Move")
    before = session.atom(0)[2:4]
    start = session.cell(*before)
    session.drag(start, (start[0], start[1] + 5))
    assert session.atom(0)[2:4] != before and session.smiles == "CC"
    session.send("u")
    assert session.atom(0)[2:4] == before


def test_move_tool_pans_in_empty_space():
    session = Session("CC").button("Move")
    x, y = session.state["view"][:2]
    session.drag((100, 30), (90, 30))
    assert session.state["view"][0] == pytest.approx(x + 10 / session.state["view"][2]) and session.state["view"][1] == y


def test_wheel_zooms_about_the_pointer():
    session = Session().click(40, 20)
    before = session.cell(*session.atom(0)[2:4])
    zoom = session.state["view"][2]
    session.send("\x1b[<64;40;20M")
    assert session.state["view"][2] > zoom and session.cell(*session.atom(0)[2:4]) == before
    session.send("\x1b[<65;40;20M\x1b[<65;40;20M")
    assert session.state["view"][2] < zoom


def test_hover_redraws_only_when_the_target_changes():
    session = Session().click(40, 20).send("\x1b[<35;100;30M")
    assert session.state["hover"] is None
    assert session.send("\x1b[<35;101;30M").output == ""
    col, row = session.cell(*session.atom(0)[2:4])
    assert PLACE.search(session.send(f"\x1b[<35;{col};{row}M").output) and session.state["hover"]
    assert session.send(f"\x1b[<35;{col};{row}M").output == ""


def test_moving_the_pointer_keeps_a_message():
    session = Session().keys("x")
    assert session.state["message"]
    assert session.send("\x1b[<35;100;30M").state["message"]


def test_pixel_reports_land_on_the_same_atom():
    session = Session().click(40, 20)
    col, row = session.cell(*session.atom(0)[2:4])
    x, y = (col - 1) * CW + CW // 2, (row - 1) * CH + CH // 2
    _, text, _ = editor.step(session.text, "", f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m", COLS, ROWS, CW, CH, pixels=True)
    assert editor.analyse(editor.decode(text))[0] == "CC"


def test_every_button_can_be_clicked():
    for button in UI.buttons:
        session = Session("CCO").keys("\t")
        session.click(button[1] + 2, button[0] + 1)
        assert session.status in (editor.CONTINUE, editor.QUIT)


def test_buttons_show_the_active_tool():
    assert "\x1b[7m N " in Session().button("N").output
    assert "\x1b[7m = " in Session().button("=").output
    assert "\x1b[7m Benzene " in Session().button("Benzene").output


@pytest.mark.parametrize("cols", [50, 60, 80, 120, 200])
def test_toolbar_wraps_and_stays_on_the_screen(cols):
    ui = editor.Layout(cols, 40)
    assert all(start + width <= cols for _, start, width, _, _ in ui.buttons)
    spots = [(row, col) for row, start, width, _, _ in ui.buttons for col in range(start, start + width)]
    assert len(spots) == len(set(spots)) and ui.canvas_rows > 4


# the picture


def test_atoms_are_drawn_where_clicks_find_them():
    session = Session().button("O").click(40, 20)
    image = np.asarray(picture(session.output)).astype(int)
    assert image.shape[:2] == (UI.canvas_rows * CH, COLS * CW)
    red = (image[:, :, 0] > 200) & (image[:, :, 1] < 170) & (image[:, :, 3] > 200)
    ys, xs = np.nonzero(red)
    col, row = xs.mean() / CW + 1, ys.mean() / CH + UI.top + 1
    assert abs(col - 40) < 2 and abs(row - 20) < 1.5


def test_mapping_holds_at_other_cell_sizes_and_zooms():
    for cell in [(8, 17), (12, 25), (20, 40)]:
        ui = editor.Layout(100, 40, *cell)
        s = editor.new_state()
        x, y = ui.to_model(s, 30.5, 18.5)
        status, text, output = editor.step("", "", f"\x1b[<0;31;19M\x1b[<0;31;19m", 100, 40, *cell)
        assert editor.decode(text)["atoms"][0][2:4] == pytest.approx([x, y])


def test_placement_fills_the_canvas_and_keeps_the_cursor():
    keys = PLACE.search(Session().click(40, 20).output)[1]
    assert f"c={COLS}," in keys and f"r={UI.canvas_rows}," in keys and "C=1," in keys and "i=" in keys


def test_each_frame_places_a_new_image_then_deletes_the_old():
    first = Session().click(40, 20)
    second_output = first.click_atom(0).output
    placed = int(re.search(r"a=T[^;]*i=(\d+)", second_output)[1])
    deleted = int(re.search(r"a=d,d=I,i=(\d+)", second_output)[1])
    assert placed != deleted and second_output.index("a=T") < second_output.index("a=d")


def test_frames_are_synchronised():
    output = Session().click(40, 20).output
    assert output.startswith("\x1b[?2026h") and output.endswith("\x1b[?2026l")


def test_a_wedge_is_drawn_and_sets_chirality():
    session = Session("CC(N)O")
    assert "@" not in session.smiles
    atoms = session.state["atoms"]
    carbon = next(i for i, a in enumerate(atoms) if a[1] == "C" and len(editor._neighbours(session.state, a[0])) == 3)
    methyl = next(i for i, a in enumerate(atoms) if a[1] == "C" and i != carbon)

    def drawing(state):
        state["focus"] = state["hover"] = None
        return np.asarray(Image.open(BytesIO(editor.draw(state, UI, "dark", []))))

    plain = drawing(session.state)
    wedged = Session("CC(N)O").button("Wedge").click_bond(carbon, methyl)
    hashed = Session("CC(N)O").button("Hash").click_bond(carbon, methyl)
    assert "@" in wedged.smiles and "@" in hashed.smiles and wedged.smiles != hashed.smiles
    assert (drawing(wedged.state) != plain).any() and (drawing(hashed.state) != drawing(wedged.state)).any()


def test_loading_keeps_stereochemistry():
    for smiles in ["C[C@H](N)C(=O)O", "C[C@@H](N)C(=O)O", "C/C=C/C", "C/C=C\\C", "c1ccccc1", "[NH4+].[Cl-]",
                   "CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1"]:
        assert Session(smiles).smiles == canonical(smiles)


def test_tidy_keeps_the_molecule_including_its_stereo():
    for smiles in ["C[C@H](N)C(=O)O", "C/C=C/C", "CC(=O)Oc1ccccc1C(=O)O"]:
        assert Session(smiles).keys("t").smiles == canonical(smiles)


def test_tidy_refuses_an_invalid_drawing_and_can_be_undone():
    assert "valid" in overvalent().keys("t").output
    session = Session().keys("\r", "\r").keys("t", "u")
    assert session.smiles == "CC"


def test_an_invalid_drawing_is_still_drawn_and_named():
    session = overvalent()
    smiles, problem, wrong = editor.analyse(session.state)
    assert smiles is None and "valence" in problem.lower() and wrong == [0]
    assert "red" in session.output
    image = np.asarray(picture(session.output)).astype(int)
    assert ((image[:, :, 0] > 180) & (image[:, :, 2] < 170) & (image[:, :, 3] > 60)).any()


def test_details_the_editor_cant_keep_are_mentioned():
    state = editor.new_state()
    assert editor.load_smiles(state, "[13CH4]") and "aren't kept" in state["message"]


def test_status_shows_formula_and_weight():
    output = Session("CCO").send("\x1b[<35;100;30M").keys("\t").output
    assert "C2H6O" in output and "MW 46.1" in output


def test_light_theme_changes_the_ink():
    def ink(theme):
        _, _, output = editor.step("", "CCCC", "", COLS, ROWS, CW, CH, theme=theme)
        pixels = np.asarray(picture(output)).astype(int)
        return pixels[pixels[:, :, 3] == 255][:, :3].sum(axis=1).mean()
    assert ink("dark") > 400 > ink("light")


def test_a_huge_window_is_drawn_smaller():
    _, _, output = editor.step("", "CCCC", "", 400, 200, 40, 80)
    image = picture(output)
    assert image.width * image.height <= editor.MAX_PIXELS * 1.01
    assert PLACE.search(output)[1].startswith("a=T,f=100,c=400,r=")


def test_a_small_window_says_so():
    status, text, output = editor.step("", "", "", 50, 8)
    assert status == editor.CONTINUE and "too small" in output and editor.decode(text)


def test_resizing_clears_the_screen():
    session = Session().click(40, 20)
    _, _, output = editor.step(session.text, "", "", COLS + 10, ROWS, CW, CH)
    assert "\x1b[2J" in output


def test_ctrl_l_redraws():
    session = Session().click(40, 20)
    assert session.send("").output == ""
    assert "\x1b[2J" in session.send("\x0c").output


def test_too_many_atoms_is_a_message_not_an_error():
    session = Session()
    for _ in range(editor.MAX_ATOMS + 3):
        session.send("\r")
    assert len(session.state["atoms"]) == editor.MAX_ATOMS and "at most" in session.state["message"]
    session.send("a")
    assert len(session.state["atoms"]) == editor.MAX_ATOMS


# input


def test_input_split_across_reads_is_put_back_together():
    session = Session()
    session.send("\x1b[<0;40")
    assert session.state["partial"] and session.state["atoms"] == []
    session.send(";20M\x1b[<0;40;20m")
    assert session.state["partial"] == "" and session.smiles == "C"


def test_a_lone_esc_is_the_esc_key():
    session = Session().keys("\r")
    assert session.send("\x1b").state["focus"] is None


def test_unknown_sequences_are_ignored():
    session = Session("CC")
    session.send("\x1b[15~\x1b[1;5P\x1bOQ\x01\x02\xff")
    assert session.smiles == "CC" and session.status == editor.CONTINUE


def test_several_events_in_one_read_all_count():
    assert Session().send("\r\r\r\r").smiles == "CCCC"


def test_state_stays_small_for_a_large_molecule():
    assert len(Session("CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1").keys(*["\r"] * 40).text) < editor.MAX_STATE


def test_random_input_never_breaks_it():
    rng = random.Random(20261101)
    keys = list("cnospfihlb-=#wd34567a+~xmeurtv/?") + ["\r", " ", "\t", "\x1b", "\x7f", "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1b[1;2A",
                                                       "\x1b[Z", "\x1b[3~", "[", "]", "\x0c"]
    for _ in range(6):
        session = Session(rng.choice(["", "CCO", "c1ccccc1C(=O)O", "C[C@H](N)C(=O)O"]))
        for _ in range(150):
            if rng.random() < 0.5:
                session.send(rng.choice(keys))
            else:
                col, row = rng.randint(1, COLS), rng.randint(1, ROWS)
                code = rng.choice([0, 0, 2, 32, 35, 64, 65])
                session.send(f"\x1b[<{code};{col};{row}{rng.choice('Mm')}")
            editor.decode(session.text)
            if session.status != editor.CONTINUE:
                break


# what the client can't be trusted with


def pack(state):
    return base64.urlsafe_b64encode(zlib.compress(json.dumps(state).encode())).decode()


def good():
    return Session("CCO").keys("\t", "u").state


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
    lambda s: s["bonds"][0].__setitem__(2, 9),
    lambda s: s["bonds"].append(list(s["bonds"][0])),
    lambda s: s.update(atoms=[[i + 1, "C", 0, 0, 0] for i in range(editor.MAX_ATOMS + 1)], bonds=[], next=editor.MAX_ATOMS + 2),
    lambda s: s.update(tool="shell"),
    lambda s: s.update(element="Xx"),
    lambda s: s.update(bond=True),
    lambda s: s.update(ring=99),
    lambda s: s.update(view=[0, 0, 1e9]),
    lambda s: s.update(view=[0, 0]),
    lambda s: s.update(focus=["z", 1]),
    lambda s: s.update(press={"cell": [1], "view": [0, 0, 4], "model": [0, 0], "target": None, "moved": False}),
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
    status, state, output = split(client.post("/api/v1/edit", data=form(state=state, events="71")))  # q
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
