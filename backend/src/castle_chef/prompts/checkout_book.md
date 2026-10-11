Book the household's $store order on Instacart's website, for delivery on
$day by $by. The cart is already filled with exactly what to order: don't
change what's in it or the payment card. The approved delivery address is
$address. If the website has switched away, select that saved address again;
never add an address or choose another.

You have a browser that's signed in to the household's Instacart account,
and tools to look at what's on screen (its elements, each with a ref; when a
dialog is open, just the dialog), find elements by their text, open a page,
click an element, and type into a field. A store's pages are long: use `find`
to locate what you need (like "View cart" or "Go to checkout") rather than
reading the whole page. Pages can take a while to load: if one looks empty
or half there, `look` again before doing anything else. The click tool never
presses Place order: only `place_order` does, once.

1. Open `/store/$slug/storefront`, open the cart, and go on to checkout (past
   any "got everything you need?" page).
2. Choose a scheduled delivery: the Schedule option (not Priority, which
   costs more, and not Fast). Click the tab for $day, then call
   `choose_window` with that tab's ref and the ref of every time window it
   offers. It says which window to click; click it and save the choice. If it
   says none works, stop.
3. Call `tip` with the ref of the items' total line in the order summary, and
   set the delivery tip to exactly that amount (the tip's other or custom
   amount), then save it.
4. Call `report` with the refs of: the items' total line, every other line of
   the summary (delivery, fees, taxes, and so on, but not the tip or total),
   the tip amount, the total, the delivery option chosen (showing its window),
   the delivery address, and the default payment card. It checks what it
   reads; if it says something's wrong, fix it and report again. If it says
   to stop, stop.
5. When it says "Done", call `place_order` with the ref of the Place order
   button, and do what it says next. After the order is placed, find
   Instacart's confirmation and call `confirmed` with the refs of its order
   number, delivery window, and total. If no confirmation shows, look in the
   account's orders (its menu's "Your orders") for this order (its day,
   window, and total); if it's there, call `confirmed` with its refs; if not,
   call `not_confirmed`.

If Instacart asks you to sign in before you've placed the order, call
`signed_out` with the ref of its sign-in control and stop.
