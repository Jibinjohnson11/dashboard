"""Server-side allowlist sanitisation for rich-text content."""
import html
import re

import nh3

ALLOWED_TAGS = {
    "p", "br", "h2", "h3", "h4", "strong", "b", "em", "i", "u",
    "ul", "ol", "li", "a", "blockquote",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(value):
    if not value:
        return ""
    return nh3.clean(
        str(value),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    ).strip()


def html_to_text(value):
    text = nh3.clean(re.sub(r"<(br|/p|/h\d|/li|/blockquote)[^>]*>", " ", str(value or ""), flags=re.I), tags=set())
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def word_count(value):
    text = html_to_text(value)
    return len(text.split()) if text else 0
