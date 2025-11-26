"""
Entity type weights for scoring and retrieval.

These weights reflect the relative importance of different entity types
for a personal assistant context. PERSON entities are weighted highest
because interpersonal relationships are typically most relevant.
"""

ENTITY_TYPE_WEIGHTS = {
    "PERSON": 1.0,       # People are most important for personal assistant
    "EVENT": 0.9,        # Events have temporal significance
    "ORG": 0.8,          # Organizations (work, schools)
    "PRODUCT": 0.7,      # Products, tools
    "WORK_OF_ART": 0.6,  # Books, movies, art
    "GPE": 0.5,          # Geographic/political entities (cities, countries)
    "NORP": 0.5,         # Nationalities, religious/political groups
    "LAW": 0.5,          # Legal references
    "FAC": 0.4,          # Facilities
    "LANGUAGE": 0.3,     # Language references
}

# Query-time priming configuration
ENTITY_BOOST_COEFFICIENT = 0.15  # Scales entity match contribution to boost
MAX_ENTITY_BOOST = 0.3  # Cap boost factor at 1.3x (1.0 + 0.3)
FUZZY_MATCH_THRESHOLD = 0.85  # Minimum similarity for fuzzy entity matching


def get_weight(entity_type: str) -> float:
    """
    Get weight for entity type.

    Args:
        entity_type: spaCy NER entity type (PERSON, ORG, etc.)

    Returns:
        Weight between 0.0 and 1.0, defaults to 0.5 for unknown types
    """
    return ENTITY_TYPE_WEIGHTS.get(entity_type, 0.5)
