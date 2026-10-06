"""Read the current price of a part from its iFixit product page.

Each product page carries a small block of structured data (schema.org "Product")
that states the price for the option the address points at, so a "?variant=..."
address for "Part Only" gives the Part Only price. Only product pages are read,
one at a time with a pause between them, and only when a person asks.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "www.ifixit.com"
PAUSE_SECONDS = 2.0
USER_AGENT = "BenchLog/1 (self-hosted repair log; one price check per part on request)"
_BLOCK = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


class IfixitError(Exception):
    pass


class RateLimited(IfixitError):
    pass


def is_product_url(url):
    """True only for an iFixit store product page, which is the only thing this module reads."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname in (HOST, "ifixit.com")
            and bool(re.match(r"^/products/[A-Za-z0-9._-]+$", parts.path)))


def _download(url, timeout=25):
    """The one place that talks to the network, so tests can replace it."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(3000000).decode("utf-8", "replace")


def parse(html):
    """Pull the price out of a product page. Returns a dict, or raises IfixitError."""
    for match in _BLOCK.finditer(html or ""):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                continue
            try:
                price = round(float(offer.get("price")), 2)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            return {
                "price": price,
                "currency": str(offer.get("priceCurrency") or ""),
                "in_stock": str(offer.get("availability") or "").rsplit("/", 1)[-1] == "InStock",
                "name": str(item.get("name") or ""),
            }
    raise IfixitError("The page loaded, but no price was found on it. iFixit may have changed its page layout.")


def fetch(url):
    """Current price for one product page."""
    if not is_product_url(url):
        raise IfixitError("That is not an iFixit product page address.")
    try:
        html = _download(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited("iFixit asked us to slow down. Try again later.")
        if exc.code == 404:
            raise IfixitError("iFixit no longer has a page at that address.")
        if exc.code in (401, 403):
            raise IfixitError("iFixit refused the request (HTTP %d). It may be blocking automatic checks from this "
                              "device, so open the link and type the price in." % exc.code)
        raise IfixitError("iFixit answered with HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise IfixitError("Could not reach iFixit: %s" % exc)
    return parse(html)


def refresh_all(rows, save, log=print, pause=None, sleep=time.sleep):
    """Check many parts. rows is a list of (part_id, label, url, old_price).

    Each distinct address is read once. save(part_id, result) stores a price.
    Stops early if iFixit says to slow down. Returns a summary dict.
    """
    pause = PAUSE_SECONDS if pause is None else pause
    cache, summary = {}, {"checked": 0, "changed": 0, "failed": 0, "stopped": False}
    first = True
    for part_id, label, url, old_price in rows:
        if url not in cache:
            if not first:
                sleep(pause)
            first = False
            try:
                cache[url] = fetch(url)
            except RateLimited as exc:
                log("Stopped: %s" % exc)
                summary["stopped"] = True
                break
            except IfixitError as exc:
                cache[url] = exc
        result = cache[url]
        if isinstance(result, Exception):
            summary["failed"] += 1
            log("FAILED  %s: %s" % (label, result))
            continue
        summary["checked"] += 1
        save(part_id, result)
        stock = "" if result["in_stock"] else " (out of stock)"
        if old_price is None or abs(old_price - result["price"]) >= 0.005:
            summary["changed"] += 1
            log("CHANGED %s: %s -> %.2f%s" % (label, "none" if old_price is None else "%.2f" % old_price,
                                              result["price"], stock))
        else:
            log("same    %s: %.2f%s" % (label, result["price"], stock))
    log("Done. %d checked, %d changed, %d failed.%s" % (
        summary["checked"], summary["changed"], summary["failed"],
        " Stopped early, run it again later for the rest." if summary["stopped"] else ""))
    return summary
