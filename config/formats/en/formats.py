"""English texts; dates are always YYYY-MM-DD."""

DATE_FORMAT = "Y-m-d"
DATETIME_FORMAT = "Y-m-d H:i"
TIME_FORMAT = "H:i"
YEAR_MONTH_FORMAT = "Y-m"
MONTH_DAY_FORMAT = "m-d"
SHORT_DATE_FORMAT = "Y-m-d"
SHORT_DATETIME_FORMAT = "Y-m-d H:i"
FIRST_DAY_OF_WEEK = 1  # Monday

# The first format is the one the admin date picker writes.
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"]
DATETIME_INPUT_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d",
]
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = "'"
NUMBER_GROUPING = 3
