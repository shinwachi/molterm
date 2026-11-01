"""The guided medicinal chemistry session, used by both the automated demo and the walkthrough.

Every finding stated in `after` is checked against the real output by tests/test_medchem.py.
"""
QUINAZOLINE = "c1ccc2ncncc2c1"
ACRYLAMIDE = "C=CC(=O)N"

STEPS = [
    {
        "title": "See the whole set",
        "command": "grid",
        "before": "You have inherited a list of 15 known compounds, mostly kinase inhibitors. "
                  "Before any numbers, look at the chemistry.",
        "after": "All 15 structures in the terminal, as plain text. There is no image viewer or file to open, so this "
                 "works just as well over ssh.",
    },
    {
        "title": "Rank by drug-likeness",
        "command": "props qed",
        "before": "Now the numbers: properties computed from each structure, sorted by QED, a drug-likeness score.",
        "after": "Lapatinib and neratinib come last and break two Rule of Five limits each, yet both are approved drugs. "
                 "These scores help you triage. They don't decide for you.",
    },
    {
        "title": "Check for assay troublemakers",
        "command": "alerts pains",
        "before": "PAINS filters flag groups known to give false positives in assays. A table can name the alert, "
                  "but a drawing shows where it is.",
        "after": "Two hits, marked in cyan on the structure: the catechol in quercetin and the ene-rhodanine in epalrestat.",
    },
    {
        "title": "Narrow the working set",
        "command": "filter pains=0",
        "before": "For a kinase project, set those two aside. Later commands only look at what is left.",
        "after": "13 compounds left. Epalrestat is an approved drug, so this means 'be careful in assays', not 'bad molecule'.",
    },
    {
        "title": "Plot size against lipophilicity",
        "command": "plot mw logp",
        "before": "Where does the set sit in property space? The plot is drawn in the same Braille dots as the molecules.",
        "after": "Four compounds are past a limit line: lapatinib, neratinib, dacomitinib and vandetanib. "
                 "Kinase inhibitors often run large and greasy.",
    },
    {
        "title": "Find the shared core",
        "command": f"match {QUINAZOLINE}",
        "before": "Many of these look related. Search for the quinazoline ring system with a SMARTS pattern.",
        "after": "7 of 13 share it, marked in cyan. Neratinib and osimertinib don't, although they hit related targets.",
    },
    {
        "title": "Find the covalent warhead",
        "command": f"match {ACRYLAMIDE}",
        "before": "Some of these inhibitors are covalent: they carry an acrylamide that reacts with a cysteine in the kinase.",
        "after": "Afatinib, dacomitinib, neratinib and osimertinib. The core search couldn't show this group, since two of "
                 "them aren't quinazolines.",
    },
    {
        "title": "Question the alert",
        "command": "alerts brenk",
        "before": "The Brenk filters look for unwanted groups in general. See what they make of those four.",
        "after": "All four are flagged as Michael acceptors, on the warhead that was designed in on purpose. "
                 "An alert is a question to answer, not a verdict.",
    },
    {
        "title": "Read the structure-activity layout",
        "command": f"rgroups {QUINAZOLINE}",
        "before": "Line up the 7 quinazolines around their shared core, to see what chemists varied at each position.",
        "after": "R1 is the aniline, in every compound. R2 and R3 carry the solubilising tails, and the acrylamides of "
                 "afatinib and dacomitinib sit in R3.",
    },
    {
        "title": "Find close analogues",
        "command": "sim gefitinib",
        "before": "Starting from gefitinib, which compounds are structurally closest?",
        "after": "Dacomitinib is nearest at 0.53, with the same chlorofluoroaniline and methoxy. Vandetanib and afatinib "
                 "tie for second, though vandetanib's aniline is different, so read fingerprints with care.",
    },
    {
        "title": "Compare with the nearest",
        "command": "compare gefitinib dacomitinib",
        "before": "Set gefitinib beside its nearest neighbour. Cyan marks whatever the two don't share.",
        "after": "The quinazoline and aniline stay. Gefitinib's morpholine tail gives way to the warhead, which adds 23 to MW "
                 "and takes cLogP to 5.2, past the limit.",
    },
    {
        "title": "Open one compound",
        "command": "show osimertinib",
        "before": "Finally, one compound's full record: structure, properties and alerts together.",
        "after": "That's the session. It only used structures, so it says nothing about potency. "
                 "Now try your own commands: help lists them all.",
    },
]
