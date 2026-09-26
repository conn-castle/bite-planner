# Ordering spec

What the ordering feature must do, and how it should look.

- [`requirements.md`](requirements.md): the functional requirements, as a checklist. Check items off as they're implemented.
- [`mockup.html`](mockup.html): the UI, as a clickable mockup with mock data. Open it in a browser; the panel on the left switches between home states. It is the design reference, not code to reuse. Delete it once the app implements it.

Where the two disagree, the requirements win. Decisions from reviews of the built app also win over the mockup:

- Every row of days is one shared component (`apps/mobile/src/ui/DateBar`), configured with props (e.g. the compact strip on home) but sharing the same parts.
- The delivery date's day row starts at the last day the previous order's meals cover, or at today if that's later; today is shown greyed out and labeled Today.
- The flow's steps each do one thing: Pick meals, Servings, Delivery date, Next delivery, Shopping list (waiting for it if needed, then reviewing it), and a final review that places the order. This replaces the single "Dates and servings" step decided earlier (built in step 5 of the build order), and adds Servings and the final review to the mockup's steps. The shopping list starts building as the member leaves Servings.
- The next-delivery day row marks the days that have meals with a bar in the order's color, starts at the delivery day, and still marks the best day.
- Placed order cards (home and the order view) have a 1px border in the order's color at 40% (`color-mix(in srgb, var(--c) 40%, transparent)`), not the mockup's neutral hairline, so each card matches its bar in the day strip.
- The bar of an order not delivered yet is solid, in a lighter tint of the order's color (45% of it on white), not dashed as in the mockup.
- While meal ideas are being made, the draft card shows how long it has taken ("Working for 0:42") instead of the mockup's made-up estimate ("About 1 min left").
- The draft card doesn't credit picks to a person ("2 meals added", not "Sam has added 2 meals"): anyone in the household can pick, and the order records only who started it.
- A placed card's last track node (its days) shows no progress ring before the first day; the mockup's empty ring still draws its round line end as a dot.

- A meal's rating is one of four exclusive values, worst to best: "not for us" (the ✕ while picking), thumbs down, thumbs up, and favorite (a favorite is always liked, so it can't be thumbs down). No rating is the fifth state. Discarding a draft keeps its "not for us" marks.
- While picking, a suggestion has only the ✕. Thumbs and the heart are for meals of placed orders.

The mockup predates two requirements: it has no meal ratings (thumbs up, thumbs down, favorite, and the prompt to rate last week's meals), and it shows food illustrations where the app shows an icon. Their design was chosen in review:

- A meal's page has one round button at the end of its toolbar: "?" when not rated, otherwise the rating. Tapped, it grows sideways into the three choices (thumbs down, thumbs up, heart); tapping the chosen one clears it.
- The prompt to rate last week's meals is on the "Finding meals" step while suggestions generate: one card per order, in the look of History's order cards, titled "How were these meals?", with the three choices beside each meal.
- History has a search field and filter chips (All, Favorites, Liked, Didn't like, Not rated) under the "By order" / "All meals" switch, and each meal shows its rating as a small mark.

Settings, as reviewed:

- The mockup's Dislikes row isn't built, and won't be: ratings and "not for us" cover it.
- Members is a read-only row listing everyone in the household ("You, Sam"), with no chevron; adding and removing members isn't scheduled yet.
- Default servings is a stepper (1–12), like the decide-by and reminder days, not the mockup's placeholder row.
- The Household group starts with the household's Name; Account has Your name and Email. A name changes in a small dialog.
- Settings has no preview of the next order's dates (the mockup's "With these settings, Order N"): settings hold settings.
- The "By order" / "All meals" switch's track is a tint of ink (8%), not the mockup's `--cc-fill-strong`, which is all but the color of the warm page behind it.

Notifications, as decided:

- "Your meal ideas are ready" goes only to the member who started the batch.
- Tapping a notification opens the order flow only when the household's draft is the one it's about (a reminder's order, or the draft whose ideas are ready); otherwise it opens Home with an alert saying why (already placed, not started yet, or the draft was discarded), and Home's top card has the next step. A tap never starts a draft (the mockup opens the flow either way).
- In the app, iOS shows no banner; "ideas ready" shows as the mockup's toast unless the order flow is open. Reminders show nothing extra (Home already shows the order's state).
- Settings' Personal group has a "Daily reminders" switch (each member's own, kept by the server; on by default): off, that member gets no daily reminders. "Your meal ideas are ready" has no switch: it goes only to whoever asked for the ideas. While iOS doesn't allow Castle Chef's notifications on the phone, the group starts with a row saying so and a button: Allow (iOS's prompt, the first time) or Open Settings (after that). The mockup's "Allow notifications: On" row isn't built.
- Where the mockup says "yours at 8:00 AM", a member whose reminders are off (the switch, or iOS) sees "yours are off" (the draft card: "your reminders are off").
- The Next delivery step's card has no mini strip of days (the mockup's `.mini-strip`): the day row under it already marks the days the meals cover.
- The Next delivery step says what it sets for the following order in one line ("Order 5: decide by …, reminders start …"), not the mockup's "What this sets" group.

The order lock, as decided:

- The lock belongs to one opening of the order flow on one phone, not just to the member. Opening the flow on another phone (or on the same phone after the app was closed) takes over the member's own lock at once; the phone that lost it shows "You're placing this order on another phone" with "Order here" (not in the mockup).
- While the locked screen is open, it turns into the draft by itself once the other member closes the flow or their app goes away for 2 minutes.
- Discarding the draft is refused while another member is ordering (the locked card has no Discard, as mocked).
- The locked card and screen show the app's steps, not the mockup's four, and the card says what they're doing at that step ("Sam is choosing the dates and servings"; with the one-thing-per-step flow, each step's own words).

## Build order

One piece at a time, each through the UI with real data:

1. Create-order flow (done).
2. Home (Orders): placed orders as cards, the days they cover in the day strip, and the next order with its target and decide-by dates. Adds the household's decide-by offset (default 1 day); its screen comes with Settings. (done)
3. History and ratings: past orders and all meals with search and filters; thumbs up, thumbs down, and favorite; the prompt to rate last week's meals; and "don't show meals like this" while picking (a household meal preference, like ratings) (done)
4. Settings: household (decide-by offset, reminder lead, default servings, members, name) and personal (notification time, your name). (done)
5. Dates, meals, and servings on one view: the order flow's step 3 (requirements "Ordering flow"), with each meal's servings stored in the order. (done)
6. Notifications: the daily reminders and "ideas ready", "Allow notifications" in Settings, and the mockup's reminder text on Home and in the order flow (the strip's reminder marks, "Reminders start …, daily · yours at 8:00 AM", the "Reminders on" chip, and "Reminders start" in the next-delivery step). Each member's reminder goes out at their clock time in their phone's zone: a time skipped by a daylight saving change has no reminder that day (EventBridge Scheduler skips it), a repeated one goes out once, and a member whose zone isn't known yet (hasn't opened the app) gets none. (done)
7. The order lock: one member ordering at a time (requirements "Order lock"), with the mockup's "Sam is placing an order" card and locked flow screen. (done)
8. The one-thing-per-step flow and the shopping list, stages 1 and 2 (requirements "Ordering flow" and "Shopping list"): Servings, Delivery date, and Next delivery become steps of their own, followed by Shopping list and a final review that places the order. Leaving Servings starts the list: Python scales each picked meal's ingredients to its servings, then the agent combines them into one list grouped by aisle, in the background like meal ideas, leaving out the items assumed on hand (shown only behind an icon). Changing the meals or servings afterwards rebuilds it from scratch. The user removes items and changes quantities; the order view shows the list. Placing still calls the stub grocery service.
9. Grocery stores, stage 3: first research Instacart and Walmart (APIs, accounts, sign-in, preferences) and choose one or both; then an agent shops the list and chooses the exact products, the user sees them before placing, and the confirmation shows the store orders.
10. Household members from the app: adding and removing people (requirements "Household"). Needs its own design first: how someone is invited and joins (today the admin command creates their Neon Auth account and emailed codes sign them in), what happens to a person who leaves, and whether a household can end up empty.

## Open questions

- Resuming a draft: back to the step where it was left, or to picking meals (as mocked)?
- Home: should the current order's meals always be visible, even when another card is on top?
