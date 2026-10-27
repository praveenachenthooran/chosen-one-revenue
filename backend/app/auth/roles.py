"""The seven PRD roles and how workbook role names map onto them."""

SELLER_AE = "Seller / Account Executive"
CLIENT_PARTNER = "Client Partner"
COMMERCIAL_OWNER = "Commercial Owner"
SALES_LEADERSHIP = "CRO / Sales Leadership"
FINANCE = "Finance"
RESOURCING = "Delivery / Resourcing"
ADMIN = "Administrator"

ALL_ROLES = (
    SELLER_AE,
    CLIENT_PARTNER,
    COMMERCIAL_OWNER,
    SALES_LEADERSHIP,
    FINANCE,
    RESOURCING,
    ADMIN,
)

# Role names as written on the workbook's Users sheet.
WORKBOOK_ROLE_ALIASES = {
    "Seller / AE": SELLER_AE,
    "Sales leadership": SALES_LEADERSHIP,
    "Resourcing (view only)": RESOURCING,
    "Admin": ADMIN,
}

# An ownership assignment only grants edit rights to someone who also holds the matching role.
OWNER_ROLE_TO_USER_ROLE = {
    "client_partner": CLIENT_PARTNER,
    "ae": SELLER_AE,
    "commercial_owner": COMMERCIAL_OWNER,
}
OWNER_ROLE_LABELS = {
    "client_partner": "Client Partner",
    "ae": "Account Executive",
    "commercial_owner": "Commercial Owner",
}

# Roles that may edit any project (PRD FR-03: "plus Sales leadership and Admin").
EDIT_ANY_ROLES = frozenset({SALES_LEADERSHIP, ADMIN})
# Roles that may read a project's audit history without owning it (traceability for Finance).
HISTORY_ANY_ROLES = frozenset({SALES_LEADERSHIP, ADMIN, FINANCE})


def canonical_role(name: str) -> str:
    return WORKBOOK_ROLE_ALIASES.get(name, name)
