from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimsMeasure:
    measure: str
    description: str
    units: str


MEASURES: list[ClaimsMeasure] = [
    ClaimsMeasure(
        "initial_claims",
        "Initial unemployment insurance claims filed during the week",
        "persons",
    ),
    ClaimsMeasure(
        "continued_claims",
        "Continued unemployment insurance claims (insured unemployment)",
        "persons",
    ),
    ClaimsMeasure(
        "iur",
        "Insured unemployment rate (continued claims as a percent of UI-covered employment)",
        "percent",
    ),
    ClaimsMeasure(
        "covered_employment",
        "UI-covered employment (denominator used for the insured unemployment rate)",
        "persons",
    ),
]

MEASURES_BY_KEY: dict[str, ClaimsMeasure] = {m.measure: m for m in MEASURES}


# 50 states + DC + PR + VI, matching the option list on
# https://oui.doleta.gov/unemploy/claims.asp.
STATES: list[tuple[str, str]] = [
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AR", "Arkansas"),
    ("AZ", "Arizona"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("DC", "District of Columbia"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("NE", "Nebraska"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NV", "Nevada"),
    ("NY", "New York"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("PR", "Puerto Rico"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VI", "Virgin Islands"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
]

STATE_CODES: list[str] = [code for code, _ in STATES]
STATE_NAME_TO_CODE: dict[str, str] = {name: code for code, name in STATES}
