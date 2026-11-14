"""Recipe edits through HTTP, actual Postgres transactions, worker and MCP tools."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from bite_planner_agent import meal_edit
from bite_planner_agent.jobfiles import write_context
from bite_planner_agent.meals import StepInput
from bite_planner_agent.server import Tools, meal_edit_server
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text
from sqlmodel import Session, select

from bite_planner import api, meal_edits, orders, push, shopping, worker
from bite_planner.db import get_engine
from bite_planner.jobs import STALE_ERROR, SUPERSEDED_ERROR, claim_job, fail_job
from bite_planner.models import EditDecision, Job, JobKind, JobStatus, Meal
from tests.test_api import (
    CASTLE,
    DAD,
    MOM,
    NEIGHBOR,
    RecordingDispatcher,
    as_user,
    change_item,
    close_flow,
    detail,
    finish_list,
    get_order,
    no_content,
    ok,
    phone,
    pick,
    ready_list,
    recipe,
    run_at_once,
    set_servings,
    with_suggestions,
)
from tests.test_api import client as client
from tests.test_api import dispatcher as dispatcher
from tests.test_api import households as households
from tests.test_failures import TASK, task_stopped
from tests.test_failures import events as events
from tests.test_products_tools import call
from tests.test_worker import use_fake_agent


def edit_path(order_id: str, meal_id: str) -> str:
    return f"/v1/grocery-orders/{order_id}/meals/{meal_id}/edits"


def send(
    client: TestClient,
    order_id: str,
    meal_id: str,
    job_id: UUID | None = None,
    feedback: str = "Use chicken breasts instead.",
    replaces: UUID | None = None,
    user: UUID = MOM,
) -> Response:
    return client.post(
        edit_path(order_id, meal_id),
        headers=as_user(user),
        json={
            "job_id": str(job_id or uuid4()),
            "feedback": feedback,
            "replaces": str(replaces) if replaces else None,
        },
    )


def read(client: TestClient, order_id: str) -> api.GroceryOrderOut:
    return api.GroceryOrderOut.model_validate(get_order(client, order_id))


def decision(
    client: TestClient,
    order_id: str,
    meal_id: str,
    job_id: UUID,
    action: str,
    base: int = 0,
    user: UUID = MOM,
) -> Response:
    return client.post(
        f"{edit_path(order_id, meal_id)}/{job_id}/{action}",
        headers=as_user(user),
        json={"base_revision": base} if action == "accept" else None,
    )


def proposed(client: TestClient, order_id: str, meal_id: str) -> UUID:
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, job_id)
    assert claimed is not None
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Chicken tacos").model_dump())
    assert worker._save(claimed, proposal, {}) == (True, None)
    return job_id


def list_start(client: TestClient, order_id: str, job_id: UUID | None = None) -> Response:
    return client.post(
        f"/v1/grocery-orders/{order_id}/list",
        headers=as_user(MOM),
        json={"job_id": str(job_id or uuid4())},
    )


def test_proposal_only_changes_recipe_on_exact_accept(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    original = client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)).json()
    job_id = proposed(client, order_id, meal_id)
    change = read(client, order_id).recipe_changes[0]
    assert (change.state, change.blocks, change.base_revision) == ("ready", True, 0)
    assert client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)).json() == original
    detail(decision(client, order_id, meal_id, job_id, "accept", 99), 409)
    accepted = api.GroceryOrderOut.model_validate(
        ok(decision(client, order_id, meal_id, job_id, "accept"))
    )
    assert accepted.recipe_changes == []
    assert accepted.picks[0].servings == 4
    stored = api.MealOut.model_validate(
        ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))
    )
    assert (stored.revision, stored.name, stored.servings, stored.ingredient_count) == (
        1,
        "Chicken tacos",
        2,
        2,
    )
    assert stored.steps[0].ingredients == [1]
    assert stored.steps[1].timer_minutes == 10
    assert stored.picture is not None
    with Session(get_engine()) as session:
        meal = session.get_one(Meal, UUID(meal_id))
        assert meal.job_id != job_id  # original generating batch/ownership remains
        job = session.get_one(Job, job_id)
        assert job.decision == EditDecision.ACCEPTED
        assert job.decided_at is not None and job.decided_at.utcoffset() == timedelta(0)
    # Idempotency precedes the now changed revision and never increments it again.
    ok(decision(client, order_id, meal_id, job_id, "accept", 0))
    detail(decision(client, order_id, meal_id, job_id, "reject"), 409)
    assert read(client, order_id).jobs[0].meals[0].revision == 1
    next_id = uuid4()
    ok(send(client, order_id, meal_id, next_id), 201)
    assert read(client, order_id).recipe_changes[0].base_revision == 1


def test_payload_retry_and_new_attempt_are_distinct(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id, [meal_id, other] = with_suggestions(client)
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    count = len(dispatcher.dispatched)
    ok(send(client, order_id, meal_id, job_id))
    assert len(dispatcher.dispatched) == count
    for changed in [
        send(client, order_id, other, job_id),
        send(client, order_id, meal_id, job_id, feedback="Other change"),
        send(client, order_id, meal_id, job_id, replaces=uuid4()),
    ]:
        detail(changed, 409)
    detail(send(client, order_id, meal_id), 409)
    with Session(get_engine()) as session:
        fail_job(session, job_id, "Agent stopped.")
    ok(send(client, order_id, meal_id, job_id))
    assert read(client, order_id).recipe_changes[0].state == "failed"
    new_id = uuid4()
    ok(send(client, order_id, meal_id, new_id, replaces=job_id), 201)
    # Lost acknowledgement of replacement must not decide the new attempt twice.
    ok(send(client, order_id, meal_id, new_id, replaces=job_id))
    assert dispatcher.dispatched.count(new_id) == 1
    with Session(get_engine()) as session:
        assert session.get_one(Job, job_id).decision == EditDecision.REJECTED
    detail(decision(client, order_id, meal_id, job_id, "accept"), 409)
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    assert read(client, order_id).recipe_changes[0].job_id == new_id


def test_dispatch_failure_blocks_until_acknowledged_or_retried(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    dispatcher.fail = True
    job_id = uuid4()
    detail(send(client, order_id, meal_id, job_id), 503)
    replay = api.GroceryOrderOut.model_validate(ok(send(client, order_id, meal_id, job_id)))
    assert replay.recipe_changes[0].state == "failed"
    blocked = list_start(client, order_id)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "recipe_changes_pending"
    assert blocked.json()["detail"]["meals"][0]["name"] == "Tacos"
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    dispatcher.fail = False
    ok(list_start(client, order_id), 201)


@pytest.mark.parametrize("status", ["pending", "running"])
def test_stale_edit_requires_choice_and_late_save_cannot_revive(
    client: TestClient, status: str
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        if status == "running":
            claimed = claim_job(session, job_id)
        else:
            claimed = session.get_one(Job, job_id)
        assert claimed is not None
        session.expunge(claimed)
        session.execute(
            text(
                "UPDATE job SET created_at = created_at - interval '3 hours', "
                "started_at = started_at - interval '3 hours' WHERE id=:id"
            ),
            {"id": job_id},
        )
        session.commit()
    assert claimed is not None
    assert read(client, order_id).recipe_changes[0].state == "failed"
    assert list_start(client, order_id).status_code == 409
    # Keep immediately on an overdue projection normalizes status before recording rejection.
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump())
    assert worker._save(claimed, proposal, {}) == (False, None)
    with Session(get_engine()) as session:
        job = session.get_one(Job, job_id)
        assert (job.status, job.decision, job.proposal) == (
            JobStatus.FAILED,
            EditDecision.REJECTED,
            None,
        )
        assert session.get_one(Meal, UUID(meal_id)).revision == 0


def test_overdue_save_without_prior_normalization_persists_failure(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, job_id)
        assert claimed is not None
        session.expunge(claimed)
        session.execute(
            text("UPDATE job SET started_at = started_at - interval '3 hours' WHERE id=:id"),
            {"id": job_id},
        )
        session.commit()
    assert claimed is not None
    assert worker._save(
        claimed, meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump()), {}
    ) == (False, None)
    with Session(get_engine()) as session:
        job = session.get_one(Job, job_id)
        assert job.status == JobStatus.FAILED and job.proposal is None


def test_running_decisions_are_conflicts_and_replace_is_causal(client: TestClient) -> None:
    order_id, [meal_id, other] = with_suggestions(client)
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    for action in ("accept", "reject"):
        detail(decision(client, order_id, meal_id, job_id, action), 409)
    detail(send(client, order_id, other, replaces=job_id), 404)
    detail(send(client, order_id, meal_id, replaces=uuid4()), 404)
    with Session(get_engine()) as session:
        fail_job(session, job_id, "Failed.")
    detail(send(client, order_id, meal_id), 409)
    detail(decision(client, order_id, meal_id, job_id, "accept"), 409)
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    detail(send(client, order_id, meal_id, replaces=job_id), 409)


def test_selected_vs_unselected_remove_readd_and_not_for_us(client: TestClient) -> None:
    order_id, [meal_id, other] = with_suggestions(client)
    job_id = proposed(client, order_id, meal_id)
    assert not read(client, order_id).recipe_changes[0].blocks
    ok(pick(client, order_id, meal_id))
    assert read(client, order_id).recipe_changes[0].blocks
    assert list_start(client, order_id).status_code == 409
    ok(client.delete(f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=as_user(MOM)))
    assert not read(client, order_id).recipe_changes[0].blocks
    ok(pick(client, order_id, meal_id))
    assert read(client, order_id).picks[0].servings == 4
    assert read(client, order_id).recipe_changes[0].state == "ready"
    no_content(
        client.put(
            f"/v1/meals/{meal_id}/rating", headers=as_user(MOM), json={"rating": "not_for_us"}
        )
    )
    assert not read(client, order_id).recipe_changes[0].blocks
    detail(send(client, order_id, meal_id), 409)
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    ok(pick(client, order_id, other))
    ok(list_start(client, order_id), 201)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("stale_source", [False, True])
def test_recipe_changes_after_shopping_preserve_work_until_selected_accept(
    client: TestClient, selected: bool, stale_source: bool
) -> None:
    order_id, [meal_id, other] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    ready_list(client, order_id)
    before = read(client, order_id).shopping_list
    assert before is not None
    item = next(i for i in before.items if not i.on_hand)
    ok(change_item(client, order_id, str(item.id), {"count": 3}))
    if stale_source:
        ok(set_servings(client, order_id, meal_id, 6))
    before = read(client, order_id).shopping_list
    target = meal_id if selected else other
    job_id = proposed(client, order_id, target)
    assert read(client, order_id).recipes_editable
    assert read(client, order_id).shopping_list == before
    ok(decision(client, order_id, target, job_id, "reject"))
    assert read(client, order_id).shopping_list == before
    # Failed and intentionally retried proposals also preserve accepted work.
    failed_id = uuid4()
    ok(send(client, order_id, target, failed_id), 201)
    with Session(get_engine()) as session:
        fail_job(session, failed_id, "Fake provider refused.")
    assert read(client, order_id).shopping_list == before
    retry_id = uuid4()
    ok(send(client, order_id, target, retry_id, replaces=failed_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, retry_id)
    assert claimed is not None
    assert worker._save(
        claimed, meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump()), {}
    ) == (True, None)
    ok(decision(client, order_id, target, retry_id, "accept"))
    after = read(client, order_id).shopping_list
    assert after is not None and before is not None
    assert after.current == (not selected and not stale_source)
    assert after.items == before.items and after.job.id == before.job.id
    # A decided retry cannot touch subsequently authorized work.
    ok(decision(client, order_id, target, retry_id, "accept"))
    assert read(client, order_id).shopping_list == after


@pytest.mark.parametrize("stage", ["pending", "running", "shop"])
@pytest.mark.parametrize("selected", [False, True])
def test_accept_supersedes_only_selected_active_work_atomically(
    client: TestClient, stage: str, selected: bool
) -> None:
    order_id, [meal_id, other] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
    if stage == "shop":
        finish_list(str(build.id), shop=False)
        active = api.JobOut.model_validate(
            ok(
                client.post(
                    f"/v1/grocery-orders/{order_id}/list/shop",
                    headers=as_user(MOM),
                    json={"job_id": str(uuid4())},
                ),
                201,
            )
        )
    else:
        active = build
    with Session(get_engine()) as session:
        claimed = claim_job(session, active.id) if stage != "pending" else None
    late_result: worker.JobResult | None = None
    if claimed is not None:
        if stage == "shop":
            late_result = worker.Products(build_id=build.id, items={}, choices={})
        else:
            from tests.test_prepared_recipes import proportional_snapshot

            assert claimed.list_source is not None
            with Session(get_engine()) as session:
                prepared = proportional_snapshot(
                    shopping.recipe_meals(session, claimed.list_source)
                )
            late_result = worker.ListItems(items=[], source=claimed.list_source, prepared=prepared)
    target = meal_id if selected else other
    edit_id = proposed(client, order_id, target)
    ok(decision(client, order_id, target, edit_id, "accept"))
    after = read(client, order_id)
    job = next(j for j in after.jobs if j.id == active.id)
    assert job.status == ("failed" if selected else "pending" if stage == "pending" else "running")
    if selected:
        assert job.error == SUPERSEDED_ERROR
        assert after.shopping_list is not None and not after.shopping_list.current
        if claimed is not None and late_result is not None:
            assert worker._save(claimed, late_result, {}) == (False, None)
    # Accept replay does not supersede a NEW build for the accepted revision.
    if selected:
        new_build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
        ok(decision(client, order_id, target, edit_id, "accept"))
        assert (
            next(j for j in read(client, order_id).jobs if j.id == new_build.id).status == "pending"
        )


def test_proposal_during_build_preserves_original_automatic_shop_pipeline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bite_planner_agent.products import ItemChoice, ProductCount
    from bite_planner_agent.shopping import Aisle

    from bite_planner.models import ShoppingListItem
    from tests.test_api import ONIONS
    from tests.test_prepared_recipes import proportional_snapshot

    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
    processed: list[JobKind] = []

    def process(job: Job) -> worker.JobResult:
        processed.append(job.kind)
        if job.kind == JobKind.BUILD_LIST:
            # A new selected proposal does not alter the already-authorized inputs.
            ok(send(client, order_id, meal_id), 201)
            with Session(get_engine()) as session:
                assert job.list_source is not None
                prepared = proportional_snapshot(shopping.recipe_meals(session, job.list_source))
            return worker.ListItems(
                items=[
                    shopping.ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1])
                ],
                source=job.list_source,
                prepared=prepared,
            )
        assert job.kind == JobKind.SHOP and job.build_id == build.id
        with Session(get_engine()) as session:
            [item] = session.exec(
                select(ShoppingListItem).where(ShoppingListItem.job_id == build.id)
            ).all()
            return worker.Products(
                build_id=build.id,
                items={1: item.id},
                choices={
                    1: ItemChoice(
                        item=1, chosen=ProductCount(product=ONIONS, count=1), alternatives=[]
                    )
                },
            )

    monkeypatch.setattr(worker, "process_job", process)
    worker.run_job(build.id)
    assert processed == [JobKind.BUILD_LIST, JobKind.SHOP]
    result = read(client, order_id)
    assert result.shopping_list is not None and result.shopping_list.current
    assert result.shopping_list.shop is not None and result.shopping_list.shop.status == "succeeded"
    assert result.shopping_list.items[0].product is not None
    for path in ("list", "list/shop", "quote"):
        response = client.post(
            f"/v1/grocery-orders/{order_id}/{path}",
            headers=as_user(MOM),
            json={"job_id": str(build.id)},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "recipe_changes_pending"


def test_guards_precede_shopping_retry_and_quote_checks(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    job_id = proposed(client, order_id, meal_id)
    for route in ("list", "list/shop", "quote"):
        # Even an existing job id cannot get past the canonical blocker predicate.
        response = client.post(
            f"/v1/grocery-orders/{order_id}/{route}",
            headers=as_user(MOM),
            json={"job_id": str(job_id)},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "recipe_changes_pending"
    tomorrow = (datetime.now(UTC) + timedelta(days=3)).date()
    response = client.post(
        f"/v1/grocery-orders/{order_id}/place",
        headers=as_user(MOM),
        json={
            "delivery_date": str(tomorrow),
            "delivery_by": "18:00",
            "next_delivery_date": str(tomorrow + timedelta(days=2)),
            "quote_id": str(uuid4()),
        },
    )
    assert (
        response.status_code == 409
        and response.json()["detail"]["code"] == "recipe_changes_pending"
    )


def test_actual_concurrent_submissions_and_build_are_serialized(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    responses = run_at_once(
        [lambda: send(client, order_id, meal_id), lambda: list_start(client, order_id)]
    )
    statuses = sorted(r.status_code for r in responses)
    # If the build wins the lock, its authorized operation survives the later proposal.
    assert statuses in ([201, 201], [201, 409])
    with Session(get_engine()) as session:
        jobs = list(session.exec(select(Job).where(Job.grocery_order_id == UUID(order_id))).all())
        assert sum(j.kind in (JobKind.EDIT_MEAL, JobKind.BUILD_LIST) for j in jobs) == (
            2 if statuses == [201, 201] else 1
        )


def test_two_edit_submissions_leave_one_undecided(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    responses = run_at_once(
        [lambda: send(client, order_id, meal_id), lambda: send(client, order_id, meal_id)]
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert len(read(client, order_id).recipe_changes) == 1


def test_household_and_flow_ownership_and_takeover(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    job_id = proposed(client, order_id, meal_id)
    detail(send(client, order_id, meal_id, user=NEIGHBOR), 404)
    detail(decision(client, order_id, meal_id, job_id, "accept", user=NEIGHBOR), 404)
    detail(send(client, order_id, meal_id, user=DAD), 423)
    displaced = client.post(
        edit_path(order_id, meal_id),
        headers=as_user(MOM, session=uuid4()),
        json={"job_id": str(uuid4()), "feedback": "Change it"},
    )
    detail(displaced, 423)
    close_flow(client, order_id)
    ok(
        client.post(
            f"/v1/grocery-orders/{order_id}/lock", headers=as_user(DAD), json={"step": "servings"}
        )
    )
    detail(send(client, order_id, meal_id, job_id, user=DAD), 409)
    ok(decision(client, order_id, meal_id, job_id, "accept", user=DAD))


@pytest.mark.parametrize("count", [0, 1])
def test_actual_worker_tools_produce_failure_or_review_never_direct_save(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    ready_list(client, order_id)
    before = read(client, order_id).shopping_list
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    use_fake_agent(monkeypatch, meals=count)
    notified: list[UUID] = []
    monkeypatch.setattr(push, "notify", lambda user, notification: notified.append(user))
    worker.run_job(job_id)
    change = read(client, order_id).recipe_changes[0]
    assert change.state == ("ready" if count else "failed")
    assert read(client, order_id).shopping_list == before
    assert not notified
    assert read(client, order_id).jobs[0].meals[0].revision == 0
    if count:
        ok(decision(client, order_id, meal_id, job_id, "accept"))
        assert read(client, order_id).jobs[0].meals[0].name == "Sheet Pan Sausage and Peppers"


def test_legacy_snapshot_nulls_and_real_metadata_changes(client: TestClient) -> None:
    _order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    with Session(get_engine()) as session:
        meal = session.get_one(Meal, UUID(meal_id))
        meal.dish = meal.active_time_minutes = meal.total_time_minutes = None
        snapshot = meal_edits.current_recipe(meal)
        assert snapshot.dish is None and snapshot.total_time_minutes is None
        assert snapshot.steps[0].ingredients == ["ground beef"]
    current = meal_edit.CurrentRecipe.model_validate(
        {**recipe("Tacos").model_dump(), "servings": 2}
    )
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Tacos").model_dump())
    assert not meal_edit.changed(current, proposal)
    proposal.steps[0].timer_minutes = 2  # null to present, unchanged instructions
    assert meal_edit.changed(current, proposal)
    proposal.steps[0].timer_minutes = None
    proposal.steps[0].ingredients = ["tortillas"]
    assert meal_edit.changed(current, proposal)


def test_mcp_outcome_last_call_wins_and_done_is_required(tmp_path: Path) -> None:
    current = meal_edit.CurrentRecipe.model_validate(
        {**recipe("Tacos").model_dump(), "servings": 2}
    )
    write_context(tmp_path, meal_edit.EditMealJob(current=current))
    tools = meal_edit_server(tmp_path)
    assert call(tools, "done")[0]
    assert call(tools, "propose_recipe", recipe=recipe("Tacos").model_dump())[0]  # no-op
    proposal = recipe("Chicken tacos")
    assert not call(tools, "propose_recipe", recipe=proposal.model_dump())[0]
    outcome = meal_edit.load_outcome(tmp_path)
    assert outcome is not None and not outcome.completed
    assert not call(tools, "cannot_change", reason="Cannot do that safely.")[0]
    assert not call(tools, "done")[0]
    outcome = meal_edit.load_outcome(tmp_path)
    assert outcome is not None and outcome.proposal is None and outcome.completed
    assert not call(tools, "propose_recipe", recipe=proposal.model_dump())[0]
    outcome = meal_edit.load_outcome(tmp_path)
    assert outcome is not None and not outcome.completed
    assert outcome.proposal is not None and outcome.proposal.steps[0].ingredients == ["ground beef"]


@pytest.mark.parametrize(
    "invalid",
    ["unknown ingredient", "timer", "active", "picture", "bounds", "blank name", "blank step"],
)
def test_mcp_refuses_unusable_recipe(tmp_path: Path, invalid: str) -> None:
    write_context(
        tmp_path,
        meal_edit.EditMealJob(
            current=meal_edit.CurrentRecipe.model_validate(
                {**recipe("Tacos").model_dump(), "servings": 2}
            )
        ),
    )
    proposal = recipe("Changed")
    if invalid == "unknown ingredient":
        proposal.steps = [StepInput(text="Cook it", ingredients=["not listed"])]
    elif invalid == "timer":
        proposal.steps[0].timer_minutes = 90
    elif invalid == "active":
        proposal.active_time_minutes = 90
    elif invalid == "picture":
        proposal.ingredients[0].color = None
    elif invalid == "blank name":
        proposal.name = " "
    elif invalid == "blank step":
        proposal.steps[0].text = " "
    else:
        proposal.name = "x" * 101
    assert call(meal_edit_server(tmp_path), "propose_recipe", recipe=proposal.model_dump())[0]
    assert meal_edit.load_outcome(tmp_path) is None


def test_accept_failure_rolls_back_recipe_and_decision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
    job_id = proposed(client, order_id, meal_id)
    # A storage failure after the replacement is staged must roll back all its rows.
    with Session(get_engine()) as session:
        draft = orders.lock_draft(
            session, CASTLE, UUID(order_id), orders.Orderer(user_id=MOM, session=phone(MOM))
        )

        def failed_commit() -> None:
            session.flush()
            raise RuntimeError("storage failed")

        monkeypatch.setattr(session, "commit", failed_commit)
        with pytest.raises(RuntimeError, match="storage failed"):
            meal_edits.decide(session, draft, MOM, UUID(meal_id), job_id, EditDecision.ACCEPTED, 0)
        session.rollback()
    with Session(get_engine()) as session:
        meal = session.get_one(Meal, UUID(meal_id))
        assert (meal.name, meal.revision) == ("Tacos", 0)
        assert session.get_one(Job, job_id).decision is None
        assert session.get_one(Job, build.id).status == JobStatus.PENDING


def test_discard_racing_worker_cannot_deadlock_or_recreate_results(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, job_id)
    assert claimed is not None
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump())
    # Actual worker and HTTP deletion start together, each with its own DB connection.
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def save() -> tuple[bool, UUID | None]:
        barrier.wait()
        return worker._save(claimed, proposal, {})

    def discard() -> Response:
        barrier.wait()
        return client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM))

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved, discarded = executor.submit(save), executor.submit(discard)
        saved.result(timeout=15)
        assert discarded.result(timeout=15).status_code == 204
    with Session(get_engine()) as session:
        assert session.get(Job, job_id) is None
        meal = session.get_one(Meal, UUID(meal_id))
        assert meal.name == "Tacos" and meal.job_id is None
    assert worker._save(claimed, proposal, {}) == (False, None)


def test_recipe_revision_is_part_of_list_source_and_scaling_refuses_old_revision(
    client: TestClient,
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    with Session(get_engine()) as session:
        before = shopping.current_source(session, UUID(order_id))
    job_id = proposed(client, order_id, meal_id)
    ok(decision(client, order_id, meal_id, job_id, "accept"))
    with Session(get_engine()) as session:
        after = shopping.current_source(session, UUID(order_id))
        assert before.picks[0].revision == 0 and after.picks[0].revision == 1 and before != after
        with pytest.raises(shopping.InvalidList, match="recipe changed"):
            shopping.recipe_meals(session, before)


@pytest.mark.parametrize("failure", ["no_op", "missing", "no_done"])
def test_worker_revalidates_output_without_trusting_tools_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["no_op", "missing", "no_done"],
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)

    def fake_run(job_dir: Path, prompt: str, tools: Tools, context: meal_edit.EditMealJob) -> None:
        job_dir.mkdir(parents=True)
        # Bypass the tool; the worker must independently reject incomplete/no-op output.
        if failure == "missing":
            return
        (job_dir / "edit.json").write_text(
            meal_edit.EditOutcome(
                proposal=meal_edit.ProposedRecipe.model_validate(
                    recipe("Tacos" if failure == "no_op" else "Changed").model_dump()
                ),
                completed=failure != "no_done",
            ).model_dump_json()
        )

    monkeypatch.setattr(worker, "run_agent", fake_run)
    worker.run_job(job_id)
    change = read(client, order_id).recipe_changes[0]
    assert (
        change.state == "failed"
        and change.error is not None
        and ("didn't change anything" if failure == "no_op" else "didn't finish") in change.error
    )


def test_timer_added_to_legacy_step_is_valid_proposal_and_accepts(client: TestClient) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, job_id)
    assert claimed is not None
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Tacos").model_dump())
    proposal.steps[0].timer_minutes = 4
    assert worker._save(claimed, proposal, {}) == (True, None)
    ok(decision(client, order_id, meal_id, job_id, "accept"))
    stored = api.MealOut.model_validate(
        ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))
    )
    assert stored.steps[0].timer_minutes == 4 and stored.revision == 1


def test_failure_handler_and_late_worker_keep_edit_failed_until_choice(
    client: TestClient, events: TestClient
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        claimed = claim_job(session, job_id, TASK)
    assert claimed is not None
    response = events.post("/events", json=task_stopped(str(job_id), 137, "OutOfMemoryError"))
    assert response.status_code == 200
    change = read(client, order_id).recipe_changes[0]
    assert change.state == "failed" and change.blocks
    assert worker._save(
        claimed, meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump()), {}
    ) == (False, None)
    assert list_start(client, order_id).status_code == 409
    ok(decision(client, order_id, meal_id, job_id, "reject"))
    assert read(client, order_id).recipe_changes == []


def test_worker_cannot_restart_an_overdue_queued_edit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    ok(pick(client, order_id, meal_id))
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id), 201)
    with Session(get_engine()) as session:
        session.execute(
            text("UPDATE job SET created_at=created_at - interval '3 hours' WHERE id=:id"),
            {"id": job_id},
        )
        session.commit()
    assert read(client, order_id).recipe_changes[0].state == "failed"
    processed: list[UUID] = []

    def process(job: Job) -> worker.JobResult:
        processed.append(job.id)
        return meal_edit.ProposedRecipe.model_validate(recipe("Changed").model_dump())

    monkeypatch.setattr(worker, "process_job", process)
    worker.run_job(job_id)
    assert processed == []
    change = read(client, order_id).recipe_changes[0]
    assert change.state == "failed" and change.error == STALE_ERROR


@pytest.mark.parametrize("rescale", [False, True])
def test_yield_change_is_meaningful_even_without_other_edits(rescale: bool) -> None:
    current = meal_edit.CurrentRecipe.model_validate(
        {**recipe("Tacos").model_dump(), "servings": 4}
    )
    proposal = meal_edit.ProposedRecipe.model_validate(recipe("Tacos").model_dump())
    if rescale:
        for ingredient in proposal.ingredients:
            if ingredient.quantity is not None:
                ingredient.quantity /= 2
    assert meal_edit.changed(current, proposal)


def test_legacy_yield_reaches_worker_and_accept_scales_shopping_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id, [meal_id] = with_suggestions(client, names=("Tacos",))
    with Session(get_engine()) as session:
        meal = session.get_one(Meal, UUID(meal_id))
        meal.servings = 4  # Existing quantities: eight tortillas, one pound beef.
        session.add(meal)
        session.commit()
    ok(pick(client, order_id, meal_id))  # Household checkout yield stays four.
    job_id = uuid4()
    ok(send(client, order_id, meal_id, job_id, feedback="Use less beef."), 201)
    contexts: list[meal_edit.CurrentRecipe] = []

    def fake_run(job_dir: Path, prompt: str, tools: Tools, context: meal_edit.EditMealJob) -> None:
        assert tools == Tools.MEAL_EDIT
        contexts.append(context.current)
        assert context.current.servings == 4
        assert [i.quantity for i in context.current.ingredients] == [8, 1, None]
        # The real prompt contains the same actual facts and the fixed output yield.
        assert context.current.model_dump_json(indent=2) in prompt
        assert "scale all quantities proportionally" in prompt
        job_dir.mkdir(parents=True)
        write_context(job_dir, context)
        proposal = meal_edit.ProposedRecipe.model_validate(recipe("Tacos").model_dump())
        proposal.ingredients[0].quantity = 4  # Half the four-serving tortillas.
        proposal.ingredients[1].quantity = 0.3  # Intentionally less than the scaled 0.5 lb.
        meal_edit.propose(job_dir, proposal)
        meal_edit.done(job_dir)

    monkeypatch.setattr(worker, "run_agent", fake_run)
    worker.run_job(job_id)
    assert len(contexts) == 1
    change = read(client, order_id).recipe_changes[0]
    assert change.state == "ready" and change.proposal is not None
    assert [i.quantity for i in change.proposal.ingredients] == [4, 0.3, None]
    before = api.MealOut.model_validate(
        ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))
    )
    assert before.servings == 4 and before.ingredients[0].quantity == 8
    accepted = api.GroceryOrderOut.model_validate(
        ok(decision(client, order_id, meal_id, job_id, "accept"))
    )
    assert accepted.picks[0].servings == 4
    stored = api.MealOut.model_validate(
        ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))
    )
    assert stored.servings == 2
    assert [i.quantity for i in stored.ingredients] == [4, 0.3, None]
    with Session(get_engine()) as session:
        [scaled] = shopping.recipe_meals(session, shopping.current_source(session, UUID(order_id)))
        assert scaled.servings == 4
        assert [line.text for line in scaled.lines] == [
            "4 tortillas",
            "0.3 lb ground beef, browned",
            "salt",
        ]


@pytest.mark.parametrize("kind", ["build", "shop"])
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("affected", [False, True])
def test_accept_only_stops_pinned_dependencies_and_undo_restores_other_work(
    client: TestClient, kind: str, running: bool, affected: bool
) -> None:
    from bite_planner_agent.products import ItemChoice, ProductCount
    from bite_planner_agent.shopping import Aisle

    from tests.test_api import ONIONS
    from tests.test_prepared_recipes import proportional_snapshot

    order_id, [a, b] = with_suggestions(client)
    ok(pick(client, order_id, a))
    build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
    if kind == "shop":
        finish_list(str(build.id), shop=False)
        active = api.JobOut.model_validate(
            ok(
                client.post(
                    f"/v1/grocery-orders/{order_id}/list/shop",
                    headers=as_user(MOM),
                    json={"job_id": str(uuid4())},
                ),
                201,
            )
        )
    else:
        active = build
    with Session(get_engine()) as session:
        original: Job = session.get_one(Job, active.id)
        if running:
            claimed = claim_job(session, active.id)
            assert claimed is not None
            original = claimed
        pinned = session.get_one(Job, build.id).list_source
        assert pinned is not None
        if kind == "build":
            result: worker.JobResult = worker.ListItems(
                items=[
                    shopping.ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1])
                ],
                source=pinned,
                prepared=proportional_snapshot(shopping.recipe_meals(session, pinned)),
            )
        else:
            bought = [item for item in shopping.items_of(session, build.id) if not item.on_hand]
            result = worker.Products(
                build_id=build.id,
                items={n: item.id for n, item in enumerate(bought, 1)},
                choices={
                    n: ItemChoice(
                        item=n, chosen=ProductCount(product=ONIONS, count=4), alternatives=[]
                    )
                    for n, _ in enumerate(bought, 1)
                },
            )
    # Work was pinned only to A. Adding B must remain reversible even after B changes.
    ok(pick(client, order_id, b))
    target = a if affected else b
    edit = proposed(client, order_id, target)
    ok(decision(client, order_id, target, edit, "accept"))
    after = read(client, order_id)
    stored = next(job for job in after.jobs if job.id == active.id)
    assert stored.status == ("failed" if affected else "running" if running else "pending")
    assert after.shopping_list is not None and not after.shopping_list.source_current
    ok(client.delete(f"/v1/grocery-orders/{order_id}/meals/{b}", headers=as_user(MOM)))
    undone = read(client, order_id)
    assert undone.shopping_list is not None
    assert undone.shopping_list.source_current is not affected
    assert undone.shopping_list.current == (not affected and kind == "shop")
    if affected:
        assert stored.error == SUPERSEDED_ERROR
        with Session(get_engine()) as session:
            assert claim_job(session, active.id) is None
        assert worker._save(original, result, {}) == (False, None)
        unchanged = read(client, order_id)
        assert unchanged.shopping_list is not None
        assert unchanged.shopping_list.items == undone.shopping_list.items
    else:
        if not running:
            with Session(get_engine()) as session:
                claimed = claim_job(session, active.id)
            assert claimed is not None
            original = claimed
        saved, next_shop = worker._save(original, result, {})
        assert saved and (next_shop is not None) == (kind == "build")
        finished = read(client, order_id)
        assert finished.shopping_list is not None and finished.shopping_list.current
        if kind == "shop":
            assert finished.shopping_list.shop is not None
            assert finished.shopping_list.shop.status == "succeeded"
            assert all(
                item.product is not None and item.product.count == 4
                for item in finished.shopping_list.items
                if not item.on_hand
            )


@pytest.mark.parametrize("status", ["pending", "running", "failed", "succeeded"])
def test_source_current_reports_exact_inputs_independent_of_status(
    client: TestClient, status: str
) -> None:
    order_id, [a, b] = with_suggestions(client)
    ok(pick(client, order_id, a))
    build = api.JobOut.model_validate(ok(list_start(client, order_id), 201))
    if status == "succeeded":
        finish_list(str(build.id))
    elif status == "running":
        with Session(get_engine()) as session:
            assert claim_job(session, build.id) is not None
    elif status == "failed":
        with Session(get_engine()) as session:
            fail_job(session, build.id, "Fake provider failed to build the list.")
    before = read(client, order_id).shopping_list
    assert before is not None and before.source_current
    assert before.current == (status == "succeeded")
    ok(set_servings(client, order_id, a, 6))
    changed = read(client, order_id).shopping_list
    assert changed is not None and not changed.source_current and not changed.current
    ok(set_servings(client, order_id, a, 4))
    assert read(client, order_id).shopping_list == before
    ok(pick(client, order_id, b))
    changed = read(client, order_id).shopping_list
    assert changed is not None and not changed.source_current
    ok(client.delete(f"/v1/grocery-orders/{order_id}/meals/{b}", headers=as_user(MOM)))
    assert read(client, order_id).shopping_list == before
    edit = proposed(client, order_id, a)
    ok(decision(client, order_id, a, edit, "accept"))
    accepted = read(client, order_id).shopping_list
    assert accepted is not None and not accepted.source_current and not accepted.current
