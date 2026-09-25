import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner, Result

from castle_chef.cli import app

runner = CliRunner()

MEAL_JSON = {
    "name": "Creamy Tuscan Chicken",
    "description": "One-pan chicken in a garlicky cream sauce.",
    "servings": 6,
    "active_time_minutes": 25,
    "total_time_minutes": 40,
    "ingredients": [
        {"name": "chicken", "quantity": 3, "unit": "lb"},
        {"name": "garlic", "quantity": 4, "unit": "clove", "preparation": "minced"},
        {"name": "heavy cream", "quantity": 2, "unit": "cup"},
    ],
    "steps": ["Season chicken.", "Sear chicken.", "Simmer in cream."],
}


def add(job_dir: Path, meal: dict[str, object]) -> Result:
    return runner.invoke(
        app, ["meal", "add"], input=json.dumps(meal), env={"CASTLE_CHEF_JOB_DIR": str(job_dir)}
    )


def test_meal_add_saves_each_meal_for_the_worker(tmp_path: Path) -> None:
    first = add(tmp_path, MEAL_JSON)
    second = add(tmp_path, MEAL_JSON | {"name": "Tacos"})

    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    saved = sorted((tmp_path / "meals").glob("*.json"))
    assert [json.loads(path.read_text())["name"] for path in saved] == [
        "Creamy Tuscan Chicken",
        "Tacos",
    ]


def test_meal_add_rejects_meal_without_servings(tmp_path: Path) -> None:
    meal = {key: value for key, value in MEAL_JSON.items() if key != "servings"}

    result = add(tmp_path, meal)

    assert result.exit_code == 1
    assert "servings" in result.stderr
    assert not (tmp_path / "meals").exists()


def test_meal_add_rejects_unknown_fields(tmp_path: Path) -> None:
    result = add(tmp_path, MEAL_JSON | {"calories": 500})

    assert result.exit_code == 1
    assert "calories" in result.stderr


def test_meal_add_requires_an_existing_job_directory(tmp_path: Path) -> None:
    result = add(tmp_path / "missing", MEAL_JSON)

    assert result.exit_code != 0
    assert not (tmp_path / "missing").exists()


def test_parallel_meal_adds_each_save_their_own_meal(tmp_path: Path) -> None:
    # The agent can run several `meal add` commands at once.
    names = [f"Meal {index}" for index in range(8)]
    processes = [
        subprocess.Popen(
            [str(Path(sys.executable).parent / "castle-chef"), "meal", "add"],
            stdin=subprocess.PIPE,
            env=os.environ | {"CASTLE_CHEF_JOB_DIR": str(tmp_path)},
            text=True,
        )
        for _ in names
    ]
    for process, name in zip(processes, names, strict=True):
        process.stdin.write(json.dumps(MEAL_JSON | {"name": name}))
        process.stdin.close()
    assert [process.wait() for process in processes] == [0] * len(names)

    saved = [json.loads(path.read_text())["name"] for path in (tmp_path / "meals").glob("*.json")]
    assert sorted(saved) == names
