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

The mockup predates two requirements: it has no meal ratings (thumbs up, thumbs down, favorite, and the prompt to rate last week's meals), and it shows food illustrations where the app shows an icon.

## Build order

One piece at a time, each through the UI with real data:

1. Create-order flow (done).
2. Home (Orders): placed orders as cards, the days they cover in the day strip, and the next order with its target and decide-by dates. Adds the household's decide-by offset (default 1 day); its screen comes with Settings.
3. History and ratings: past orders and all meals with search and filters; thumbs up, thumbs down, and favorite; the prompt to rate last week's meals; and "don't show meals like this" while picking (a household meal preference, like ratings).
4. Settings: household (decide-by offset, reminder lead) and personal (notification time).
5. Notifications: the daily reminders and "ideas ready". Needs an APNs key from the Apple Developer site.
6. The rest of the order flow: the order lock and the shopping list step.

## Open questions

- Can a meal be both a favorite and thumbs down?
- The "ideas ready" notification: only the member who started the generation, or everyone?
- Discarding a draft: its "don't show meals like this" choices are household preferences, so they stay. Confirm.
- Resuming a draft: back to the step where it was left, or to picking meals (as mocked)?
- Home: should the current order's meals always be visible, even when another card is on top?
