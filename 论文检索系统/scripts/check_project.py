"""对组员版最终项目执行本地数据校验与 A/C/回归测试，不联网、不改正式数据。"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
temp_root = (ROOT / ".verification/tmp").resolve()
assert temp_root.is_relative_to(ROOT)
temp_root.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(temp_root)


def main():
    sys.path.insert(0, str(ROOT / "paper-data/scripts"))
    from validate_data import validate_jsonl, validate_stats
    rows_by_file, errors = {}, []
    for name, lo, hi in [("papers_dev.jsonl", 100, 200), ("papers_final.jsonl", 500, 1000), ("papers.jsonl", 500, 1000)]:
        rows, found = validate_jsonl(ROOT / "paper-data/data" / name, lo, hi)
        rows_by_file[name] = rows
        errors.extend(found)
    _, found = validate_stats(ROOT / "paper-data", rows_by_file)
    errors.extend(found)
    if errors:
        print("DATA_VALIDATION_FAILED\n" + "\n".join(errors))
        return 1
    print(f"DATA_VALIDATION_OK: formal={len(rows_by_file['papers_final.jsonl'])}", flush=True)
    suite = unittest.TestSuite()
    for directory in [ROOT / "tests", ROOT / "paper-data/tests"]:
        suite.addTests(unittest.TestLoader().discover(str(directory), top_level_dir=str(directory)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
