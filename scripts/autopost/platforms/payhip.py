"""Payhip: its API covers license keys and coupons, not creating products."""

from .manual import ManualListing


class Payhip(ManualListing):
    name = "payhip"
    label = "Payhip"
    hosts_file = True
