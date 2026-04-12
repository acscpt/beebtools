# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Cross-cutting definitions shared by every layer.

This module sits beside the layer hierarchy, not inside it. Any module
in the package may import from here, but this module must not import
from any other beebtools module. It depends only on the Python standard
library.

    Contracts      tokens, boot, entry, inf, codec
    Formats        dfs, adfs
    BASIC          pretty, basic             shared.py
    Dispatch       image                     (cross-cutting,
    Orchestration  disc                       importable by all,
    CLI            cli                        imports from none)
    Public API     __init__
"""


class BeebToolsWarning(UserWarning):
    """Warning category for all beebtools diagnostics.

    Allows callers to filter on beebtools warnings specifically
    when using warnings.catch_warnings() or warnings.filterwarnings().
    """
