"""Part search links, and optional live prices from the eBay Browse API.

The price check needs a free eBay developer keyset (App ID and Cert ID).
It only reads public listings. Results are suggestions: listings for small
parts are noisy, so a person always confirms the price before it is saved.
"""

import base64
import json
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"

MARKETPLACES = {
    "EBAY_US": ("United States", "ebay.com"),
    "EBAY_DE": ("Germany", "ebay.de"),
    "EBAY_NL": ("Netherlands", "ebay.nl"),
    "EBAY_GB": ("United Kingdom", "ebay.co.uk"),
    "EBAY_CA": ("Canada", "ebay.ca"),
    "EBAY_AU": ("Australia", "ebay.com.au"),
}

# Device types iFixit sells manufacturer-backed parts for, so its listing is worth checking first.
IFIXIT_OFFICIAL = {"Steam Deck LCD", "Steam Deck OLED"}

# iFixit's parts page for each device type. Used when a part has no product page saved.
IFIXIT_PARTS_PAGES = {
    "DualSense (PS5 controller)": "DualSense",
    "DualShock 4 (PS4 controller)": "DualShock_4",
    "Xbox Series controller": "Xbox_Series_X_Wireless_Controller",
    "Xbox One controller": "Xbox_One_Controller",
    "Joy-Con (L)": "Joy-Con",
    "Joy-Con (R)": "Joy-Con",
    "Joy-Con 2 (L)": "Nintendo_Switch_2_Joy-Con",
    "Joy-Con 2 (R)": "Nintendo_Switch_2_Joy-Con",
    "Switch Pro Controller": "Switch_Pro_Controller",
    "Nintendo Switch (original)": "Nintendo_Switch",
    "Nintendo Switch Lite": "Nintendo_Switch_Lite",
    "Nintendo Switch OLED": "Nintendo_Switch_OLED_Model",
    "Nintendo Switch 2": "Nintendo_Switch_2",
    "Steam Deck LCD": "Steam_Deck",
    "Steam Deck OLED": "Steam_Deck_OLED",
    "PlayStation 5 console": "PlayStation_5",
    "PlayStation 4 console": "PlayStation_4",
}


def ifixit_link(part_name, type_name):
    """iFixit's parts page for the device type, or a search of its site for unknown types."""
    if type_name in IFIXIT_PARTS_PAGES:
        return "https://www.ifixit.com/Parts/" + IFIXIT_PARTS_PAGES[type_name]
    # iFixit's own search address is not dependable, so search its site through Google.
    plain = re.sub(r"\s+", " ", re.sub(r"[()/]", " ", "%s %s" % (type_name or "", part_name or ""))).strip()
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus("site:ifixit.com " + plain)


_token = {"value": "", "expires": 0, "client_id": ""}


class PriceError(Exception):
    pass


def part_query(part_name, part_number, type_name):
    """A search phrase a person would type for this part."""
    clean = lambda text: re.sub(r"\s+", " ", re.sub(r"[()/]", " ", text or "")).strip()
    if part_number:
        # A chip is found by its marking. The first number is enough when two are listed.
        return "%s %s" % (part_number.split("/")[0].strip(), clean(part_name))
    return "%s %s replacement" % (clean(type_name), clean(part_name))


def links(part_name, part_number, type_name, marketplace="EBAY_US", query=None):
    """Search links for a part: general web, shopping results, and eBay buy-it-now."""
    q = urllib.parse.quote_plus(query or part_query(part_name, part_number, type_name))
    domain = MARKETPLACES.get(marketplace, MARKETPLACES["EBAY_US"])[1]
    return {
        "google": "https://www.google.com/search?q=" + q,
        "shopping": "https://www.google.com/search?tbm=shop&q=" + q,
        "ebay": "https://www.%s/sch/i.html?_nkw=%s&LH_BIN=1" % (domain, q),
        "ifixit": ifixit_link(part_name, type_name),
        "ifixit_official": type_name in IFIXIT_OFFICIAL,
    }


def compare(ebay_price, ifixit_price):
    """Describe the gap between an eBay price and an iFixit price in plain words."""
    if ebay_price is None or ifixit_price is None:
        return None
    gap = round(ifixit_price - ebay_price, 2)
    if abs(gap) < 0.005:
        return {"gap": 0.0, "cheaper": "same", "percent": 0}
    # "X% less than the dearer one", so the figure can never exceed 100.
    dearer = max(ebay_price, ifixit_price)
    return {"gap": abs(gap), "cheaper": "ebay" if gap > 0 else "ifixit",
            "percent": round(100.0 * abs(gap) / dearer) if dearer > 0 else None}


def _http(request, timeout=20):
    """The one place that talks to the network, so tests can replace it."""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_token(client_id, client_secret):
    if _token["value"] and _token["client_id"] == client_id and _token["expires"] > time.time() + 60:
        return _token["value"]
    basic = base64.b64encode(("%s:%s" % (client_id, client_secret)).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Authorization": "Basic " + basic})
    try:
        data = _http(request)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise PriceError("eBay rejected the keys. Check the App ID and Cert ID in Settings.")
        raise PriceError("eBay sign-in failed with HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PriceError("Could not reach eBay: %s" % exc)
    if "access_token" not in data:
        raise PriceError("eBay did not return an access token.")
    _token.update(value=data["access_token"], client_id=client_id,
                  expires=time.time() + int(data.get("expires_in", 7200)))
    return _token["value"]


def search(query, client_id, client_secret, marketplace="EBAY_US", limit=25, new_only=True):
    """Return fixed-price listings for a query, cheapest total first."""
    if not client_id or not client_secret:
        raise PriceError("Add your eBay developer keys in Settings first.")
    token = _get_token(client_id, client_secret)
    filters = ["buyingOptions:{FIXED_PRICE}"]
    if new_only:
        filters.append("conditions:{NEW}")
    params = urllib.parse.urlencode({"q": query, "limit": str(limit), "filter": ",".join(filters)})
    request = urllib.request.Request(SEARCH_URL + "?" + params, headers={
        "Authorization": "Bearer " + token, "X-EBAY-C-MARKETPLACE-ID": marketplace,
        "Accept": "application/json"})
    try:
        data = _http(request)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _token["value"] = ""
        raise PriceError("eBay search failed with HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PriceError("Could not reach eBay: %s" % exc)

    items = []
    for raw in data.get("itemSummaries") or []:
        try:
            price = float(raw["price"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        shipping = 0.0
        for option in raw.get("shippingOptions") or []:
            try:
                shipping = float(option["shippingCost"]["value"])
                break
            except (KeyError, TypeError, ValueError):
                continue
        items.append({
            "title": raw.get("title", ""), "price": price, "shipping": shipping, "total": price + shipping,
            "currency": (raw.get("price") or {}).get("currency", ""), "url": raw.get("itemWebUrl", ""),
            "condition": raw.get("condition", ""), "seller": (raw.get("seller") or {}).get("username", ""),
        })
    return sorted(items, key=lambda item: item["total"])


def summarize(items):
    """Low, median, and high total price. The median is the suggestion, since extremes are often junk."""
    totals = [item["total"] for item in items]
    if not totals:
        return None
    return {"count": len(totals), "low": min(totals), "median": statistics.median(totals), "high": max(totals),
            "currency": items[0]["currency"]}
