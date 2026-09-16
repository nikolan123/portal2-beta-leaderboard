from app.bio import render_bio

def test_basic_bio_markdown():
    html = str(render_bio("**bold** *italic* ~~strike~~ [link](https://example.com)\nhttps://example.org"))
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<del>strike</del>" in html
    assert 'href="https://example.com"' in html
    assert 'href="https://example.org"' in html
    assert "<br" in html

def test_bio_blocks_unsafe_html_links_and_images():
    html = str(render_bio('<script>alert(1)</script> <img src=x onerror=alert(1)> [bad](javascript:alert(1)) ![image](https://example.com/img.png)'))
    assert "<script" not in html
    assert "<img" not in html
    assert 'href="javascript:' not in html
    assert "&lt;script&gt;" in html
