from dataclasses import dataclass


@dataclass(frozen=True)
class BlsSeries:
    series_id: str
    description: str
    units: str
    survey: str  # "CES" (establishment) or "CPS" (household)


# Headline series from the monthly Employment Situation release.
# All series are seasonally adjusted.
EMPLOYMENT_SITUATION_SERIES: list[BlsSeries] = [
    BlsSeries("CES0000000001", "Total nonfarm payroll employment", "thousands", "CES"),
    BlsSeries("CES0500000001", "All employees, total private", "thousands", "CES"),
    BlsSeries("CES0500000002", "Average weekly hours, total private", "hours", "CES"),
    BlsSeries("CES0500000003", "Average hourly earnings, total private", "dollars", "CES"),
    BlsSeries("CES0500000007", "Average weekly earnings, total private", "dollars", "CES"),
    BlsSeries("CES0600000001", "Goods-producing employment", "thousands", "CES"),
    BlsSeries("CES0800000001", "Service-providing employment", "thousands", "CES"),
    BlsSeries("LNS14000000", "Unemployment rate", "percent", "CPS"),
    BlsSeries("LNS11300000", "Labor force participation rate", "percent", "CPS"),
    BlsSeries("LNS12300000", "Employment-population ratio", "percent", "CPS"),
    BlsSeries("LNS12000000", "Civilian employment level", "thousands", "CPS"),
    BlsSeries("LNS13000000", "Civilian unemployed level", "thousands", "CPS"),
    BlsSeries("LNS13327709", "U-6 alternative measure of labor underutilization", "percent", "CPS"),
    BlsSeries("LNS11000000", "Civilian labor force level", "thousands", "CPS"),
]
