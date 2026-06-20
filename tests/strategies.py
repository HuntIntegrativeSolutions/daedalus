"""Hypothesis strategies for CIP elementary data types."""

from hypothesis import strategies as st

# Signed integers
sint_values = st.integers(min_value=-128, max_value=127)
int_values = st.integers(min_value=-32768, max_value=32767)
dint_values = st.integers(min_value=-(2**31), max_value=2**31 - 1)
lint_values = st.integers(min_value=-(2**63), max_value=2**63 - 1)

# Unsigned integers
usint_values = st.integers(min_value=0, max_value=255)
uint_values = st.integers(min_value=0, max_value=65535)
udint_values = st.integers(min_value=0, max_value=2**32 - 1)
ulint_values = st.integers(min_value=0, max_value=2**64 - 1)

# Floats — avoid NaN/inf for simple round-trip since struct.pack preserves them
real_values = st.floats(allow_nan=False, allow_infinity=False, width=32)
lreal_values = st.floats(allow_nan=False, allow_infinity=False)

# Boolean
bool_values = st.booleans()

# Bit arrays (list of exactly N bools)
byte_array_values = st.lists(st.booleans(), min_size=8, max_size=8)
word_array_values = st.lists(st.booleans(), min_size=16, max_size=16)
dword_array_values = st.lists(st.booleans(), min_size=32, max_size=32)
lword_array_values = st.lists(st.booleans(), min_size=64, max_size=64)

# Strings (printable ASCII subset for codec testing)
ascii_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=200,
)

# Logical segment parameters
logical_types = st.sampled_from(
    [
        "class_id",
        "instance_id",
        "member_id",
        "connection_point",
        "attribute_id",
        "service_id",
    ]
)
logical_values_8bit = st.integers(min_value=0, max_value=0xFF)
logical_values_16bit = st.integers(min_value=0x100, max_value=0xFFFF)
logical_values_32bit = st.integers(min_value=0x10000, max_value=0xFFFF_FFFF)
