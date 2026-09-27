Make one shopping list for a family's meals. Everything you need is in these instructions: don't read any files or look anything up, and run no command but `castle-chef list add`.

Below are the meals, each numbered, with its ingredients already scaled to the servings being made:

$meals

Combine these into the items to buy:

- Every ingredient above is covered by exactly one item. The same ingredient in several meals, or in several forms, is one item: "1 onion" and "1 cup onion, diced" become "yellow onions" with the total count; "2 cloves garlic" and "1 tsp garlic, minced" become garlic cloves.
- Each item has one unit for its total amount. Keep an ingredient's own unit; convert only to combine amounts in different units, into the unit a shopper would use (8 oz and 1 lb of ground beef make 1.5 lb). Buy vegetables and fruit whole, by count ("cups of broccoli florets" become heads of broccoli, "1 cup onion, diced" an onion). Keep the amount the recipes need, rounded to at most two decimals; don't round up to package sizes.
- Name items as a shopper looks for them, without preparation words ("chicken thighs", not "chicken thighs, cubed").
- These are assumed to be in every kitchen: $on_hand. Never list them as items to buy. If any meal uses one, add it once with `"on_hand": true`, named exactly as written here, with the meals that use it and no quantity.
- Give each item its aisle, one of: $aisles.
- `meals` lists the numbers of the meals that use the item.

Add each item by running this shell command once per item, with the item as JSON on stdin:

```sh
castle-chef list add <<'EOF'
{"name": "yellow onions", "quantity": 2, "unit": null, "aisle": "produce", "meals": [1, 3]}
EOF
```

The JSON must match this JSON Schema:

```json
$item_schema
```

You can add several items in one shell command, one `castle-chef list add` per item. If a command reports an error, fix that item's JSON and add it again; an item that was saved must not be added again.

After you finish, the list is checked: every item's `meals` are numbers from the list above; an item with `"on_hand": true` is named exactly as one of the items assumed in every kitchen, and appears once; no item to buy is named as one of them; and there is at least one item to buy. There is nothing else to check or look up. When every item is added, reply with how many you added.
