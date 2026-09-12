"""Path predicates shared by the classifier and the symbol inspector.

These two decided "is this a test?" separately and drifted: the inspector was
corrected to match path segments while the classifier kept a bare substring
test, so a package named `latest` or `contest`, a directory `testdata`, or a
class `ProtestController` silently classified as a test -- and, since a test
outranks a plugin-detected role, swallowed a real @RestController with it.
"""
from pathlib import PurePosixPath

_TEST_SEGMENTS = {
    "test",
    "tests",
    "spec",
    "specs",
    "__tests__",
    "testing",
    "it",  # Maven failsafe integration-test source root: src/it/java
}

_TEST_FILE_SUFFIXES = (
    "_test.py",
    "_test.go",
    "test.java",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
    "_spec.rb",
)


def is_test_path(path: str) -> bool:
    """Whether a path belongs to a test tree, in any language's convention.

    Matches whole path segments, never substrings: `src/test/java/...` and
    `tests/test_x.py` are tests, `com/acme/latest/OrderController.java` is not.
    """
    if not path:
        return False
    parts = [part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts]
    if any(part in _TEST_SEGMENTS for part in parts):
        return True
    name = parts[-1] if parts else ""
    return name.startswith("test_") or name.endswith(_TEST_FILE_SUFFIXES)
