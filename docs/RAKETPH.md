# Raket.ph

I found no public API for Raket.ph, only its dashboard, so this platform can't
list anything by itself. Turn it on (`platforms.raketph.enabled: true` in
`config.yml`) and each book gets `content/raketph/<slug>/listing.md` plus
`cover.jpg`, committed by the workflow. Open it, add a product on Raket.ph and
paste the name, price, description, image and file link across.

- The file link is a Google Drive share link. Set the file to "Anyone with the
  link can view", or upload the PDF to Raket.ph directly.
- `price:` and `currency:` under `platforms.raketph` change what is shown.
  Otherwise the Gumroad price is used (as a number, not converted to PHP).
- Raket.ph's fees, categories and rules for digital products weren't checked.
  If you find an API or import, tell me and I'll make it automatic.
