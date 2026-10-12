# Stores without a product API: Raket.ph, Payhip, Lemon Squeezy

None of these lets a program create a product (Payhip's API covers license keys
and coupons; Lemon Squeezy's lists and reads products; Raket.ph has none that I
found — I couldn't open their docs, so check if that has changed). So for each
new book the publisher writes a ready-to-paste listing:

```
content/raketph/<book>/listing.md + cover.jpg
content/payhip/<book>/listing.md + cover.jpg
content/lemonsqueezy/<book>/listing.md + cover.jpg
```

They are switched on in `config.yml` (they need no keys) and committed by the
workflow like everything else. Your part, per book and store: open the folder,
click Add product, paste name / price / description, upload `cover.jpg`, and
upload the PDF (Payhip, Lemon Squeezy) or paste the Drive link (Raket.ph; set
it to "Anyone with the link can view"). To switch one off, set its `enabled:`
to `false`.

Claude in Chrome can do the pasting for you from these files; see the
prompts we wrote for Raket.ph, which work for the other two with the store
name changed.

The Gumroad price is used as-is; set `price:` under a platform to change it.
