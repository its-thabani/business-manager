"""Business facts that are not configuration.

These are true of StayLit Apparel as it actually operated. They are not
guesses and they are not settings a host environment should override.
"""

from datetime import date

# First Shopify order. Before this the storefront was on Wix, so bank rows
# from that period will not line up with Shopify orders or payouts.
SHOPIFY_LAUNCH_DATE = date(2025, 7, 16)
