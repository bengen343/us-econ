from dataclasses import dataclass


@dataclass(frozen=True)
class BlsSeries:
    series_id: str
    description: str
    units: str
    survey: str  # "CES" (establishment) or "CPS" (household)
    seasonally_adjusted: bool


# Series tracked from the monthly Employment Situation release and related
# CPS/CES tables. Seasonal adjustment is also encoded in the series-ID prefix
# (CES*/LNS* = SA, CEU*/LNU* = NSA) but is stored explicitly so downstream
# queries can filter without substring matching.
EMPLOYMENT_SITUATION_SERIES: list[BlsSeries] = [
    # CES headline aggregates, hours, and earnings (seasonally adjusted)
    BlsSeries("CES0000000001", "Total nonfarm payroll employment", "thousands", "CES", True),
    BlsSeries("CES0500000001", "All employees, total private", "thousands", "CES", True),
    BlsSeries("CES0500000002", "Average weekly hours, total private", "hours", "CES", True),
    BlsSeries("CES0500000003", "Average hourly earnings, total private", "dollars", "CES", True),
    BlsSeries("CES0500000007", "Average weekly earnings, total private", "dollars", "CES", True),
    BlsSeries("CES0600000001", "Goods-producing employment", "thousands", "CES", True),
    BlsSeries("CES0800000001", "Service-providing employment", "thousands", "CES", True),

    # CES employment by supersector / industry (seasonally adjusted)
    BlsSeries("CES1000000001", "All employees, mining and logging", "thousands", "CES", True),
    BlsSeries("CES2000000001", "All employees, construction", "thousands", "CES", True),
    BlsSeries("CES3000000001", "All employees, manufacturing", "thousands", "CES", True),
    BlsSeries("CES3100000001", "All employees, durable goods", "thousands", "CES", True),
    BlsSeries("CES3200000001", "All employees, nondurable goods", "thousands", "CES", True),
    BlsSeries("CES4000000001", "All employees, trade, transportation, and utilities", "thousands", "CES", True),
    BlsSeries("CES4142000001", "All employees, wholesale trade", "thousands", "CES", True),
    BlsSeries("CES4200000001", "All employees, retail trade", "thousands", "CES", True),
    BlsSeries("CES4300000001", "All employees, transportation and warehousing", "thousands", "CES", True),
    BlsSeries("CES4422000001", "All employees, utilities", "thousands", "CES", True),
    BlsSeries("CES5000000001", "All employees, information", "thousands", "CES", True),
    BlsSeries("CES5500000001", "All employees, financial activities", "thousands", "CES", True),
    BlsSeries("CES6000000001", "All employees, professional and business services", "thousands", "CES", True),
    BlsSeries("CES6056130001", "All employees, employment services", "thousands", "CES", True),
    BlsSeries("CES6056132001", "All employees, temporary help services", "thousands", "CES", True),
    BlsSeries("CES6500000001", "All employees, private education and health services", "thousands", "CES", True),
    BlsSeries("CES7000000001", "All employees, leisure and hospitality", "thousands", "CES", True),
    BlsSeries("CES8000000001", "All employees, other services", "thousands", "CES", True),
    BlsSeries("CES9000000001", "All employees, government", "thousands", "CES", True),
    BlsSeries("CES9091000001", "All employees, federal government", "thousands", "CES", True),
    BlsSeries("CES9092000001", "All employees, state government", "thousands", "CES", True),
    BlsSeries("CES9093000001", "All employees, local government", "thousands", "CES", True),

    # CEU employment by supersector / industry (not seasonally adjusted)
    BlsSeries("CEU0000000001", "Total nonfarm payroll employment", "thousands", "CES", False),
    BlsSeries("CEU0500000001", "All employees, total private", "thousands", "CES", False),
    BlsSeries("CEU0600000001", "Goods-producing employment", "thousands", "CES", False),
    BlsSeries("CEU0800000001", "Service-providing employment", "thousands", "CES", False),
    BlsSeries("CEU1000000001", "All employees, mining and logging", "thousands", "CES", False),
    BlsSeries("CEU2000000001", "All employees, construction", "thousands", "CES", False),
    BlsSeries("CEU3000000001", "All employees, manufacturing", "thousands", "CES", False),
    BlsSeries("CEU3100000001", "All employees, durable goods", "thousands", "CES", False),
    BlsSeries("CEU3200000001", "All employees, nondurable goods", "thousands", "CES", False),
    BlsSeries("CEU4000000001", "All employees, trade, transportation, and utilities", "thousands", "CES", False),
    BlsSeries("CEU4142000001", "All employees, wholesale trade", "thousands", "CES", False),
    BlsSeries("CEU4200000001", "All employees, retail trade", "thousands", "CES", False),
    BlsSeries("CEU4300000001", "All employees, transportation and warehousing", "thousands", "CES", False),
    BlsSeries("CEU4422000001", "All employees, utilities", "thousands", "CES", False),
    BlsSeries("CEU5000000001", "All employees, information", "thousands", "CES", False),
    BlsSeries("CEU5500000001", "All employees, financial activities", "thousands", "CES", False),
    BlsSeries("CEU6000000001", "All employees, professional and business services", "thousands", "CES", False),
    BlsSeries("CEU6056130001", "All employees, employment services", "thousands", "CES", False),
    BlsSeries("CEU6056132001", "All employees, temporary help services", "thousands", "CES", False),
    BlsSeries("CEU6500000001", "All employees, private education and health services", "thousands", "CES", False),
    BlsSeries("CEU7000000001", "All employees, leisure and hospitality", "thousands", "CES", False),
    BlsSeries("CEU8000000001", "All employees, other services", "thousands", "CES", False),
    BlsSeries("CEU9000000001", "All employees, government", "thousands", "CES", False),
    BlsSeries("CEU9091000001", "All employees, federal government", "thousands", "CES", False),
    BlsSeries("CEU9092000001", "All employees, state government", "thousands", "CES", False),
    BlsSeries("CEU9093000001", "All employees, local government", "thousands", "CES", False),

    # CPS labor force measures (seasonally adjusted)
    BlsSeries("LNS14000000", "Unemployment rate", "percent", "CPS", True),
    BlsSeries("LNS11300000", "Labor force participation rate", "percent", "CPS", True),
    BlsSeries("LNS12300000", "Employment-population ratio", "percent", "CPS", True),
    BlsSeries("LNS12000000", "Civilian employment level", "thousands", "CPS", True),
    BlsSeries("LNS13000000", "Civilian unemployed level", "thousands", "CPS", True),
    BlsSeries("LNS13327709", "U-6 alternative measure of labor underutilization", "percent", "CPS", True),
    BlsSeries("LNS11000000", "Civilian labor force level", "thousands", "CPS", True),

    # CPS labor force measures (not seasonally adjusted)
    BlsSeries("LNU01000000", "Civilian labor force level", "thousands", "CPS", False),
    BlsSeries("LNU03000000", "Civilian unemployed level", "thousands", "CPS", False),
]
