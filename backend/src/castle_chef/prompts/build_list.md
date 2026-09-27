Make one shopping list of things to buy at a grocery store, covering all the ingredients of the family's meals below. The amounts are already scaled to the servings being made.

## Staples

Staples are ingredients every kitchen already has: $staples. Never add them with `add_item`. Instead, add each staple the meals use, once, with `add_staple`. Ordinary variants count as the staple: black pepper is pepper, sea salt is salt, and extra-virgin olive oil is olive oil. Bell peppers, seasoned salts, and other oils are not staples.

## Rules

1. Cover every ingredient line exactly once: in one shopping list item, or as a staple.

2. Combine ingredients that you would buy as the same product at the store. Preparation alone doesn't make a separate item. For example, whole and diced onions are one item, and so are garlic cloves and minced fresh garlic. Keep items apart when they are different products, such as fresh and dried, canned and fresh, or boneless and bone-in.

3. Name each item as a shopper recognizes it. Leave out amounts, units, and preparation done at home. Keep what's needed to buy the right product, such as "boneless chicken thighs" or "crushed tomatoes".

4. Sum the specified amounts for a single item into one purchasable unit.

5. Count fresh produce that's bought by the piece, converting prepared amounts with reasonable yields for that ingredient. Make what's counted clear in the name, such as "broccoli heads" or "garlic cloves". Keep a weight or volume for produce not bought by the piece.

6. Give each item one of these aisles, by the form being bought:
$aisles

Explicitly frozen ingredients go in frozen. Canned, dried, and other shelf-stable packaged ingredients go in pantry, even when made from meat or produce. Use other only when no other aisle fits.

## Meals

Each ingredient line has an id, such as 1a, that the tools use.

$meals

## Tools

Add each item with the castle-chef `add_item` tool and each staple with `add_staple`, giving the ids of the lines each one covers. If a tool reports an error, fix what it names and call it again. To change an item or staple you added, take it off with `remove` and add it again.

When you think the list is finished, call `done`. It checks only that every line is covered exactly once and that there is something to buy; the names, amounts, and aisles are yours to decide. Fix what it reports and call it again until it says the list is complete. Then reply with the number of items and staples you added.
