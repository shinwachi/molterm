"""Markush structures and the Ketcher features in the editor: the text they travel as, and drawing them with the mouse and keys."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
sys.path.insert(0, os.path.dirname(__file__))
import annotate  # noqa: E402
import board  # noqa: E402
import editor  # noqa: E402
import markush  # noqa: E402
import molecule  # noqa: E402
from app import app  # noqa: E402
from test_editor import UI, Session  # noqa: E402

MARKUSH = "*C1CCCCC1* |$_R1;;;;;;;_R2$,RG:_R1={CCC},{N},_R2={O},LOG={_R1:;;>0._R2:_R1;H;0,1}|"


# the text


def test_fields_split_only_at_the_top_level():
    assert markush.split("CCO") == ("CCO", [])
    assert markush.split("CC |$a,b;c$,atomProp:0.x.y,(0,0,;1,0,)|") == ("CC", ["$a,b;c$", "atomProp:0.x.y", "(0,0,;1,0,)"])
    with pytest.raises(markush.MarkushError):
        markush.split("CC |$;$")


def test_rgroup_definitions_and_logic_come_off_and_go_back_as_they_were():
    core, definitions, logic = markush.separate(MARKUSH)
    assert core == "*C1CCCCC1* |$_R1;;;;;;;_R2$|"
    assert definitions == {1: ["CCC", "N"], 2: ["O"]}
    assert logic == {1: {"then": 0, "rest_h": False, "occurrence": ">0"}, 2: {"then": 1, "rest_h": True, "occurrence": "0,1"}}
    assert markush.write_rgroups(definitions) == "RG:_R1={CCC},{N},_R2={O}"
    assert markush.write_logic(logic) == "LOG={_R1:;;>0._R2:_R1;H;0,1}"


def test_a_definition_that_bridges_marks_both_ends_and_has_no_radicals():
    mol, first, second = markush.fragment_mol("[CH2:1]C[CH2:2]")
    assert (first, second) == (0, 2) and all(a.GetNumRadicalElectrons() == 0 for a in mol.GetAtoms())
    assert markush.fragment_smiles(mol, first, second) == "[CH2:1]C[CH2:2]"


def test_the_renderers_read_a_markush_structure_and_a_query():
    parsed = molecule.parse(MARKUSH, "smiles")
    assert parsed.core.GetNumAtoms() == 8 and parsed.mol.GetNumAtoms() == 8 + 3 + 1 + 1  # the core and its three definitions
    assert parsed.rgroups == {1: ["CCC", "N"], 2: ["O"]}
    query = molecule.parse("[#6]-[#6,#7,#8]:[!#9&!#17]", "smiles")
    assert query.format == "smarts" and query.mol.GetNumAtoms() == 3
    client = app.test_client()
    for text in (MARKUSH, "[#6]-[#6,#7,#8]", "Cl*.c1ccccc1 |m:1:2.3.4|"):
        assert client.get("/api/v1/render.txt", query_string={"input": text, "mode": "braille"}).status_code == 200


def test_annotations_keep_the_rest_of_the_extension():
    smiles, notes = annotate.split("*CC |$_R1;;$,atomProp:2.atomNote.end,RG:_R1={N}|")
    assert smiles == "*CC |$_R1;;$,RG:_R1={N}|" and notes["atoms"] == [[2, "", "end"]]
    assert annotate.write(smiles, notes) == "*CC |$_R1;;$,atomProp:2.atomNote.end,RG:_R1={N}|"


# through the editor and back


@pytest.mark.parametrize("text, expected", [
    (MARKUSH, MARKUSH),
    ("*C1CCCCC1* |$_R1;;;;;;;_R2$,RG:_R1={[CH2:1]C[CH2:2]},_R2={O}|", None),
    ("C*c1ccccc1 |$;Q_e;;;;;;$|", None),
    ("CC |$;AH_p$|", "*C |$AH_p;$|"),
    ("Cl*.c1ccccc1 |m:1:2.3.4|", "*Cl.c1ccccc1 |$star_e;;;;;;;$,m:0:4.3.2|"),
    ("C[C@H](O)[C@@H](C)F |&1:1,o2:3|", "C[C@H](F)[C@H](C)O |o1:1,&1:3|"),
    ("*CC(*)C |$star_e;;;star_e;$,Sg:n:2,1::ht|", "*CC(*)C |$star_e;;;star_e;$,Sg:n:1,2:n::::|"),
    ("[13CH3]C", "C[13CH3]"),
    ("[CH2]C |^1:0|", None),
    ("CCC |LN:1:1.4|", None),
    ("[#6]-[#6,#7,#8]:[!#9&!#17]", None),
    ("CC(=O)* |$;;;_AP1$|", "*C(C)=O |$_AP1;;;$|"),
    ("*CC* |$_AP1;;;_AP2$|", None),
])
def test_markush_and_query_text_comes_back_from_the_editor(text, expected):
    s = editor.new_state()
    assert editor.load_smiles(s, text), s["message"]
    assert not s["message"]
    editor.check_state(json.loads(json.dumps(s)))
    assert editor.annotated(s) == (expected or text)


def test_a_loaded_definition_is_drawn_beside_the_core_with_its_attachment_point():
    s = editor.new_state()
    editor.load_smiles(s, MARKUSH)
    members = [a for a in s["atoms"] if "rg" in editor._extras(a)]
    assert len(members) == 5 and all(a[2] > 2 for a in members)  # to the right
    assert sum(editor._extras(a).get("ap") == 1 for a in members) == 3  # one each
    assert editor._fragments(s) == {1: [[9, 10, 11], [12]], 2: [[13]]}


# drawing them


def test_the_label_box_takes_markush_atoms_lists_and_isotopes():
    for text, smiles in [("R1", "*CC |$_R1;;$|"), ("Q", "*CC |$Q_e;;$|"), ("AH", "*CC |$AH_p;;$|"), ("13C", "CC[13CH3]"), ("D", "[2H]CC")]:
        session = Session("CCC").hover_atom(0).keys("\r").label(text)
        assert editor.annotated(session.state) == smiles, text
    session = Session("CCC").hover_atom(0).keys("\r").label("C,N,O")
    assert editor.annotated(session.state) == "[#6,#7,#8]-[#6]-[#6]"
    session = Session("CCC").hover_atom(0).keys("\r").label("!F,Cl")
    assert editor.annotated(session.state) == "[!#9&!#17]-[#6]-[#6]"
    session = Session("CCC").hover_atom(0).keys("\r").label("Xq,Q")
    assert "isn't an element" in session.state["message"]


def test_chemdraw_keys_for_markush_atoms():
    session = Session("CCC").hover_atom(0).keys("r")
    assert editor.annotated(session.state) == "*CC |$_R1;;$|"
    session.keys("r")
    assert editor.annotated(session.state) == "*CC |$_R2;;$|"
    assert editor.annotated(Session("CCC").hover_atom(0).keys("x").state) == "*CC |$X_p;;$|"
    assert editor.annotated(Session("CCC").hover_atom(0).keys("d").state) == "[2H]CC"
    session = Session("CCC").hover_atom(0).keys(".")
    assert editor._extras(session.atom(0)) == {"ap": 1} and editor.annotated(session.state) == "*CCC |$_AP1;;;$|"
    session.keys(".", ".", ".")
    assert "ap" not in editor._extras(session.atom(0))


def test_the_rgroup_label_tool_numbers_atoms():
    session = Session("CCCC").tool("rlabel")
    session.click_atom(0).click_atom(3)
    assert editor.annotated(session.state) == "*CC* |$_R1;;;_R2$|"


def test_atom_properties_from_the_submenu():
    session = Session("CCC").click_atom(0, button=2).pick("Properties and Markush ▸")
    assert session.state["menu"][1] == "atom_more"
    session.pick("Isotope...").label("13")
    assert editor.annotated(session.state) == "CC[13CH3]"
    session.click_atom(0, button=2).pick("Properties and Markush ▸").pick("Radical")
    assert editor.annotated(session.state) == "CC[13CH2] |^1:2|"
    session = Session("CCC").click_atom(1, button=2).pick("Properties and Markush ▸").pick("Link Node...").label("1-3")
    assert editor.annotated(session.state) == "CCC |LN:1:1.3|"
    session = Session("CCC").click_atom(0, button=2).pick("Properties and Markush ▸").pick("Link Node...").label("1-3")
    assert "two bonds" in session.state["message"] and editor.annotated(session.state) == "CCC"
    session = Session("CCC").click_atom(2, button=2).pick("Properties and Markush ▸").pick("Atom List...").label("N,O")
    assert editor.annotated(session.state) == "[#6]-[#6]-[#7,#8]"


def test_query_bonds_and_topology_make_a_smarts():
    session = Session("CCC").click_bond(0, 1, button=2).pick("More Bonds and Queries ▸").pick("Single or Double")
    assert editor.annotated(session.state) == "[#6]-,=[#6]-[#6]"
    session.click_bond(1, 2, button=2).pick("More Bonds and Queries ▸").pick("In a Ring")
    assert editor.annotated(session.state) == "[#6]-,=[#6]-&@[#6]"
    session = Session("CCC").hover_bond(0, 1).keys("y")
    assert session.state["bonds"][0][2] == editor.WAVY and editor.annotated(session.state) == "CCC"


def select(session, *indices):
    session.tool("select")
    for index in indices:
        session.click_atom(index, button=4)  # with Shift, to add each
    return session


def test_a_variable_attachment_to_selected_ring_atoms():
    session = select(Session("Clc1ccccc1"), 1, 2, 3)
    session.click_bond(0, 1, button=2).pick("More Bonds and Queries ▸").pick("Attach to Selected Atoms")
    text = editor.annotated(session.state)
    assert text.startswith("*Cl.c1ccccc1 |") and ",m:0:" in text.replace("|", ",|") and "$star_e" in text
    assert len(editor.molecule.parse(text, "smiles").core.GetBondWithIdx(0).GetProp("_MolFileBondEndPts").split()) == 4
    # the bond is drawn to the middle of the ring atoms, and found there
    bond = next(b for b in session.state["bonds"] if editor._bond_extras(b).get("ends"))
    middle = editor._bond_ends(session.state, bond)[1]
    assert editor._bond_at(session.state, UI, *middle)[1:] == bond[:2]


def test_a_repeating_unit_and_a_stereo_group_from_the_selection():
    session = select(Session("CCCC"), 1, 2).click_atom(1, button=2).pick("Markush and Stereo ▸").pick("Repeating Unit...").label("n")
    assert editor.annotated(session.state) == "CCCC |Sg:n:1,2:n::::|"
    session = select(Session("C[C@H](O)CC"), 1).click_atom(1, button=2).pick("Markush and Stereo ▸").pick("Stereo Group...").label("or1")
    assert editor.annotated(session.state) == "CC[C@H](C)O |o1:2|"


def test_an_rgroup_definition_from_the_selection_and_its_logic():
    session = Session("*C1CCCCC1.CCC |$_R1;;;;;;;;;$|")
    select(session, 7)
    session.click_atom(7, button=2).pick("Markush and Stereo ▸").pick("R-group Definition...").label("1")
    assert editor.annotated(session.state) == "*C1CCCCC1 |$_R1;;;;;;$,RG:_R1={CCC}|"
    assert "until then it's its first atom" in session.state["message"]
    session.menu("Structure", "R-group Logic...").label("R1 >0 H")
    assert editor.annotated(session.state) == "*C1CCCCC1 |$_R1;;;;;;$,RG:_R1={CCC},LOG={_R1:;H;>0}|"
    session.keys("\x1a")  # undo takes the logic back too
    assert "LOG" not in editor.annotated(session.state)


def test_the_chain_tool_draws_a_zigzag_as_long_as_the_drag():
    session = Session().tool("chain")
    session.drag(session.cell(0, 0), session.cell(4 * 0.866, 0))
    assert editor.analyse(session.state)[0] == "CCCCC" and "4 bonds" in session.state["message"]
    angles = {round(abs(b[2] - a[2]), 2) for a, b in zip(session.state["atoms"], session.state["atoms"][1:])}
    assert angles == {0.87}  # each bond 30 degrees off the line


def test_structure_commands():
    session = Session("CC(=O)Nc1ccc(O)cc1").menu("Structure", "Calculated Values")
    assert session.state["message"].startswith("C8H9NO2   MW 151.16   exact 151.0633   C 63.56% H 6.00%")
    session.menu("Structure", "Aromatize")
    assert sum(b[2] == editor.AROMATIC for b in session.state["bonds"]) == 6 and session.smiles == "CC(=O)Nc1ccc(O)cc1"
    session.menu("Structure", "Dearomatize")
    assert not any(b[2] == editor.AROMATIC for b in session.state["bonds"]) and session.smiles == "CC(=O)Nc1ccc(O)cc1"
    session.menu("Structure", "Add Explicit Hydrogens")
    assert sum(a[1] == "H" for a in session.state["atoms"]) == 9 and session.smiles == "CC(=O)Nc1ccc(O)cc1"
    session.menu("Structure", "Remove Explicit Hydrogens")
    assert not any(a[1] == "H" for a in session.state["atoms"])
    assert "R1 has no definition" in Session("*CC |$_R1;;$|").menu("Structure", "Check Structure").state["message"]
    assert Session("CCO").menu("Structure", "Check Structure").state["message"] == "Check: no problems found."


def test_a_flip_keeps_the_stereochemistry_and_a_turn_keeps_the_molecule():
    session = Session("C[C@H](O)CC")
    before = session.smiles
    session.menu("Structure", "Flip Horizontal")
    assert session.smiles == before and session.state["bonds"][0][2] == editor.WEDGE  # was hashed
    session.menu("Structure", "Flip Vertical").menu("Structure", "Rotate 90°")
    assert session.smiles == before


def test_clean_up_keeps_the_markush_parts():
    session = Session(MARKUSH)
    session.keys("\x0b")
    assert editor.annotated(session.state) == MARKUSH
    session = select(Session("Clc1ccccc1"), 1, 2, 3)
    session.click_bond(0, 1, button=2).pick("More Bonds and Queries ▸").pick("Attach to Selected Atoms")
    before = editor.annotated(session.state)
    session.keys("\x0b")
    assert editor.annotated(session.state) == before


def test_every_feature_draws():
    text = "*c1cc(*)c(Cl)cc1C[C@H](O)CCC* |$_R1;;;;Q_e;;;;;;;;;;;_R2$,Sg:n:12,13::ht,&1:10,RG:_R1={CCC},{N},_R2={[CH2:1]C[CH2:2]}|"
    session = Session(text)
    assert "\x1b_G" in session.output
    assert editor.annotated(session.state).startswith("*CCC[C@H](O)Cc1cc(Cl)c(*)cc1*")


@pytest.mark.parametrize("change", [
    lambda s: s["atoms"][0].append({"iso": 999}),
    lambda s: s["atoms"][0].append({"r": 1}),
    lambda s: s["atoms"][0].append({}),
    lambda s: s["atoms"][0].append({"shell": 1}),
    lambda s: s["atoms"][0].append({"st": "and10"}),
    lambda s: s["atoms"][0].append({"sru": [1, "n; rm"]}),
    lambda s: s["atoms"][0].append({"link": [4, 1]}),
    lambda s: s["atoms"][0].__setitem__(1, "R#"),
    lambda s: s["atoms"][0][1:2] == ["L"] or s["atoms"][0].__setitem__(1, "L") or s["atoms"][0].append({"list": ["Xx"]}),
    lambda s: s["bonds"][0].append({"ends": [99, 98]}),
    lambda s: s["bonds"][0].append({"topo": "loop"}),
    lambda s: s.update(rlogic={"1": {"then": 1, "rest_h": False, "occurrence": ""}}),
    lambda s: s.update(rlogic={"1": {"then": 0, "rest_h": False, "occurrence": "; rm"}}),
    lambda s: s.update(prompt={"targets": [["a", 1]], "text": "", "kind": "shell"}),
    lambda s: s.update(undo=[[s["atoms"], s["bonds"], s["next"], s["notes"]]]),
])
def test_a_tampered_markush_state_is_refused(change):
    state = Session("CCC").state
    change(state)
    with pytest.raises(editor.StateError):
        editor.decode(editor.encode(state))


def test_the_list_keeps_a_markush_structure():
    status, state, output = board.step("", MARKUSH + " Library\nCCO Ethanol", "", 120, 40, 10, 20, True)
    assert status == editor.CONTINUE and "Library" in output
    status, _, printed = board.step(state, "", "q", 120, 40, 10, 20, True)
    assert status == editor.QUIT and printed == MARKUSH + " Library\nCCO Ethanol"
