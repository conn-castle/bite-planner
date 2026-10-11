# Requirements

Functional requirements for ordering. Check an item off when it's implemented. The UI is specified by [`mockup.html`](mockup.html).

## Orders

- [x] An order is N meals covering M days; meals are not assigned to specific days.
- [x] Anyone in the household can create and place an order.
- [x] Creating an order walks the user through picking the meals, the delivery date, and the next delivery date.
- [x] The delivery date defaults to the order's target date (the previous order's next delivery date); the user can change it.
- [x] The earliest selectable delivery date comes from a function; it returns tomorrow.
- [x] The delivery is a day and a latest time it should arrive by ("by 6:00 PM"), chosen after the shopping list. Any day from the earliest can be chosen, not only the days Instacart offers yet (it shows up to five days after today); the time starts at the household's delivery time (stage 3).
- [x] The next delivery date defaults to the delivery date plus one day per meal; the user can change it.
- [x] The next delivery date becomes the target date of the household's next order.
- [x] Each order's decide-by date is its target date minus the household's decide-by offset.
- [x] Delivery dates that overlap or leave a gap with the previous order are allowed.
- [x] Orders cannot be edited once placed. From stage 3, placed means booked at Instacart: an approved order waiting to book can still be changed in the app (even though Instacart allows some changes after booking, the app doesn't).

## Ordering flow

The flow being built toward, one piece at a time:

1. Meal ideas are generated, and while they generate the user is asked to rate recent meals.
2. Pick meals.
3. Servings: each picked meal's servings (− and + around the number). Leaving this step starts the shopping list: it's made, then shopped at Instacart, in one wait.
4. The shopping list: the Wegmans products chosen for it, which the user reviews and changes (piece 9). Until piece 9, the list of things to buy, after the dates.
5. Delivery: the day, and the latest time it should arrive by (piece 9). Until piece 9, the delivery date, after servings.
6. Next delivery date (which sets the next order's decide-by date).
7. Final review: the user checks the order, with its estimated total, and approves it. It's booked at Instacart right away or, for a day not bookable yet, one day after Instacart first offers it; the confirmation shows the Instacart order.

Each step does one thing.

- [x] The flow has one step each for servings, the delivery date, and the next delivery date (replacing the single "Dates and servings" step).
- [x] Each picked meal's servings are stored in the order. A meal starts at the household's default servings and can be changed per meal.

## Draft order

- [x] An unplaced order is a household draft: its suggestions, picks, and dismissals are kept when the flow is closed.
- [x] Any household member can resume the draft, at the furthest step its flow has reached.
- [x] A household member can discard the draft.

## Picking meals

- [x] The user picks meals from 20 suggestions by default.
- [x] The user can add a suggestion to the order, and toggle it back off.
- [x] The user can mark a suggestion as "don't show meals like this", and toggle it back off. This is offered only while picking suggestions.
- [x] Future suggestions avoid meals like the ones marked "don't show meals like this"; these preferences belong to the household.
- [x] The user can generate 20 more suggestions, added below the current ones.
- [x] The user can leave while suggestions are generating and gets a notification when they're ready.
- [x] Each meal shows an icon: a picture made from its ingredients, the same every time (meals from before pictures show a plain plate).

## Rating meals

- [x] A meal can be rated thumbs up, thumbs down, or favorite, separate from "don't show meals like this". A meal has one rating at a time; a favorite is always liked (spec/README.md).
- [x] Each rating can be toggled off again.
- [x] Ratings belong to the household and are stored for the suggestion agent to use later.
- [x] When generating suggestions, the app asks the user to rate meals from the last week that aren't rated yet.

## Order lock

- [x] Only one household member can be creating an order at a time.
- [x] The lock is held from when order creation starts until it is closed or the order is placed (from stage 3: approved).
- [x] The open app renews the lock every 30 seconds; the lock expires 2 minutes after the last renewal.
- [x] While one member has order creation open, other members are told that another household member is ordering.

## Shopping list

The list is made in three stages:

1. Python scales each picked meal's ingredients to its servings (deterministic).
2. An AI agent combines those ingredients into one list of things to buy (e.g. "1 onion" and "1 cup diced onion" become onions), grouped by aisle.
3. An agent shops that list at Instacart: it chooses the exact product for each item, with alternatives; the user reviews them in the app, picks the delivery day and latest time, and approves, and the order is booked without anything more to do.

Stage 3 is Instacart only, for our own household (research in piece 9, 2026-09-27):

- No grocery service offers a public, self-serve way for another app to place orders on a shopper's account. Instacart's developer program isn't accepting applications; its tools that fill a real cart and check out are for partners (ChatGPT, Claude, Gemini, Muse). Walmart's checkout API and grocery program need approval and a business case.
- For our household it works today, with the household's accounts (Nick's): Instacart's own tools in the ChatGPT apps, called through the Codex app-server with the household's ChatGPT sign-in, search Wegmans (with sizes, prices, pictures, and "Buy it again") and fill the cart; a headless browser with the household's Instacart session reads checkout (times, fees, total) and places the order after the user approves it in the app. The model only searches and chooses, through our tools; our code writes the cart and checks out. Instacart's terms don't allow an automated system to operate an account without its authorization; the account owner accepts that for personal use.
- Walmart is the path if Castle Chef is ever offered to other households: its product API is self-serve, and one link fills anyone's Walmart cart with chosen products and quantities; the shopper then checks out themselves.

- [x] Before an order is placed, the backend generates its shopping list from the chosen meals and their servings (stages 1 and 2), asynchronously (it takes minutes), starting as soon as the servings are chosen; the user sees it when it's done.
- [x] Changing the picked meals or their servings after the list was built rebuilds it from scratch; the user's changes to the old list are dropped. (Generation isn't deterministic and can change the list a lot, so there is no merging.)
- [x] The user can remove items from the shopping list and change their quantities.
- [x] Items assumed to be on hand are never on the list. The list is the same for every household and isn't editable: salt, pepper, olive oil, vegetable oil, water. When the meals need any of them, an icon on the shopping list shows it; tapping it lists the assumed items these recipes need. They aren't shown otherwise (they'd be noise).
- [x] Every scaled ingredient line ends up in exactly one item to buy or one staple; the agent says which lines each covers, and the list isn't saved until every line is covered.
- [x] Things counted (lemons, onions) are rounded up to whole ones; amounts in a unit keep up to two decimals (the product is chosen in stage 3).
- [x] Items are grouped by aisle: produce, meat & seafood, dairy & eggs, frozen, pantry, and other (for what fits none of them).
- [x] The list is made and then shopped in one wait after servings; the shopping list step shows the chosen Wegmans products by aisle, as tiles (picture, the item and the product under it, an icon for what the household has bought before, price, how many, and a × to not buy it). The list of things to buy is no longer a step of its own (stage 3).
- [x] The agent gets the list (what to buy) and the meals with their scaled ingredients (for reference). For each item it chooses the product that best fits what the meals need, enough to cover the item's amount on the list, breaking ties by what the household has bought before, plus up to eight alternatives, or why there are none.
- [x] An item Wegmans doesn't have comes with alternatives to buy and/or why the meal works without it. Such items come first, in a red card; the user skips each or picks an alternative before going on.
- [x] Tapping a product opens its page: whether it was bought before and what it's for; the user can change how many, switch to an alternative (up to eight), or not buy it.
- [x] The delivery step is the day and the latest time ("by 6:00 PM"); it doesn't ask Instacart anything. The time is the store's local time (the delivery address's), shown as that time whatever the phone's own time zone.
- [x] The final review fills the household's Wegmans cart with the chosen products and reads checkout for the estimated total: items, fees, tax, and the tip (from settings). Whenever the cart is filled (here, and when booking), the household's own items already in it are set aside first and put back when that's done: read, booked, failed, or interrupted (a run that stops midway puts them back on the next one).
- [x] The final review shows the estimated total (the items, each of checkout's other lines as Instacart labels them, and the tip), the day and latest time, and the account's address, card, and what Instacart does when an item is out of stock (a setting on each item in the cart, "Replace with best match" unless changed: the one setting, or "Varies by item"). Below the store's minimum ($10 at Wegmans), it says so and the order can't be approved.
- [x] Approving the order books it at Instacart with nothing more to do. The window (any of Instacart's windows; they're two hours, starting every hour from 7–9 AM to 9–11 PM, measured 2026-09-28): the latest one that day that ends by the chosen time; if it's full, the one before it, and so on, earlier through the day; if none of those is left, the earliest-ending window the time falls in (for "by 3:00 PM", 2–4 PM; for "by 3:30 PM", 2–4 PM, not 3–5 PM), the one window allowed to end after the chosen time. The review says so ("by 3:00 PM, or 2–4 PM if nothing earlier is left"), and the confirmation shows the window booked.
- [x] When it books: one day after Instacart first offers the chosen day (Instacart shows up to five days after today, so a Friday delivery first shows on Sunday and books on Monday, four days before), at 9:00 AM store time, from a scheduled job; an order approved later than that books right away. Until then it waits to book.
- [x] The scheduled job runs every hour from 9:00 AM to 9:00 PM store time, starting only the orders that are due and still waiting: a run that failed is made up by the next one, and running it again never starts an order twice (decided 2026-10-04). The alarms cover its schedule.
- [x] While it waits to book, the order can be changed, unless the following order already has a draft or has been placed (then only cancelled): opening it goes through the flow again, and it needs approving again (the total is read again); it doesn't book while it's being changed or until it's approved again.
- [x] Choosing the window never needs the user, except when the chosen day has none of those windows left: nothing is ordered, and the user is told and picks another day or time, which books it the same way.
- [x] Booking fills the cart again with the approved products and counts, and just before placing it checks that the cart is those products and counts, the address and card are the ones the final review showed, and the tip follows the tip rule, then clicks once. The window and the total are Instacart's at that moment (prices can change between approving and booking); the confirmation shows the total charged. An order is clicked at most once: a retried job, a second approve, or a scheduled run after it never clicks again.
- [x] Booking never goes without a product that was approved. If one is no longer available, the household's setting decides: use the first of its alternatives that's available (and tell whoever approved it about the swap), or ask (nothing is ordered, and whoever approved it is told and picks an alternative). With none of its alternatives available, it asks either way.
- [x] While it waits to book, the order can be cancelled outright (discarded, like a draft), up to when it books.
- [x] A failure before the click (signed out, the store's minimum not met, the address or card changed, the checkout page changed) orders nothing: the order stays approved, marked "Not booked" with why, and Try again books it the same way.
- [x] An order still waiting to book two hours after its booking time shows as "Not booked" ("It didn't book on Mon, Oct 5 as planned."), with Try again, which books it through the API, not the schedule; until then the next hourly run may still book it. No notification says it's late: one would need our code to be running, and whenever it is, the hourly run books it (decided 2026-10-04).
- [x] A failure after the click with no confirmation marks the order "check Instacart", never "not placed". The worker first looks for it in Instacart's orders; if it can't tell, the order asks "Was Order 4 placed?" with a link to the household's Instacart orders and "It was placed" / "It wasn't placed". Nothing books it again until that's settled.
- [x] Only production places orders; development never orders and doesn't touch the real Instacart cart unless agreed for a test.

## Placing and status

- [x] Placing an order calls the API, which saves the order and its status in the database.
- [x] The call to the grocery service is a stub that does nothing and returns success (until piece 9 part 3, which books at Instacart).
- [x] An order's status is saved and shown; for now it is always "order placed" (piece 9 part 3 adds waiting to book, not booked, and check Instacart).
- [x] After approving, the flow shows a waiting screen while it books ("Booking at Wegmans", about a minute, which can be closed), or, for a day not bookable yet, the day it will book.
- [x] The confirmation after booking shows the Instacart order number, the booked window, the total charged, and "Open in Instacart"; so does the order view.
- [x] Approving counts as ordering for the rest of the app: reminders stop and the next order starts from it, whether it's booked yet or not. While it waits, what the next order takes from it (its target and decide-by dates, and so its reminders) follows it: approving it again with another next delivery moves them, never starting another order, and cancelling it puts reminders back on its own dates, as if it had never been approved.
- [x] An approved order not booked yet is "Waiting to book": the order card's dot track shows it at the Placed dot, not yet checked, until it's booked (then "Order placed"). One that needs a new time says so on its card, and opening it goes to its delivery day and time.

## Calendar and tasks

- [x] The app shows delivery dates, decide-by dates, and the days each order covers, from past orders to 90 days ahead.
- [x] The app tells the user which tasks are due, such as picking meals and placing the next order.

## Notifications

- [x] The app sends push notifications to household members.
- [x] Reminders to order start a set number of days before the decide-by date and go out daily at each member's notification time until the order is placed, including after the decide-by date.
- [x] Reminders go to every member, including while another member is ordering.
- [x] Booking tells whoever approved the order how it went: booked (with the window, and any product swapped for an alternative), needs a new time, needs a product chosen, not booked (and why), or check Instacart. Tapping it opens that order (stage 3).

## Household

- [x] A person is in only one household.
- [x] Settings lists the household's members by name.
- [x] Anyone in a household can add and remove its members: invite someone by email (they join on their first sign-in with it; only an invited email can make an account), withdraw an invitation, or remove anyone, themselves too, but never the last member.

## Settings

Household settings (shared by all members):

- [x] Decide-by offset: days before the target date, default 1.
- [x] Reminder lead: days before the decide-by date that reminders start, default 2.
- [x] Default servings: the servings each picked meal starts at, default 4.
- [x] Delivery time: the latest time deliveries should arrive by, default 6:00 PM; each order's delivery starts at it (stage 3).
- [x] Tip: a percentage of the items' total, default 10%, and a maximum tip, default $15: the tip is the percentage, but never more than the maximum (stage 3).
- [x] A product gone at booking: use an alternative (default), or ask (stage 3).

Personal settings:

- [x] Notification time, in the time zone of the member's phone (detected automatically, never shown or asked for).

## Views

- [x] History shows every past order and all of its meals, grouped by order.
- [x] History's meals can be searched by name and filtered, including by rating.
- [x] Order view: an order's dates, status, meals, and shopping list (from stage 3, its Instacart order too).
- [x] Meal view: a meal's recipe (servings, ingredients, facts).
- [x] Instructions view: a meal's cooking steps.

## Deferred

- Walmart, and ordering for households other than ours (see stage 3).
- Setting up the ChatGPT and Instacart connections from the app (after the MVP; until then they're set up with commands).
- Making shopping faster, once ordering works end to end: find out where its time goes (a 24-item list took 11 minutes with Grok at extra-high reasoning; the current OpenAI runner uses GPT-6.1 Sol at high reasoning).
- Real delivery status updates.
- Handling gaps between orders (for now, reminders go out every day).
- The suggestion agent using ratings.
- Meal icons v2 (generated combinations of icons) and v3 (AI-generated images).
- Adding free-form items to the shopping list (done in the grocery app instead).
- Having suggestions available immediately, such as generating them ahead of time (to discuss; the ready notification may change with it).
- Meal preferences the household can check in settings (well after launch), which fill the suggestion agent's instructions; "low effort to cook" becomes one of them.
- Capturing each job's full cost robustly (the agent's tokens and dollars, the Fargate task, and anything else it uses), stored with the job, so we can estimate what the service would cost to run as a product.
