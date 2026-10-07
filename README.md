# Refurb restock alert (cloud)

Checks Apple Canada's refurbished store every ~5 minutes on GitHub Actions and sends phone
notifications through [ntfy](https://ntfy.sh) (free app for iPhone/Android).

Watching for:
- 14" MacBook Pro, M5 Pro, standard display, under $3,000 CAD
- iPhone 17 Pro (not Max), 512GB or 1TB

Alerts only fire when Apple's product page says the item is actually buyable, and fire again if
a unit that was in someone's checkout becomes available again. Night alerts (11 PM–7 AM Atlantic) are silent.

The ntfy topic is stored as the repository secret `NTFY_TOPIC`.
To stop: Actions tab → "Refurb restock check" → ⋯ → Disable workflow (or delete the repo).
