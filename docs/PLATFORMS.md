# Adding a platform

Everything a platform needs is already done by the time it is called: the
schedule has fired, the PDF is downloaded, the page count is read, the copy
is written and the cover art is rendered. A platform receives a finished
`book` and returns what happened.

## The shape of it

```python
# scripts/autopost/platforms/payhip.py
from .base import Platform, Result


class Payhip(Platform):
    name = "payhip"                          # the key in config.yml
    label = "Payhip"                         # shown in logs and the dashboard
    required_secrets = ("PAYHIP_API_KEY",)   # checked before anything downloads

    def publish(self, book, pdf_path):
        listing = book.listing               # title, summary, description_html,
                                             # tags, receipt, refund_period, price
        cover = book.images.get("cover")     # a Path, or None

        if self.dry_run:
            return Result("dry-run")

        # ... create or update the product ...
        return Result("published", product_id="abc123", url="https://payhip.com/b/abc123")
```

Then register it:

```python
# scripts/autopost/platforms/__init__.py
from .payhip import Payhip

REGISTRY = {p.name: p for p in (Gumroad, Payhip, LemonSqueezy, Etsy)}
```

and switch it on:

```yaml
# config.yml
platforms:
  payhip:
    enabled: true
```

## What you get

`book.listing` is a dict:

| Key | |
|---|---|
| `title` | SEO title, page count corrected against the PDF |
| `summary` | one sentence, under the configured limit |
| `description_html` | the full body: hook, what-you-get, guarantees, perfect-for, how-to-print |
| `tags` | deduplicated, capped at `marketing.max_tags` |
| `receipt` | the purchase-receipt message |
| `refund_period` | as configured |
| `price` | a string, from the pricing rules or `books.json` |
| `pages` | the real page count |
| `social` | `{network: {caption, hashtags, text, image}}` |

`book.images` is `{"cover": Path, "thumbnail": Path, "pin": Path, "social": Path}`.
Any of them may be missing if the PDF could not be rendered — check before
using one.

## The rules

**Be idempotent.** A run can die at any point and the next one will call you
again with the same book. Look the listing up by name (or by the id in
`state/published.json`) and update it rather than creating a second one.
`Gumroad.find_existing` is the reference.

**Raise `log.AutopostError` for anything the user can fix**, with the fix in
the message. It is caught per-platform, so one broken platform does not stop
the others, and the message lands on the dashboard.

**Respect `self.dry_run`.** Return `Result("dry-run")` without any write.

**Return a real `Result`.** `status` is one of `published`, `draft`,
`failed`, `skipped`, `dry-run`. The `url` is what the social packs link to.

## Testing it

`tests/smoke.py` runs the whole pipeline against a stand-in CLI with no
network. Copy the `FAKE_CLI` pattern for your platform, and make it refuse
the things the real API refuses — the Gumroad stand-in rejects a product with
no cover, which is exactly the failure that stalled the first production run.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python tests/smoke.py
```

## Social networks

Same idea, in `scripts/autopost/social.py`. The captions and correctly-sized
images are already written to `content/social/<slug>/` — an integration only
has to post them.

Worth knowing before you start: Instagram and TikTok need a Business account
and an app that the platform reviews by hand; Pinterest needs a business
account and a reviewed app too. Expect the approval, not the code, to be the
work. A scheduler like Buffer or Publer with a simpler API is often the
faster route.
