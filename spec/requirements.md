# Requirements

Functional requirements for ordering. Check an item off when it's implemented. The UI is specified by [`mockup.html`](mockup.html).

## Orders

- [x] An order is N meals covering M days; meals are not assigned to specific days.
- [x] Anyone in the household can create and place an order.
- [x] Creating an order walks the user through picking the meals, the delivery date, and the next delivery date.
- [x] The delivery date defaults to the order's target date (the previous order's next delivery date); the user can change it.
- [x] The earliest selectable delivery date comes from a function (later: the grocery service's soonest slot); for now it returns tomorrow.
- [x] The next delivery date defaults to the delivery date plus one day per meal; the user can change it.
- [x] The next delivery date becomes the target date of the household's next order.
- [x] Each order's decide-by date is its target date minus the household's decide-by offset.
- [x] Delivery dates that overlap or leave a gap with the previous order are allowed.
- [x] Orders cannot be edited once placed.

## Ordering flow

The flow being built toward, one piece at a time:

1. Meal ideas are generated, and while they generate the user is asked to rate recent meals.
2. Pick meals.
3. Servings: each picked meal's servings (− and + around the number). Leaving this step starts building the shopping list.
4. Delivery date.
5. Next delivery date (which sets the next order's decide-by date).
6. The shopping list: the user waits for it here if it isn't built yet, then reviews it (removes items, changes quantities).
7. An agent shops the list at the grocery store(s) and chooses the exact products to order; the user sees those exact items before placing (with grocery stores, later).
8. Final review: the user checks the order and confirms; it's placed, and the confirmation shows the store order(s) it's associated with.

Each step does one thing.

- [x] The flow has one step each for servings, the delivery date, and the next delivery date (replacing the single "Dates and servings" step).
- [x] Each picked meal's servings are stored in the order. A meal starts at the household's default servings and can be changed per meal.

## Draft order

- [ ] An unplaced order is a household draft: its suggestions, picks, and dismissals are kept when the flow is closed.
- [x] Any household member can resume the draft.
- [x] A household member can discard the draft.

## Picking meals

- [x] The user picks meals from 20 suggestions by default.
- [x] The user can add a suggestion to the order, and toggle it back off.
- [x] The user can mark a suggestion as "don't show meals like this", and toggle it back off. This is offered only while picking suggestions.
- [x] Future suggestions avoid meals like the ones marked "don't show meals like this"; these preferences belong to the household.
- [x] The user can generate 20 more suggestions, added below the current ones.
- [x] The user can leave while suggestions are generating and gets a notification when they're ready.
- [ ] Each meal shows an icon.

## Rating meals

- [x] A meal can be rated thumbs up, thumbs down, or favorite, separate from "don't show meals like this". A meal has one rating at a time; a favorite is always liked (spec/README.md).
- [x] Each rating can be toggled off again.
- [x] Ratings belong to the household and are stored for the suggestion agent to use later.
- [x] When generating suggestions, the app asks the user to rate meals from the last week that aren't rated yet.

## Order lock

- [x] Only one household member can be creating an order at a time.
- [x] The lock is held from when order creation starts until it is closed or the order is placed.
- [x] The open app renews the lock every 30 seconds; the lock expires 2 minutes after the last renewal.
- [x] While one member has order creation open, other members are told that another household member is ordering.

## Shopping list

The list is made in three stages:

1. Python scales each picked meal's ingredients to its servings (deterministic).
2. An AI agent combines those ingredients into one list of things to buy (e.g. "1 onion" and "1 cup diced onion" become onions), grouped by aisle.
3. An agent shops that list at the grocery store(s) (Instacart and/or Walmart, whichever is easier to set up; both is ideal) and chooses the exact products to order, using the household's store accounts and preferences.

- [x] Before an order is placed, the backend generates its shopping list from the chosen meals and their servings (stages 1 and 2), asynchronously (it takes minutes), starting as soon as the servings are chosen; the user sees it when it's done.
- [x] Changing the picked meals or their servings after the list was built rebuilds it from scratch; the user's changes to the old list are dropped. (Generation isn't deterministic and can change the list a lot, so there is no merging.)
- [x] The user can remove items from the shopping list and change their quantities.
- [x] Items assumed to be on hand are never on the list. The list is the same for every household and isn't editable: salt, pepper, olive oil, vegetable oil, water. When the meals need any of them, an icon on the shopping list shows it; tapping it lists the assumed items these recipes need. They aren't shown otherwise (they'd be noise).
- [x] Every scaled ingredient line ends up in exactly one item to buy or one staple; the agent says which lines each covers, and the list isn't saved until every line is covered.
- [x] Things counted (lemons, onions) are rounded up to whole ones; amounts in a unit keep up to two decimals (the product is chosen in stage 3).
- [x] Items are grouped by aisle: produce, meat & seafood, dairy & eggs, frozen, pantry, and other (for what fits none of them).
- [ ] Before placing, the user sees the exact products that will be ordered (stage 3).

## Placing and status

- [x] Placing an order calls the API, which saves the order and its status in the database.
- [x] The call to the grocery service is a stub that does nothing and returns success.
- [x] An order's status is saved and shown; for now it is always "order placed".
- [ ] The confirmation after placing shows the store order(s) the order is associated with.

## Calendar and tasks

- [x] The app shows delivery dates, decide-by dates, and the days each order covers, from past orders to 90 days ahead.
- [ ] The app tells the user which tasks are due, such as picking meals and placing the next order.

## Notifications

- [x] The app sends push notifications to household members.
- [x] Reminders to order start a set number of days before the decide-by date and go out daily at each member's notification time until the order is placed, including after the decide-by date.
- [x] Reminders go to every member, including while another member is ordering.

## Household

- [x] A person is in only one household.
- [x] Settings lists the household's members by name.
- [ ] Anyone in a household can add and remove its members. Not scheduled yet; until then, the admin command does it.

## Settings

Household settings (shared by all members):

- [x] Decide-by offset: days before the target date, default 1.
- [x] Reminder lead: days before the decide-by date that reminders start, default 2.
- [x] Default servings: the servings each picked meal starts at, default 4.

Personal settings:

- [x] Notification time, in the time zone of the member's phone (detected automatically, never shown or asked for).

## Views

- [x] History shows every past order and all of its meals, grouped by order.
- [x] History's meals can be searched by name and filtered, including by rating.
- [x] Order view: an order's dates, status, meals, and shopping list.
- [x] Meal view: a meal's recipe (servings, ingredients, facts).
- [ ] Instructions view: a meal's cooking steps.

## Deferred

- Placing orders with a real grocery service (with shopping list stage 3).
- Real delivery status updates.
- Delivery time windows.
- Handling gaps between orders (for now, reminders go out every day).
- The suggestion agent using ratings.
- Meal icons v2 (generated combinations of icons) and v3 (AI-generated images).
- Adding free-form items to the shopping list (done in the grocery app instead).
- Having suggestions available immediately, such as generating them ahead of time (to discuss; the ready notification may change with it).
- Meal preferences the household can check in settings (well after launch), which fill the suggestion agent's instructions; "low effort to cook" becomes one of them.
- Capturing each job's full cost robustly (the agent's tokens and dollars, the Fargate task, and anything else it uses), stored with the job, so we can estimate what the service would cost to run as a product.
