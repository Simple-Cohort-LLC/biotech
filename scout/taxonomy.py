"""Keyword rulebook for sector classification.

Used as the fallback when the Claude classifier is unavailable, and as the
sanity check on its output. Tune the keyword lists as you see misses — this
file is meant to be edited.
"""

from __future__ import annotations

import re

# Ordered most-specific to least. First rule with enough hits wins, so a company
# matching both "crispr" and "platform" lands in synthetic-biology, not techbio.
SECTOR_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "therapeutics",
        (
            "therapeutic", "drug", "clinical trial", "preclinical", "oncology",
            "immunotherapy", "antibody", "small molecule", "gene therapy",
            "cell therapy", "car-t", "mrna", "vaccine", "biologic", "peptide",
            "rnai", "antisense", "kinase inhibitor", "ind-enabling", "phase 1",
            "phase i", "pharmacology", "indication", "efficacy",
        ),
    ),
    (
        "diagnostics",
        (
            "diagnostic", "assay", "biomarker", "liquid biopsy", "screening test",
            "point-of-care", "in vitro diagnostic", "ivd", "companion diagnostic",
            "early detection", "pathology", "cytology", "clia",
        ),
    ),
    (
        "synthetic-biology",
        (
            "synthetic biology", "crispr", "gene editing", "genome engineering",
            "directed evolution", "strain engineering", "cell-free", "biofoundry",
            "protein engineering", "metabolic engineering", "dna synthesis",
            "base editing", "prime editing",
        ),
    ),
    (
        "tools-and-instruments",
        (
            "sequencing", "spectrometry", "microscopy", "reagent", "instrument",
            "microfluidic", "flow cytometry", "lab automation", "sample prep",
            "single-cell", "spatial transcriptomics", "cryo-em", "consumable",
            "benchtop",
        ),
    ),
    (
        "techbio-and-bioinformatics",
        (
            "machine learning", "artificial intelligence", "computational",
            "bioinformatics", "in silico", "foundation model", "protein structure",
            "molecular dynamics", "generative model", "algorithm", "data platform",
            "digital twin", "simulation",
        ),
    ),
    (
        "ag-and-food-tech",
        (
            "agriculture", "agricultural", "crop", "livestock", "soil", "seed trait",
            "fertilizer", "aquaculture", "alternative protein", "cultivated meat",
            "precision fermentation", "food safety", "plant-based", "agtech",
        ),
    ),
    (
        "industrial-biotech",
        (
            "industrial biotechnology", "biomanufacturing", "bioprocess",
            "enzyme", "biofuel", "biopolymer", "bioplastic", "fermentation",
            "carbon capture", "biocatalysis", "green chemistry", "bioremediation",
        ),
    ),
    (
        "medtech-and-devices",
        (
            "medical device", "implant", "catheter", "surgical", "prosthe",
            "wearable sensor", "endoscop", "robotic surgery", "510(k)", "pma",
            "neurostimulation", "orthopedic",
        ),
    ),
    (
        "digital-health",
        (
            "digital health", "telehealth", "telemedicine", "patient engagement",
            "care delivery", "ehr", "electronic health record", "remote monitoring",
            "clinical workflow", "health app", "care management",
        ),
    ),
]

# If none of these appear anywhere, the record probably is not biotech at all.
BIOTECH_GATE = (
    "bio", "genom", "protein", "cell", "molecul", "drug", "therap", "clinic",
    "diagnos", "medic", "health", "pharma", "disease", "patient", "enzyme",
    "dna", "rna", "gene", "vaccin", "antibod", "microb", "immun", "tissue",
    "assay", "life science", "crispr", "sequenc", "oncolog", "neuro",
)


def _hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for kw in keywords if kw in text)


def looks_like_biotech(text: str) -> bool:
    return any(marker in text.lower() for marker in BIOTECH_GATE)


def classify(name: str, description: str) -> tuple[str, float]:
    """Return (sector, confidence). Confidence is crude: it scales with hits."""
    text = f"{name} {description}".lower()
    text = re.sub(r"\s+", " ", text)

    best_sector = "unknown"
    best_hits = 0
    for sector, keywords in SECTOR_RULES:
        count = _hits(text, keywords)
        if count > best_hits:
            best_sector, best_hits = sector, count

    if best_hits == 0:
        return "unknown", 0.0
    # 1 hit -> 0.4, 2 -> 0.55, 3 -> 0.7, 4+ -> 0.8 (capped; rules never claim certainty)
    confidence = min(0.25 + 0.15 * best_hits, 0.8)
    return best_sector, confidence
