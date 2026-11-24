"""Column names this plugin reads from an OCEL and writes back into one."""

# OCEL 2.0 column names, as Ocelescope stores them.
EID = "ocel:eid"
OID = "ocel:oid"
ACTIVITY = "ocel:activity"
OTYPE = "ocel:type"
TIMESTAMP = "ocel:timestamp"
QUALIFIER = "ocel:qualifier"

# Quantity extension (QEL) column names.
ITEM_TYPE = "qel:item_type"
QUANTITY = "qel:quantity"

# Attributes this plugin adds to the annotated log it returns. Events carry the
# emissions caused by the event; objects carry the share allocated to them, so
# the object column sums to (at most) the event column over the whole log.
EVENT_CO2_ATTRIBUTE = "carbon:event_co2e"
OBJECT_CO2_ATTRIBUTE = "carbon:allocated_co2e"
