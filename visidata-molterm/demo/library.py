"""A library of 1000 compounds for the live demo: every amide of 40 carboxylic acids and 25 amines.

Made up, but each one is a real, sensible structure, and the set is varied enough to sort and filter: aryl, heteroaryl and alkyl acids;
primary, secondary, cyclic and aromatic amines. It needs no chemistry toolkit: each acid's group is written from the atom that bonds to
the carbonyl and each amine from its nitrogen, so the SMILES of an amide is O=C(<group>)<amine>. Ring numbers inside the brackets close
before the amine starts, so the two can't clash. Run it to write the CSV: python demo/library.py [path]
"""
import csv
import os
import sys

# (series, name, the group as written from the atom bonded to the carbonyl)
ACIDS = [
    ("aryl", "benzoic", "c1ccccc1"), ("aryl", "4-fluorobenzoic", "c1ccc(F)cc1"), ("aryl", "4-chlorobenzoic", "c1ccc(Cl)cc1"),
    ("aryl", "4-methoxybenzoic", "c1ccc(OC)cc1"), ("aryl", "4-(trifluoromethyl)benzoic", "c1ccc(C(F)(F)F)cc1"),
    ("aryl", "4-cyanobenzoic", "c1ccc(C#N)cc1"), ("aryl", "3-bromobenzoic", "c1cccc(Br)c1"), ("aryl", "salicylic", "c1ccccc1O"),
    ("aryl", "2-naphthoic", "c1ccc2ccccc2c1"), ("aryl", "biphenyl-4-carboxylic", "c1ccc(-c2ccccc2)cc1"),
    ("aryl", "3,5-difluorobenzoic", "c1cc(F)cc(F)c1"), ("aryl", "4-(dimethylamino)benzoic", "c1ccc(N(C)C)cc1"),
    ("aryl", "4-nitrobenzoic", "c1ccc([N+](=O)[O-])cc1"), ("aryl", "4-(methylsulfonyl)benzoic", "c1ccc(S(C)(=O)=O)cc1"),
    ("aryl", "4-methylbenzoic", "c1ccc(C)cc1"), ("aryl", "4-hydroxybenzoic", "c1ccc(O)cc1"), ("aryl", "4-aminobenzoic", "c1ccc(N)cc1"),
    ("heteroaryl", "picolinic", "c1ccccn1"), ("heteroaryl", "nicotinic", "c1cccnc1"), ("heteroaryl", "isonicotinic", "c1ccncc1"),
    ("heteroaryl", "thiophene-2-carboxylic", "c1cccs1"), ("heteroaryl", "2-furoic", "c1ccco1"),
    ("heteroaryl", "indole-5-carboxylic", "c1ccc2[nH]ccc2c1"), ("heteroaryl", "quinoxaline-2-carboxylic", "c1cnc2ccccc2n1"),
    ("heteroaryl", "imidazole-2-carboxylic", "c1ncc[nH]1"), ("heteroaryl", "5-methylisoxazole-3-carboxylic", "c1cc(C)on1"),
    ("heteroaryl", "piperonylic", "c1ccc2c(c1)OCO2"), ("heteroaryl", "benzothiazole-2-carboxylic", "c1nc2ccccc2s1"),
    ("alkyl", "cyclopropanecarboxylic", "C1CC1"), ("alkyl", "cyclopentanecarboxylic", "C1CCCC1"),
    ("alkyl", "cyclohexanecarboxylic", "C1CCCCC1"), ("alkyl", "pivalic", "C(C)(C)C"), ("alkyl", "isovaleric", "CC(C)C"),
    ("alkyl", "phenylacetic", "Cc1ccccc1"), ("alkyl", "3-phenylpropanoic", "CCc1ccccc1"), ("alkyl", "phenoxyacetic", "COc1ccccc1"),
    ("alkyl", "tetrahydropyran-4-carboxylic", "C1CCOCC1"), ("alkyl", "Boc-proline", "[C@@H]1CCCN1C(=O)OC(C)(C)C"),
    ("alkyl", "cinnamic", "/C=C/c1ccccc1"), ("alkyl", "1-methylpiperidine-4-carboxylic", "C1CCN(C)CC1"),
]
# (name, the amine as written from its nitrogen)
AMINES = [
    ("benzylamine", "NCc1ccccc1"), ("morpholine", "N1CCOCC1"), ("piperidine", "N1CCCCC1"), ("1-methylpiperazine", "N1CCN(C)CC1"),
    ("pyrrolidine", "N1CCCC1"), ("dimethylamine", "N(C)C"), ("ethylamine", "NCC"), ("cyclopropylamine", "NC1CC1"),
    ("aniline", "Nc1ccccc1"), ("4-fluoroaniline", "Nc1ccc(F)cc1"), ("2-aminopyridine", "Nc1ccccn1"), ("ethanolamine", "NCCO"),
    ("N,N-dimethylethylenediamine", "NCCN(C)C"), ("1-phenylethylamine", "NC(C)c1ccccc1"), ("4-hydroxypiperidine", "N1CCC(O)CC1"),
    ("tetrahydroisoquinoline", "N1CCc2ccccc2C1"), ("furfurylamine", "NCc1ccco1"), ("2-aminothiazole", "Nc1nccs1"),
    ("tryptamine", "NCCc1c[nH]c2ccccc12"), ("1-phenylpiperazine", "N1CCN(c2ccccc2)CC1"), ("tert-butylamine", "NC(C)(C)C"),
    ("glycine methyl ester", "NCC(=O)OC"), ("3,4-methylenedioxyaniline", "Nc1ccc2c(c1)OCO2"), ("tetrahydrofurfurylamine", "NCC1CCCO1"),
    ("ammonia", "N"),
]
FIELDS = ["id", "acid", "amine", "series", "smiles"]


def compounds():
    """The library, as rows of FIELDS, acids in the outer loop."""
    rows = []
    for series, acid, group in ACIDS:
        for amine, nitrogen in AMINES:
            rows.append([f"MT-{len(rows) + 1:04d}", acid, amine, series, f"O=C({group}){nitrogen}"])
    return rows


def write(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".part"
    with open(temporary, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(FIELDS)
        out.writerows(compounds())
    os.replace(temporary, path)
    return path


if __name__ == "__main__":
    print(write(sys.argv[1] if len(sys.argv) > 1 else "/tmp/compounds.csv"), file=sys.stderr)
