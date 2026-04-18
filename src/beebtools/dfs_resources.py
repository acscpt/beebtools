# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Resource strings for the DFS format engine.

The ``RESOURCES`` mapping is a simple map to map of resource strings.  The primary
key is the consuming module name and then resource property names to their string
values.
"""


RESOURCES = {
    "cli": {
        "attrib.access":
            "DFS:\n"
            "  Absolute: L, LOCKED, \"\" (unlock)\n"
            "  Mutation: +L, -L\n"
    },
}
