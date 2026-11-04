# SPDX-License-Identifier: AGPL-3.0-only
"""Parser vocabulary, independent of database ids (ADR 0019).

Canonical spellings are returned without converting quantities. In particular,
packages stay packages; a parenthesized weight is a note, not a conversion.
Only volume-to-volume or mass-to-mass conversion is allowed by ADR 0019;
conversion and database seeding are outside the line parser.
"""

REVIEW_THRESHOLD = 0.8

# Canonical spelling -> accepted spellings. Case and a terminal period are
# normalized by the parser. Keep the table conservative; unknown text survives.
UNIT_SYNONYMS = {
    "tbsp": ("tbsp", "tablespoon", "tablespoons"),
    "tsp": ("tsp", "teaspoon", "teaspoons"),
    "cup": ("cup", "cups"),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "oz": ("oz", "ounce", "ounces"),
    "g": ("g", "gram", "grams"),
    "kg": ("kg", "kilogram", "kilograms"),
    "ml": ("ml", "milliliter", "milliliters"),
    "l": ("l", "liter", "liters"),
    "can": ("can", "cans"),
    "jar": ("jar", "jars"),
    "clove": ("clove", "cloves"),
}
UNIT_ALIASES = {
    alias: canonical
    for canonical, aliases in UNIT_SYNONYMS.items()
    for alias in aliases
}
PACKAGE_UNITS = frozenset({"can", "jar"})
