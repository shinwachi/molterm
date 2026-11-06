// The demo page's samples, the same as the server's (server/samples.py): [group, [[name, SMILES, formula], ...]].
export const GROUPS = [
    ["Everyday", [
        ["Water", "O", "H2O"],
        ["Ethanol", "CCO", "C2H6O"],
        ["Acetic acid", "CC(=O)O", "C2H4O2"],
        ["Caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C", "C8H10N4O2"],
        ["Nicotine", "CN1CCC[C@H]1c1cccnc1", "C10H14N2"],
        ["Table salt", "[Na+].[Cl-]", "ClNa"],
    ]],
    ["Medicines", [
        ["Aspirin", "CC(=O)Oc1ccccc1C(=O)O", "C9H8O4"],
        ["Paracetamol", "CC(=O)Nc1ccc(O)cc1", "C8H9NO2"],
        ["Ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O", "C13H18O2"],
        ["Fluoxetine", "CNCCC(Oc1ccc(cc1)C(F)(F)F)c1ccccc1", "C17H18F3NO"],
        ["Penicillin G", "CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O", "C16H18N2O4S"],
        ["Morphine", "CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5", "C17H19NO3"],
    ]],
    ["Biology", [
        ["L-Alanine", "C[C@H](N)C(=O)O", "C3H7NO2"],
        ["Glycine, zwitterion", "[NH3+]CC(=O)[O-]", "C2H5NO2"],
        ["Glucose", "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O", "C6H12O6"],
        ["Adenine", "Nc1ncnc2[nH]cnc12", "C5H5N5"],
        ["Serotonin", "NCCc1c[nH]c2ccc(O)cc12", "C10H12N2O"],
        ["AMP", "Nc1ncnc2c1ncn2[C@@H]1O[C@H](COP(=O)(O)O)[C@@H](O)[C@H]1O", "C10H14N5O7P"],
        ["Cholesterol", "C[C@H](CCCC(C)C)[C@H]1CC[C@@H]2[C@@]1(CC[C@H]3[C@H]2CC=C4[C@@]3(CC[C@@H](C4)O)C)C", "C27H46O"],
    ]],
    ["Shapes", [
        ["Benzene", "c1ccccc1", "C6H6"],
        ["Naphthalene", "c1ccc2ccccc2c1", "C10H8"],
        ["Cubane", "C12C3C4C1C5C2C3C45", "C8H8"],
        ["Adamantane", "C1C2CC3CC1CC(C2)C3", "C10H16"],
    ]],
];
export const DEFAULT = "Aspirin";
