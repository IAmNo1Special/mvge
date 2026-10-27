import pytest

from mvge.core.sandbox import MvgeSandbox, SandboxTimeoutError


def test_validate_ast_allows_safe_code() -> None:
    s = MvgeSandbox()
    s.validate_ast("x = 1 + 2")


def test_validate_ast_forbids_import_os() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("import os")


def test_validate_ast_forbids_from_import() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("from os import path")


def test_validate_ast_refuses_forbidden_module_in_allowlist() -> None:
    """An allowlist may not name a forbidden module at all.

    Naming one used to let the import parse and fail only at every use, which
    made a permissive-looking configuration look valid until synthesised code
    hit it much later. One rule now covers both halves: the import is refused,
    and so is any attribute reached through the module.
    """
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("import os", allowed_modules={"os"})
    for code in (
        "import os\nos.system('echo x')",
        "import os\nleaked = os.environ['OPENROUTER_API_KEY']",
    ):
        with pytest.raises(ValueError, match="Forbidden"):
            s.validate_ast(code, allowed_modules={"os"})


def test_validate_ast_forbids_call_eval() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("eval('1')")


def test_execute_sync_simple() -> None:
    s = MvgeSandbox()
    result = s._execute_sync("x = 1 + 2", None)
    assert result["x"] == 3


def test_execute_sync_with_context() -> None:
    s = MvgeSandbox()
    result = s._execute_sync("y = x + 1", {"x": 5})
    assert result["y"] == 6


def test_execute_code_success() -> None:
    s = MvgeSandbox()
    result = s.execute_code("a = 10", timeout_seconds=10.0)
    assert result["a"] == 10


def test_execute_code_forbidden_raises() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.execute_code("import subprocess", timeout_seconds=1.0)


def test_execute_code_timeout() -> None:
    s = MvgeSandbox()
    with pytest.raises(SandboxTimeoutError):
        s.execute_code("while True: pass", timeout_seconds=0.3)


def test_execute_code_runtime_error() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="division by zero"):
        s.execute_code("x = 1 / 0", timeout_seconds=5.0)


def test_execute_code_filters_modules() -> None:
    s = MvgeSandbox()
    result = s.execute_code(
        "import math\ny = math.sqrt(16)", allowed_modules={"math"}, timeout_seconds=5.0
    )
    assert result["y"] == 4.0
    assert "math" not in result


def test_execute_sync_safe_import_forbidden() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden AST node"):
        s._execute_sync("import os", None)


def test_validate_ast_forbids_dunder_attribute() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="dunder"):
        s.validate_ast("x = ().__class__")


def test_validate_ast_forbids_dunder_subclass_traversal() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="dunder"):
        s.validate_ast("x = ().__class__.__base__.__subclasses__()")


def test_validate_ast_allowlist_rejects_unlisted_import() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="allowed_modules"):
        s.validate_ast("import socket", allowed_modules={"math"})


def test_validate_ast_allowlist_rejects_unlisted_from_import() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="allowed_modules"):
        s.validate_ast("from pathlib import Path", allowed_modules={"math"})


def test_validate_ast_allowlist_permits_listed_import() -> None:
    s = MvgeSandbox()
    s.validate_ast("import math", allowed_modules={"math"})


def test_execute_code_warns_without_allowlist() -> None:
    s = MvgeSandbox()
    with pytest.warns(UserWarning, match="best-effort"):
        s.execute_code("a = 1", timeout_seconds=10.0)


# Attribute-form targets. visit_Call used to match only bare ast.Name, so
# anything reached through an attribute - os.system, subprocess.run - sailed
# past the denylist even while the bare form was rejected.


def test_validate_ast_forbids_attribute_form_call() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("import os\nos.system('echo x')")


def test_validate_ast_forbids_attribute_form_call_without_import() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("os.system('echo x')")


def test_validate_ast_forbids_subprocess_attribute_form() -> None:
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("import subprocess\nsubprocess.run(['echo', 'x'])")


def test_validate_ast_forbids_allowlisted_module_attribute_form() -> None:
    """An allowlist entry must not hand back command execution.

    ``allowed_modules`` constrains which modules may be imported. Listing a
    forbidden module must not become the grant that makes ``os.system``
    reachable, which is how heal-my-goap's GOAP sandbox was defeated.
    """
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast(
            "import os\nos.system('echo x')",
            allowed_modules={"pathlib", "subprocess", "os", "urllib", "json", "re"},
        )


def test_validate_ast_forbids_environment_subscript_on_allowlisted_module() -> None:
    """Credential theft needs no call: ``os.environ[...]`` is a Subscript."""
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast(
            "import os\nleaked = os.environ['OPENROUTER_API_KEY']",
            allowed_modules={"os"},
        )


def test_execute_code_rejects_attribute_form_escape() -> None:
    """End-to-end: the escape must fail before the payload ever runs."""
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.execute_code(
            "import os\nos.system('echo x')",
            allowed_modules={"os", "subprocess"},
            timeout_seconds=5.0,
        )


def test_validate_ast_refuses_from_import_of_forbidden_module_in_allowlist() -> None:
    """The from-import form must agree with the plain import form."""
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("from os import path", allowed_modules={"os"})


def test_validate_ast_permits_listed_non_forbidden_module() -> None:
    """The allowlist still grants what it is allowed to grant."""
    s = MvgeSandbox()
    s.validate_ast("import json", allowed_modules={"json", "re"})


def test_validate_ast_hard_bans_sys_even_when_allowlisted() -> None:
    """sys defeats the restricted builtins; an allowlist must not re-grant it."""
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s.validate_ast("import sys", allowed_modules={"sys", "json"})


def test_execute_sync_refuses_forbidden_module_in_allowlist() -> None:
    """The runtime import hook must agree with AST validation.

    ``safe_import`` is the second seam. A rule enforced in only one of the two
    leaves a bypass that the other half of the fix already had to cover.
    """
    s = MvgeSandbox()
    with pytest.raises(ValueError, match="Forbidden"):
        s._execute_sync("import os", None, allowed_modules={"os", "json"})


def test_validate_ast_still_permits_allowed_non_forbidden_attribute_calls() -> None:
    """The fix must not blanket-ban attribute access."""
    s = MvgeSandbox()
    s.validate_ast(
        "import pathlib\np = pathlib.Path('/tmp/x')",
        allowed_modules={"pathlib"},
    )


def test_validate_ast_relative_from_import_without_module() -> None:
    s = MvgeSandbox()
    s.validate_ast("from . import sibling")


def test_worker_target_success_and_error() -> None:
    from unittest.mock import MagicMock

    from mvge.core.sandbox import _sandbox_process_target

    queue = MagicMock()
    _sandbox_process_target(
        "x = 42\nimport math\n", {}, queue, allowed_modules={"math"}
    )
    queue.put.assert_called_with(("success", {"x": 42}))

    queue.reset_mock()
    _sandbox_process_target("x = 1 / 0", {}, queue)
    args, _ = queue.put.call_args
    assert args[0][0] == "error"
    assert "division by zero" in args[0][1]


def test_execute_sync_allowed_module_success() -> None:
    s = MvgeSandbox()
    res = s._execute_sync(
        "import math\ny = math.floor(3.9)", {}, allowed_modules={"math"}
    )
    assert res["y"] == 3
