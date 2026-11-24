"""Loaded in every verification Python child before application dependencies."""

import os
import sys

if os.environ.get("M101_MANIFEST"):
    try:
        from productflow_backend.infrastructure.verification import (
            install_database_guard,
            install_network_guard,
        )

        install_network_guard()
        install_database_guard()
        from productflow_backend.infrastructure.verification_process import register_child

        register_child()
    except Exception:
        # Python otherwise only warns on sitecustomize failure and continues.
        sys.stderr.write("M1-01 isolation bootstrap failed; process stopped.\n")
        os._exit(78)
