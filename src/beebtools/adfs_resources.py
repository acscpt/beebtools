# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Resource strings for the ADFS format engine.

The ``RESOURCES`` mapping is a simple map to map of resource strings.  The primary
key is the consuming module name and then resource property names to their string 
values.
"""


RESOURCES = {
    "cli": {
        "attrib.access":
            "ADFS:\n"
            "  Absolute: LWR, LWR/r, LWRr, \"\" (no access)\n"
            "  Mutation: +L, -W, +L-W+R\n"
            "  Case: uppercase=owner, lowercase=public\n"
            "  Letters outside LWRE/wre (including D) are ignored with a warning.\n",
    },
}
