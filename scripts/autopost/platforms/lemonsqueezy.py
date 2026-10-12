"""Lemon Squeezy: its API lists and reads products but does not create them."""

from .manual import ManualListing


class LemonSqueezy(ManualListing):
    name = "lemonsqueezy"
    label = "Lemon Squeezy"
    hosts_file = True
