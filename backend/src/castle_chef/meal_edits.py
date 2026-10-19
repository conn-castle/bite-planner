"""Order-owned recipe edits: proposal only until one exact Accept decision."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from castle_chef_agent.meal_edit import CurrentIngredient, CurrentRecipe, ProposedRecipe, changed
from castle_chef_agent.meals import RECIPE_SERVINGS, StepInput
from pydantic import BaseModel
from sqlmodel import Session, col, func, select, update

from castle_chef import jobs, orders, ratings
from castle_chef.db import for_update
from castle_chef.models import (
    EditDecision,
    GroceryOrder,
    Job,
    JobKind,
    JobStatus,
    Meal,
    MealEditRequest,
    MealIngredient,
    MealRating,
    MealStep,
    utc_now,
)

type ChangeState = Literal["running", "ready", "failed"]


class EditOpen(Exception):
    pass


class EditDecided(Exception):
    pass


class EditNotReady(Exception):
    pass


class EditStale(Exception):
    pass


class EditNotFound(Exception):
    pass


class RecipeBlocker(BaseModel):
    meal_id: UUID
    name: str
    state: ChangeState


class RecipeChangesPending(Exception):
    def __init__(self, blockers: list[RecipeBlocker]) -> None:
        self.blockers = blockers
        super().__init__(
            "Review recipe changes before shopping: " + ", ".join(b.name for b in blockers)
        )


class Change(BaseModel):
    meal_id: UUID
    job_id: UUID
    state: ChangeState
    picked: bool
    blocks: bool
    feedback: str
    requested_by: UUID
    created_at: datetime
    started_at: datetime | None
    error: str | None
    base_revision: int
    proposal: ProposedRecipe | None


def current_recipe(meal: Meal) -> CurrentRecipe:
    return CurrentRecipe(
        name=meal.name,
        servings=meal.servings,
        description=meal.description,
        dish=meal.dish,
        active_time_minutes=meal.active_time_minutes,
        total_time_minutes=meal.total_time_minutes,
        ingredients=[
            CurrentIngredient.model_validate(i, from_attributes=True) for i in meal.ingredients
        ],
        steps=[
            StepInput(
                text=s.text,
                timer_minutes=s.timer_minutes,
                ingredients=[meal.ingredients[p].name for p in s.ingredients],
            )
            for s in meal.steps
        ],
    )


def open_jobs(session: Session, order_id: UUID) -> list[Job]:
    return list(
        session.exec(
            select(Job).where(
                Job.grocery_order_id == order_id,
                Job.kind == JobKind.EDIT_MEAL,
                col(Job.decision).is_(None),
            )
        ).all()
    )


def changes(session: Session, order: GroceryOrder) -> list[Change]:
    picked = {p.meal.id for p in orders.picks(session, order.id)}
    now = utc_now()
    result: list[Change] = []
    for job in open_jobs(session, order.id):
        request = job.meal_edit
        if request is None or job.meal_id is None:
            raise RuntimeError(f"Edit job {job.id} has no recipe request.")
        stale = jobs.is_stale(job, now)
        state: ChangeState = (
            "failed"
            if stale or job.status == JobStatus.FAILED
            else "ready"
            if job.status == JobStatus.SUCCEEDED
            else "running"
        )
        result.append(
            Change(
                meal_id=job.meal_id,
                job_id=job.id,
                state=state,
                picked=job.meal_id in picked,
                blocks=job.meal_id in picked,
                feedback=request.feedback,
                requested_by=job.requested_by,
                created_at=job.created_at,
                started_at=job.started_at,
                error=jobs.STALE_ERROR if stale else job.error,
                base_revision=request.base_revision,
                proposal=job.proposal if state == "ready" else None,
            )
        )
    return result


def recipe_blockers(session: Session, order: GroceryOrder) -> list[RecipeBlocker]:
    return [
        RecipeBlocker(meal_id=c.meal_id, name=session.get_one(Meal, c.meal_id).name, state=c.state)
        for c in changes(session, order)
        if c.blocks
    ]


def require_resolved(session: Session, order: GroceryOrder) -> None:
    """Caller holds order lock; guard BEFORE any shopping reuse/retry return."""
    jobs.fail_stale_jobs(session, order.id)
    if blockers := recipe_blockers(session, order):
        raise RecipeChangesPending(blockers)


def _suggestion(session: Session, order: GroceryOrder, meal_id: UUID) -> Meal:
    meal = session.exec(
        select(Meal)
        .join(Job, col(Meal.job_id) == Job.id)
        .where(
            Meal.id == meal_id,
            Job.grocery_order_id == order.id,
            Job.kind == JobKind.GENERATE_MEALS,
        )
    ).first()
    if meal is None:
        raise orders.NotASuggestion("No such suggestion on this order.")
    return meal


def submit(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    meal_id: UUID,
    job_id: UUID,
    feedback: str,
    replaces: UUID | None,
) -> tuple[Job, bool]:
    existing = session.get(Job, job_id)
    if existing is not None:
        expected = (order.id, member_id, JobKind.EDIT_MEAL, meal_id, feedback, replaces)
        request = existing.meal_edit
        actual = (
            existing.grocery_order_id,
            existing.requested_by,
            existing.kind,
            existing.meal_id,
            request.feedback if request else None,
            request.replaces if request else None,
        )
        session.rollback()
        if actual != expected:
            raise jobs.JobIdTaken("This job id belongs to a different request.")
        return existing, False
    jobs.fail_stale_jobs(session, order.id)
    meal = _suggestion(session, order, meal_id)
    if (
        ratings.of_meals(session, order.household_id, [meal_id]).get(meal_id)
        == MealRating.NOT_FOR_US
    ):
        raise orders.NotForUs("This meal is marked not for us.")
    undecided = next((j for j in open_jobs(session, order.id) if j.meal_id == meal_id), None)
    previous = session.get(Job, replaces) if replaces is not None else None
    if replaces is not None:
        if previous is None or previous.grocery_order_id != order.id or previous.meal_id != meal_id:
            raise EditNotFound("No such recipe change.")
        if previous.decision is not None:
            raise EditDecided("This recipe change was already decided. Refresh the recipe.")
    if undecided is not None:
        if undecided.status in jobs.ACTIVE or undecided.id != replaces:
            raise EditOpen(
                "Review the recipe change, or try again or keep the current recipe first."
            )
        _decide(undecided, member_id, EditDecision.REJECTED)
        session.add(undecided)
        session.flush()  # release the unique undecided slot before INSERT
    job = Job(
        id=job_id,
        grocery_order_id=order.id,
        requested_by=member_id,
        kind=JobKind.EDIT_MEAL,
        meal_id=meal_id,
        meal_edit=MealEditRequest(
            feedback=feedback, base_revision=meal.revision, replaces=replaces
        ),
    )
    return jobs.add_job(session, job), True


def _decide(job: Job, member_id: UUID, decision: EditDecision) -> None:
    job.decision, job.decided_by, job.decided_at = decision, member_id, utc_now()


def decide(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    meal_id: UUID,
    job_id: UUID,
    decision: EditDecision,
    base_revision: int | None = None,
) -> None:
    jobs.fail_stale_jobs(session, order.id)
    job = session.exec(
        for_update(
            select(Job).where(
                Job.id == job_id,
                Job.grocery_order_id == order.id,
                Job.meal_id == meal_id,
                Job.kind == JobKind.EDIT_MEAL,
            )
        )
    ).first()
    if job is None:
        raise EditNotFound("No such recipe change.")
    if job.decision == decision:
        session.commit()  # decided retries precede revision and eligibility checks
        return
    if job.decision is not None:
        raise EditDecided("This recipe change was already decided. Refresh the recipe.")
    if job.status in jobs.ACTIVE or (
        decision == EditDecision.ACCEPTED and job.status != JobStatus.SUCCEEDED
    ):
        raise EditNotReady("This recipe change isn't ready for that decision.")
    if decision == EditDecision.ACCEPTED:
        meal = session.exec(for_update(select(Meal).where(Meal.id == meal_id))).one()
        request, proposal = job.meal_edit, job.proposal
        if request is None or proposal is None:
            raise RuntimeError(f"Succeeded edit {job.id} has no proposal/request.")
        if base_revision != request.base_revision or meal.revision != request.base_revision:
            raise EditStale("The recipe changed. Refresh it before reviewing changes.")
        # Validate the immutable saved proposal again before touching accepted data.
        proposal = ProposedRecipe.model_validate(proposal.model_dump())
        if not changed(current_recipe(meal), proposal):
            raise EditStale("This proposal no longer changes the recipe.")
        meal.name, meal.description, meal.dish = proposal.name, proposal.description, proposal.dish
        meal.active_time_minutes = proposal.active_time_minutes
        meal.total_time_minutes = proposal.total_time_minutes
        meal.servings = RECIPE_SERVINGS
        meal.steps = [
            MealStep(
                text=s.text, timer_minutes=s.timer_minutes, ingredients=proposal.step_ingredients(s)
            )
            for s in proposal.steps
        ]
        meal.ingredients = [
            MealIngredient(position=p, **i.model_dump()) for p, i in enumerate(proposal.ingredients)
        ]
        meal.revision += 1
        session.add(meal)
        if any(p.meal.id == meal_id for p in orders.picks(session, order.id)):
            jobs.supersede_shopping(session, order.id, meal_id)
    _decide(job, member_id, decision)
    session.add(job)
    session.commit()


def finish_edit(session: Session, job_id: UUID, proposal: ProposedRecipe) -> bool:
    """One statement satisfies the success/proposal CHECK; late workers cannot revive."""
    finished = session.exec(
        update(Job)
        .where(
            col(Job.id) == job_id,
            col(Job.status) == JobStatus.RUNNING,
            ~jobs._stale(),
        )
        .values(
            proposal=proposal,
            status=JobStatus.SUCCEEDED,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    ).rowcount
    if finished:
        session.commit()
    else:
        session.rollback()
    return bool(finished)
