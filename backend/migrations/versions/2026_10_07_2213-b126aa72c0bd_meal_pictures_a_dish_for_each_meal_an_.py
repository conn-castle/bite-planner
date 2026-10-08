"""Meal pictures: a dish for each meal, an icon and color for its ingredients

Revision ID: b126aa72c0bd
Revises: 10176786b8f1
Create Date: 2026-10-07 22:13:31.109231

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b126aa72c0bd"
down_revision: str | Sequence[str] | None = "10176786b8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISHES = ("plate", "bowl", "skillet", "tacos")
ICONS = (
    *("fish", "shrimp", "drumstick", "steak", "pork_chop", "ground_meat", "tofu", "egg"),
    *("beans", "chickpeas", "rice", "pasta", "sauce", "tortilla", "leafy", "stalks"),
    *("round", "pepper", "mushroom", "root", "broccoli", "corn", "bread", "bacon"),
    *("cheese_wedge", "cheese_cube"),
)
COLORS = (
    *("white", "cream", "yellow", "gold", "tan", "orange", "salmon", "pink", "red"),
    *("dark_red", "purple", "brown", "black", "light_green", "green", "dark_green"),
)


def upgrade() -> None:
    """Upgrade schema."""
    dish = postgresql.ENUM(*DISHES, name="dish")
    icon = postgresql.ENUM(*ICONS, name="ingredient_icon")
    color = postgresql.ENUM(*COLORS, name="ingredient_color")
    for enum in (dish, icon, color):
        enum.create(op.get_bind())
    # Meals from before pictures have none: their dish and icons stay null.
    op.add_column("meal", sa.Column("dish", dish, nullable=True))
    op.add_column("meal_ingredient", sa.Column("icon", icon, nullable=True))
    op.add_column("meal_ingredient", sa.Column("color", color, nullable=True))
    op.create_check_constraint(
        "meal_ingredient_icon_color", "meal_ingredient", "(icon IS NULL) = (color IS NULL)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("meal_ingredient_icon_color", "meal_ingredient", type_="check")
    op.drop_column("meal_ingredient", "color")
    op.drop_column("meal_ingredient", "icon")
    op.drop_column("meal", "dish")
    for name in ("ingredient_color", "ingredient_icon", "dish"):
        postgresql.ENUM(name=name).drop(op.get_bind())
