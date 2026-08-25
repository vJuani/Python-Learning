PROPERTY_TYPES = (
    "apartment",
    "house",
    "ph",
    "land",
    "commercial",
    "office",
    "other",
)

LISTING_PURPOSES = (
    "sale",
    "rental",
    "temporary_rental",
)

INVOICE_FULL_COMMISSION_VALUES = (
    "yes",
    "no",
)


def is_valid_listing_purpose(value):
    return value in LISTING_PURPOSES
