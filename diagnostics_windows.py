"""Windows-safe offline diagnostics entry point.

This runner intentionally uses the project's Python environment and avoids
NamedTemporaryFile because SQLite can fail to reopen an open Windows temp file.
"""

import json
import os
import sqlite3
import tempfile


def run():
    results = []

    # Import check: report the interpreter and the exact missing dependency.
    try:
        import streamlit  # noqa: F401
        import ultimate_bot  # noqa: F401
        import factory_runtime  # noqa: F401
        import db_architecture  # noqa: F401
        import db_runtime  # noqa: F401
        import runtime_bindings  # noqa: F401
        results.append({
            "name": "Imports",
            "status": "PASS",
            "detail": f"Core imports passed under {os.path.abspath(os.sys.executable)}",
        })
    except Exception as exc:
        results.append({
            "name": "Imports",
            "status": "FAIL",
            "detail": f"{type(exc).__name__}: {exc}",
            "python": os.path.abspath(os.sys.executable),
        })

    # Database check: use a temporary directory, not an open NamedTemporaryFile.
    try:
        from db_architecture import migrate_vault, make_run_id, create_run_record, update_run_record
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "diagnostic.db")
            conn = sqlite3.connect(db_path)
            migrate_vault(conn)
            row_id, run_id = create_run_record(conn, "Diagnostic story", "diagnostic", make_run_id())
            if not row_id or not run_id:
                raise AssertionError("run-record creation did not return row/run identity")
            update_run_record(conn, row_id, status="REJECTED", reported=1, rejected_reason="offline diagnostic")
            row = conn.execute("SELECT status FROM vault WHERE id = ?", (row_id,)).fetchone()
            conn.close()
        if not row or row[0] != "REJECTED":
            raise AssertionError("run-record lifecycle failed")
        results.append({
            "name": "Database",
            "status": "PASS",
            "detail": "SQLite migration + run-record create/update passed in a Windows-safe temporary directory",
        })
    except Exception as exc:
        results.append({
            "name": "Database",
            "status": "FAIL",
            "detail": f"{type(exc).__name__}: {exc}",
        })

    passed = sum(1 for item in results if item["status"] == "PASS")
    return {
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "all_passed": passed == len(results),
        "python": os.path.abspath(os.sys.executable),
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
