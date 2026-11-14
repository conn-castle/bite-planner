Adjust the recipes to the chosen servings, then combine their ingredients into one shopping list.

## Recipes

When the chosen servings differ from the original yield, adjust the original recipe using the proportional estimates as a starting point. Round to practical cooking amounts: both 1⅞ cans and 2⅛ cans can become 2 cans. Preserve the recipe's proportions and flavour. Don't round weights or volumes to fit retail packages or guess package sizes. Make quantities in the steps match the ingredients, including portions split between steps or reserved for later. Save the adjusted amounts and steps with `adapt_recipe`.

## Shopping list

Combine ingredients that can be bought as one product. For example, combine whole and diced onions, but keep fresh tomatoes and canned tomatoes separate. Add each item to buy with `add_item`.

List the total amount the final recipes use: for example, 3 cups of milk, not a guessed number of cartons. A later step chooses which packages to buy. If a recipe measures an ingredient in cans, keep that unit.

Staples are ingredients we assume are already in the kitchen: $staples. Ordinary varieties count as staples too, such as sea salt and extra-virgin olive oil. Record the ones used with `add_staple` instead of buying them.

Choose the aisle for the product being bought:
$aisles

Call `done` when complete.

## Meals

$meals
