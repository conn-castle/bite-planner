# Ordering spec

What the ordering feature must do, and how it should look.

- [`requirements.md`](requirements.md): the functional requirements, as a checklist. Check items off as they're implemented.
- [`mockup.html`](mockup.html): the UI, as a clickable mockup with mock data. Open it in a browser; the panel on the left switches between home states. It is the design reference, not code to reuse. Delete it once the app implements it.

Where the two disagree, the requirements win. Decisions from reviews of the built app also win over the mockup:

- Every row of days is one shared component (`apps/mobile/src/ui/DateBar`), configured with props (e.g. the compact strip on home) but sharing the same parts.
- The delivery date's day row starts at the last day the previous order's meals cover, or at today if that's later; today is shown greyed out and labeled Today.
- In both date steps, the chosen-date card sits below the day row, with its space reserved so choosing a date moves nothing.
- The next-delivery day row marks the days that have meals with a bar in the order's color, starts at the delivery day, and still marks the best day.
- The next-delivery coverage details have one fixed layout (rows, not a sentence) whatever is chosen.
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
- "Allow notifications" comes with Notifications (build step 6).
- Settings has no preview of the next order's dates (the mockup's "With these settings, Order N"): settings hold settings.
- The "By order" / "All meals" switch's track is a tint of ink (8%), not the mockup's `--cc-fill-strong`, which is all but the color of the warm page behind it.

## Build order

One piece at a time, each through the UI with real data:

1. Create-order flow (done).
2. Home (Orders): placed orders as cards, the days they cover in the day strip, and the next order with its target and decide-by dates. Adds the household's decide-by offset (default 1 day); its screen comes with Settings. (done)
3. History and ratings: past orders and all meals with search and filters; thumbs up, thumbs down, and favorite; the prompt to rate last week's meals; and "don't show meals like this" while picking (a household meal preference, like ratings) (done)
4. Settings: household (decide-by offset, reminder lead, default servings, members) and personal (notification time).
5. Dates, meals, and servings on one view: the order flow's step 3 (requirements "Ordering flow"), with each meal's servings stored in the order.
6. Notifications: the daily reminders and "ideas ready", "Allow notifications" in Settings, and the mockup's reminder text on Home and in the order flow (the strip's reminder marks, "Reminders start …, daily · yours at 8:00 AM", the "Reminders on" chip, and "Reminders start" in the next-delivery step). Each member's reminder goes out at their clock time in their phone's zone: a time skipped by a daylight saving change goes out when the clock jumps, a repeated one only once, and a member whose zone isn't known yet (hasn't opened the app) gets none. Needs an APNs key from the Apple Developer site.
7. The rest of the order flow: the order lock, the shopping list (generated in the background, then edited), and the confirmation with the store orders.

## Open questions

- The "ideas ready" notification: only the member who started the generation, or everyone?
- Resuming a draft: back to the step where it was left, or to picking meals (as mocked)?
- Home: should the current order's meals always be visible, even when another card is on top?
