"""Migrations must stay backward-compatible (expand/contract).

A release migrates Neon `main` while the previous release's app and worker keep
running against it, and TestFlight builds already on phones can't be rolled
back. So `upgrade()`, and the helpers it calls, may add, but may not drop or
rename tables or columns, unless the statement is marked on the line above with
`# contract: <why the previous release no longer uses it>`.
Replacing RLS policies, constraints, and indexes is allowed.
"""

import ast
import re
from pathlib import Path

import pytest

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
DESTRUCTIVE_OPS = {"drop_table", "drop_column", "rename_table"}
DESTRUCTIVE_SQL = re.compile(r"\bDROP\s+(TABLE|COLUMN)\b|\bRENAME\b", re.IGNORECASE)


def _sql_text(node: ast.expr) -> str:
    """The literal text of a SQL argument: a string, f-string (placeholders dropped),
    or `sa.text(...)` of one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_sql_text(value) for value in node.values)
    if isinstance(node, ast.Call) and node.args:
        return _sql_text(node.args[0])
    return ""


def _is_destructive(call: ast.Call) -> bool:
    # Any receiver: `op`, or `batch_op` inside `op.batch_alter_table(...)`.
    if not isinstance(call.func, ast.Attribute):
        return False
    name = call.func.attr
    if name in DESTRUCTIVE_OPS:
        return True
    if name == "alter_column":
        return any(keyword.arg == "new_column_name" for keyword in call.keywords)
    if name == "execute" and call.args:
        return bool(DESTRUCTIVE_SQL.search(_sql_text(call.args[0])))
    return False


def _upgrade_functions(module: ast.Module) -> list[ast.FunctionDef]:
    """`upgrade()` and the module's functions it calls, directly or through each other."""
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    reached: dict[str, ast.FunctionDef] = {}
    calls = ["upgrade"]
    while calls:
        name = calls.pop()
        if name in reached or name not in functions:
            continue
        reached[name] = functions[name]
        calls += [
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
    return list(reached.values())


def unmarked_destructive_statements(source: str) -> list[int]:
    """Line numbers of destructive statements without a `# contract:` mark, in
    `upgrade()` and the helpers it calls."""
    lines = source.splitlines()
    return sorted(
        node.lineno
        for function in _upgrade_functions(ast.parse(source))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _is_destructive(node)
        and not (node.lineno >= 2 and lines[node.lineno - 2].strip().startswith("# contract:"))
    )


@pytest.mark.parametrize("path", sorted(VERSIONS_DIR.glob("*.py")), ids=lambda path: path.name)
def test_migration_upgrade_is_backward_compatible(path: Path) -> None:
    lines = unmarked_destructive_statements(path.read_text())
    assert not lines, (
        f"{path.name} drops or renames a table or column on line(s) {lines}. The previous "
        "release still runs against the migrated database. Stop using it in one release, drop "
        "it in a later one, and mark the statement with `# contract: <why>` on the line above."
    )


@pytest.mark.parametrize(
    "statement",
    [
        'op.drop_table("meal")',
        'op.drop_column("meal", "servings")',
        'op.rename_table("meal", "dish")',
        'op.alter_column("meal", "name", new_column_name="title")',
        'op.execute("ALTER TABLE meal DROP COLUMN servings")',
        'op.execute(f"DROP TABLE {table}")',
        'op.execute("alter table meal rename to dish")',
        'op.execute(sa.text("DROP TABLE meal"))',
        'batch_op.drop_column("servings")',
    ],
)
def test_guard_flags_dropping_or_renaming(statement: str) -> None:
    source = f"def upgrade() -> None:\n    {statement}\n\n\ndef downgrade() -> None:\n    pass\n"
    assert unmarked_destructive_statements(source) == [2]


@pytest.mark.parametrize(
    "statement",
    [
        'op.execute("DROP POLICY request_meals ON job")',
        'op.drop_constraint("job_params", "job", type_="check")',
        'op.drop_index("ix_job_status", table_name="job")',
        'op.add_column("meal", sa.Column("notes", sa.Text()))',
    ],
)
def test_guard_allows_additive_and_replaceable_changes(statement: str) -> None:
    source = f"def upgrade() -> None:\n    {statement}\n"
    assert unmarked_destructive_statements(source) == []


def test_guard_accepts_a_marked_contract_step_and_ignores_downgrade() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    # contract: r41 stopped reading meal.servings\n"
        '    op.drop_column("meal", "servings")\n'
        "\n"
        "def downgrade() -> None:\n"
        '    op.drop_table("meal")\n'
    )
    assert unmarked_destructive_statements(source) == []


def test_guard_looks_inside_the_helpers_upgrade_calls_and_only_those() -> None:
    source = (
        "def _retype() -> None:\n"
        '    op.execute("ALTER TYPE order_step RENAME TO order_step_old")\n'
        "\n"
        "def _rebuild() -> None:\n"
        "    _retype()\n"
        "\n"
        "def _drop_lists() -> None:\n"
        '    op.drop_table("shopping_list_item")\n'
        "\n"
        "def upgrade() -> None:\n"
        "    _rebuild()\n"
        "\n"
        "def downgrade() -> None:\n"
        "    _drop_lists()\n"
    )
    assert unmarked_destructive_statements(source) == [2]
