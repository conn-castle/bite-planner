"""Write delivery evidence in the same transaction as the action it describes."""

from uuid import UUID

from castle_chef_agent.windows import Window
from sqlmodel import Session

from castle_chef.models import DeliveryDecision, DeliveryDecisionKind, GroceryOrder
from castle_chef.worker_tasks import WorkerStop


def record(
    session: Session,
    order: GroceryOrder,
    kind: DeliveryDecisionKind,
    *,
    user_id: UUID | None = None,
    job_id: UUID | None = None,
    proposal_id: UUID | None = None,
    window: Window | None = None,
    authorized_total_cents: int | None = None,
    worker_stop: WorkerStop | None = None,
) -> None:
    issue = order.book_issue
    session.add(
        DeliveryDecision(
            grocery_order_id=order.id,
            household_id=order.household_id,
            kind=kind,
            user_id=user_id,
            job_id=job_id,
            proposal_id=proposal_id or (issue.proposal.id if issue and issue.proposal else None),
            delivery_date=order.delivery_date,
            delivery_by=order.delivery_by,
            next_delivery_date=order.next_delivery_date,
            window=window,
            issue=issue,
            booking=order.booking,
            authorized_total_cents=authorized_total_cents,
            worker_stop=worker_stop,
        )
    )
