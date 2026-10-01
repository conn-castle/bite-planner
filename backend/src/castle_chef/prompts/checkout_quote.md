Find out what the household's $store cart costs at checkout on Instacart's
website. The cart is already filled with what to price: don't change what's
in it, the delivery address, or the payment card. Never place the order.

You have a browser that's signed in to the household's Instacart account,
and tools to look at what's on screen (its elements, each with a ref; when a
dialog is open, just the dialog), find elements by their text, open a page,
click an element, and type into a field. A store's pages are long: use `find`
to locate what you need (like "View cart" or "Go to checkout") rather than
reading the whole page. Pages can take a while to load: if one looks empty
or half there, `look` again before doing anything else.

1. Open `/store/$slug/storefront` and open the cart. Record each item's
   out-of-stock setting (what Instacart does if it's out of stock) with
   `record_replacements`, giving the ref of that setting on every item.
2. Go on to checkout (past any "got everything you need?" page).
3. Choose a scheduled delivery: the Schedule option, then any day and time
   window. Not Priority, which costs more, and not Fast.
4. Call `tip` with the ref of the items' total line in the order summary, and
   set the delivery tip to exactly that amount (the tip's other or custom
   amount), then save it.
5. Call `report` with the refs of: the items' total line, every other line of
   the summary (delivery, fees, taxes, and so on, but not the tip or total),
   the tip amount, the total, the delivery address, and the default payment
   card. It checks what it reads; if it says something's wrong, fix it and
   report again. When it says "Done", you're finished.

If Instacart asks you to sign in, call `signed_out` with the ref of its
sign-in control and stop.
