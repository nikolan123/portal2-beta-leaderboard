import mistune
import nh3
from markupsafe import Markup

_markdown = mistune.create_markdown(escape=True, hard_wrap=True, plugins=["strikethrough", "url"])

def render_bio(text: str) -> Markup:
    return Markup(nh3.clean(
        _markdown(text),
        tags={"p", "br", "a", "strong", "em", "del", "code"},
        attributes={"a": {"href", "title"}},
        url_schemes={"https", "http", "mailto"},
        link_rel="noopener noreferrer nofollow",
    ))
