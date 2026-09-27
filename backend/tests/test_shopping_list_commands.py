"""The agent's `castle-chef shopping-list` commands, as it runs them in a job directory."""

from pathlib import Path

import pytest
from castle_chef_agent import shopping as agent_shopping
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.shopping import Aisle, IngredientLine, ListJob
from typer.testing import CliRunner, Result

from castle_chef import shopping
from castle_chef.cli import app
from castle_chef.shopping import ListEntry

runner = CliRunner()

LINES = [
    IngredientLine(id="1a", meal=1, text="1 onion", has_amount=True),
    IngredientLine(id="1b", meal=1, text="0.5 lemon", has_amount=True),
    IngredientLine(id="1c", meal=1, text="1 tsp kosher salt", has_amount=True),
    IngredientLine(id="2a", meal=2, text="1 cup onion, diced", has_amount=True),
    IngredientLine(id="2b", meal=2, text="1 lemon", has_amount=True),
    IngredientLine(id="2c", meal=2, text="8 oz ground beef", has_amount=True),
]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    """A job directory as the worker makes it for these lines."""
    write_context(tmp_path, ListJob(lines=LINES))
    return tmp_path


def run(job_dir: Path, *arguments: str) -> Result:
    return runner.invoke(
        app, ["shopping-list", *arguments], env={"CASTLE_CHEF_JOB_DIR": str(job_dir)}
    )


def test_an_item_is_added_with_the_lines_it_covers(job_dir: Path) -> None:
    result = run(
        job_dir, "add", "--name", "ground beef", "--quantity", "0.5", "--unit", "lb",
        "--aisle", "meat_seafood", "--line", "2c",
    )  # fmt: skip

    assert (result.exit_code, result.stdout) == (0, 'Added "ground beef": 0.5 lb, meat_seafood.\n')
    [item] = agent_shopping.load_draft(job_dir).items
    assert (item.name, item.quantity, item.unit, item.aisle, item.lines) == (
        "ground beef",
        0.5,
        "lb",
        Aisle.MEAT_SEAFOOD,
        ["2c"],
    )


def test_a_count_is_rounded_up_to_whole_ones(job_dir: Path) -> None:
    result = run(
        job_dir, "add", "--name", "lemons", "--quantity", "1.5", "--aisle", "produce",
        "--line", "1b", "--line", "2b",
    )  # fmt: skip

    assert result.stdout == 'Added "lemons": 2, produce.\n'
    assert agent_shopping.load_draft(job_dir).items[0].quantity == 2


@pytest.mark.parametrize(("given", "saved"), [("1.005", 2), ("2.0000000001", 2), ("0.004", 1)])
def test_any_part_of_a_count_is_rounded_up(job_dir: Path, given: str, saved: float) -> None:
    result = run(job_dir, "add", "--name", "lemons", "--quantity", given, "--aisle", "produce",
                 "--line", "1b")  # fmt: skip

    assert result.exit_code == 0, result.output
    assert agent_shopping.load_draft(job_dir).items[0].quantity == saved


@pytest.mark.parametrize("quantity", ["0.0000001", None])
def test_an_item_that_would_be_saved_invalid_is_refused(
    job_dir: Path, quantity: str | None
) -> None:
    name = "lemons" if quantity else "   "
    amount = ["--quantity", quantity] if quantity else []
    result = run(job_dir, "add", "--name", name, *amount, "--aisle", "produce", "--line", "1b")

    assert result.exit_code == 1
    assert result.stderr.startswith("Invalid item:")
    assert run(job_dir, "done").stderr.startswith("Not covered yet:")  # the list still loads


def test_the_worker_checks_the_lines_it_made_not_job_json(job_dir: Path) -> None:
    # The agent can rewrite job.json, e.g. down to the one line it covered.
    write_context(job_dir, ListJob(lines=LINES[5:]))
    run(job_dir, "add", "--name", "ground beef", "--quantity", "8", "--unit", "oz",
        "--aisle", "meat_seafood", "--line", "2c")  # fmt: skip
    assert run(job_dir, "done").exit_code == 0

    with pytest.raises(shopping.InvalidList, match=r"^Not covered yet: 1a \(1 onion\);"):
        shopping.finished_list(job_dir, ListJob(lines=LINES))


def test_an_amount_in_a_unit_keeps_two_decimals(job_dir: Path) -> None:
    result = run(
        job_dir, "add", "--name", "ground beef", "--quantity", "0.3333", "--unit", "lb",
        "--aisle", "meat_seafood", "--line", "2c",
    )  # fmt: skip

    assert result.stdout == 'Added "ground beef": 0.33 lb, meat_seafood.\n'


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        (
            ["--name", "Kosher Salt", "--line", "1c"],
            '"Kosher Salt" is the staple salt: add it with '
            "`castle-chef shopping-list staple 'salt'` instead.",
        ),
        (["--name", "onions", "--line", "3a"], "There is no line 3a."),
        (
            ["--name", "onions", "--quantity", "1", "--line", "1a", "--line", "1a"],
            "A line is given twice.",
        ),
        (
            ["--name", "onions", "--line", "1a", "--line", "2a"],
            '"onions" needs a quantity: 1a, 2a give an amount.',
        ),
        (["--name", "beef", "--quantity", "inf", "--unit", "lb", "--line", "2c"], "Invalid item:"),
        (["--name", "onions", "--unit", "cup", "--line", "1a"], "--unit needs --quantity."),
        (
            ["--name", "beef", "--quantity", "0.001", "--unit", "lb", "--line", "2c"],
            "0.001 lb rounds to 0: use a smaller unit.",
        ),
        (
            ["--name", "Sea-salt.", "--line", "1c"],
            '"Sea-salt." is the staple salt: add it with '
            "`castle-chef shopping-list staple 'salt'` instead.",
        ),
    ],
    ids=[
        "staple",
        "unknown line",
        "line twice",
        "no quantity",
        "infinite",
        "unit alone",
        "rounds to 0",
        "staple spelled",
    ],
)
def test_an_item_that_breaks_a_rule_is_refused_with_why(
    job_dir: Path, arguments: list[str], problem: str
) -> None:
    result = run(job_dir, "add", "--aisle", "pantry", *arguments)

    assert result.exit_code == 1
    assert result.stderr.startswith(problem)
    assert agent_shopping.load_draft(job_dir).items == []


def test_a_line_already_covered_is_refused_naming_what_covers_it(job_dir: Path) -> None:
    run(job_dir, "add", "--name", "yellow onions", "--quantity", "2", "--aisle", "produce",
        "--line", "1a", "--line", "2a")  # fmt: skip
    run(job_dir, "staple", "salt", "--line", "1c")

    onion = run(job_dir, "add", "--name", "red onion", "--quantity", "1", "--aisle", "produce",
                "--line", "2a")  # fmt: skip
    pepper = run(job_dir, "staple", "pepper", "--line", "1c")

    assert onion.stderr == 'Line 2a is already in "yellow onions".\n'
    assert pepper.stderr == "Line 1c is already in the staple salt.\n"


def test_an_item_or_staple_is_changed_by_removing_it_first(job_dir: Path) -> None:
    run(job_dir, "add", "--name", "Lemons", "--quantity", "1", "--aisle", "produce",
        "--line", "1b")  # fmt: skip
    run(job_dir, "staple", "salt", "--line", "1c")

    again = run(job_dir, "add", "--name", "lemons", "--quantity", "2", "--aisle", "produce",
                "--line", "1b", "--line", "2b")  # fmt: skip
    salt_again = run(job_dir, "staple", "salt", "--line", "1c")
    removed = [run(job_dir, "remove", "lemons"), run(job_dir, "remove", "salt")]
    replaced = run(job_dir, "add", "--name", "lemons", "--quantity", "2", "--aisle", "produce",
                   "--line", "1b", "--line", "2b")  # fmt: skip

    assert again.stderr == (
        '"lemons" is already on the list. To change it, remove it '
        "(`castle-chef shopping-list remove 'lemons'`) and add it again.\n"
    )
    assert salt_again.stderr.startswith("salt is already added.")
    assert [result.exit_code for result in [*removed, replaced]] == [0, 0, 0]
    draft = agent_shopping.load_draft(job_dir)
    assert [(item.name, item.lines) for item in draft.items] == [("lemons", ["1b", "2b"])]
    assert draft.staples == []
    assert run(job_dir, "remove", "limes").stderr == '"limes" is not on the list.\n'


def test_a_staple_can_only_be_one_of_the_staples(job_dir: Path) -> None:
    result = run(job_dir, "staple", "seasoned salt", "--line", "1c")

    assert result.exit_code == 2
    assert agent_shopping.load_draft(job_dir).staples == []


def test_done_says_what_is_not_covered_until_every_line_is(job_dir: Path) -> None:
    run(job_dir, "staple", "salt", "--line", "1c")
    unfinished = run(job_dir, "done")
    run(job_dir, "add", "--name", "yellow onions", "--quantity", "2", "--aisle", "produce",
        "--line", "1a", "--line", "2a")  # fmt: skip
    run(job_dir, "add", "--name", "lemons", "--quantity", "1.5", "--aisle", "produce",
        "--line", "1b", "--line", "2b")  # fmt: skip
    almost = run(job_dir, "done")
    run(job_dir, "add", "--name", "ground beef", "--quantity", "8", "--unit", "oz",
        "--aisle", "meat_seafood", "--line", "2c")  # fmt: skip
    complete = run(job_dir, "done")

    assert (unfinished.exit_code, unfinished.stderr) == (
        1,
        "Not covered yet: 1a (1 onion); 1b (0.5 lemon); 2a (1 cup onion, diced); "
        "2b (1 lemon); 2c (8 oz ground beef). The list has nothing to buy.\n",
    )
    assert almost.stderr == "Not covered yet: 2c (8 oz ground beef).\n"
    assert (complete.exit_code, complete.stdout) == (
        0,
        "Complete: 3 items and 1 staples cover all 6 lines.\n",
    )
    # What the worker saves: each item and staple with the meals its lines are from.
    assert shopping.finished_list(job_dir, ListJob(lines=LINES)) == [
        ListEntry(name="yellow onions", quantity=2, aisle=Aisle.PRODUCE, meals=[1, 2]),
        ListEntry(name="lemons", quantity=2, aisle=Aisle.PRODUCE, meals=[1, 2]),
        ListEntry(name="ground beef", quantity=8, unit="oz", aisle=Aisle.MEAT_SEAFOOD, meals=[2]),
        ListEntry(name="salt", aisle=Aisle.PANTRY, meals=[1], on_hand=True),
    ]


def test_the_worker_refuses_a_list_that_is_not_done(job_dir: Path) -> None:
    run(job_dir, "add", "--name", "ground beef", "--quantity", "8", "--unit", "oz",
        "--aisle", "meat_seafood", "--line", "2c")  # fmt: skip

    with pytest.raises(shopping.InvalidList, match=r"^Not covered yet: 1a \(1 onion\);"):
        shopping.finished_list(job_dir, ListJob(lines=LINES))


@pytest.mark.parametrize("command", ["add", "staple", "remove", "done"])
def test_help_shows_only_what_the_agent_gives(job_dir: Path, command: str) -> None:
    # The worker sets the job directory; the help mustn't send the agent looking for it.
    help_text = run(job_dir, command, "--help").stdout

    assert "job" not in help_text.lower()


def test_an_item_needs_no_quantity_when_its_lines_give_none(tmp_path: Path) -> None:
    write_context(
        tmp_path,
        ListJob(
            lines=[IngredientLine(id="1a", meal=1, text="hot sauce, to taste", has_amount=False)]
        ),
    )

    result = run(tmp_path, "add", "--name", "hot sauce", "--aisle", "pantry", "--line", "1a")

    assert (result.exit_code, result.stdout) == (0, 'Added "hot sauce": no amount, pantry.\n')
    assert run(tmp_path, "done").exit_code == 0
