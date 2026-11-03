"""English texts, European date and time conventions."""

DATE_FORMAT = "j M Y"
DATETIME_FORMAT = "j M Y, H:i"
TIME_FORMAT = "H:i"
YEAR_MONTH_FORMAT = "F Y"
MONTH_DAY_FORMAT = "j F"
SHORT_DATE_FORMAT = "d.m.Y"
SHORT_DATETIME_FORMAT = "d.m.Y H:i"
FIRST_DAY_OF_WEEK = 1  # Monday

# The first format is the one the admin date picker writes.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"]
DATETIME_INPUT_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d",
]
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = "'"
NUMBER_GROUPING = 3
