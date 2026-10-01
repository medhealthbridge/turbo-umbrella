"""Autopost — watch a Drive folder, publish books, prepare the marketing.

The pieces, in the order a run uses them:

    config    load config.yml (+ env overrides)
    sources   find new PDFs in Google Drive, download them
    metadata  turn a filename into a catalog entry
    marketing write the listing copy, SEO and social captions
    images    render cover / thumbnail / pin / social from page 1
    platforms push the listing to Gumroad (and friends)
    social    write the ready-to-post packs
    state     remember what happened, so nothing is ever posted twice
    dashboard rebuild the status page
"""

__version__ = "2.0.0"
