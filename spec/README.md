# Ordering spec

What the ordering feature must do, and how it should look.

- [`requirements.md`](requirements.md): the functional requirements, as a checklist. Check items off as they're implemented.
- [`mockup.html`](mockup.html): the UI, as a clickable mockup with mock data. Open it in a browser; the panel on the left switches between home states. It is the design reference, not code to reuse. Delete it once the app implements it.

Where the two disagree, the requirements win.

The mockup predates two requirements: it has no meal ratings (thumbs up, thumbs down, favorite, and the prompt to rate last week's meals), and it shows food illustrations where the app shows an icon.

## Open questions

- Can a meal be both a favorite and thumbs down?
- The "ideas ready" notification: only the member who started the generation, or everyone?
- Discarding a draft: its "don't show meals like this" choices are household preferences, so they stay. Confirm.
- Resuming a draft: back to the step where it was left, or to picking meals (as mocked)?
- Home: should the current order's meals always be visible, even when another card is on top?
