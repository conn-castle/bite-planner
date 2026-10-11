Replies from Instacart's tools (`codex app-server`'s `mcpServer/tool/call`), recorded
2026-09-27 for the store Wegmans. Removed: addresses and account fields, every
`_meta` entry but the widget data (the widget's environment holds a token), the
refusal's `error_data` (it held the household's location), and the cart's id
(replaced with 1). `cart-with-items.json` is the recorded empty cart holding two
recorded search products (quantities and subtotal set to match), since a cart with
items wasn't recorded.

`addresses.json` uses the saved-address reply shape inspected on 2026-10-11,
with synthetic IDs and locations. It contains no household addresses or tokens.
