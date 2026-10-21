"""Check rendered public documentation in an isolated CI Chromium session.

Run after Quarto render. Never uses an existing browser profile or account session.
Screenshots support human visual review; successful checks alone are not visual sign-off.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright


def chapter_paths(project: Path) -> list[str]:
    config = yaml.safe_load(project.read_text(encoding="utf-8"))
    paths: list[str] = []

    def collect(entries: list[object]) -> None:
        for entry in entries:
            if isinstance(entry, str):
                source = Path(entry)
                if source.suffix not in {".md", ".qmd"} or source.is_absolute() or ".." in source.parts:
                    raise ValueError(f"Unsupported chapter: {entry}")
                paths.append(source.with_suffix(".html").as_posix())
            elif isinstance(entry, dict) and isinstance(entry.get("chapters"), list):
                collect(entry["chapters"])
            else:
                raise TypeError(f"Unsupported chapter entry: {entry!r}")

    collect(config["book"]["chapters"])
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Chapter manifest must be nonempty and unique")
    return paths


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def check(site: Path, project: Path, artifacts: Path) -> None:
    paths = chapter_paths(project)
    for relative in paths:
        if not (site / relative).is_file():
            raise ValueError(f"Missing rendered chapter: {relative}")
    artifacts.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    results: list[dict[str, object]] = []
    chapter_failures: list[dict[str, str]] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for label, viewport in (("desktop", {"width": 1440, "height": 1000}),
                                        ("mobile", {"width": 390, "height": 844})):
                    context = browser.new_context(viewport=viewport, permissions=["clipboard-read", "clipboard-write"])
                    # Local corpus plus its declared public font stylesheets/files; no account session or external navigation.
                    font_hosts = {"fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net"}
                    context.route("**/*", lambda route, _request, *, font_hosts=font_hosts: route.continue_() if (
                        route.request.url.startswith(origin + "/") or (
                            urlparse(route.request.url).scheme == "https"
                            and urlparse(route.request.url).hostname in font_hosts
                            and route.request.resource_type in {"stylesheet", "font"}
                        )) else route.abort())
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    page.on("response", lambda response, errors=errors: errors.append(f"HTTP {response.status}: {response.url}")
                            if response.status >= 400 else None)
                    page.on("requestfailed", lambda request, errors=errors, font_hosts=font_hosts: errors.append(f"Failed: {request.url}")
                            if (request.url.startswith(origin + "/") or urlparse(request.url).hostname in font_hosts) else None)
                    for relative in paths:
                        start_errors = len(errors)
                        try:
                            response = page.goto(origin + "/" + relative, wait_until="networkidle")
                            assert response is not None and response.status == 200
                            expect(page.locator("main#quarto-document-content")).to_be_visible()
                            expect(page.locator("h1.title")).to_be_visible()
                            page.evaluate("document.fonts.ready")
                            page.evaluate("Promise.all([...document.images].map(image => image.decode().catch(() => null)))")
                            broken = page.locator("main img").evaluate_all("images => images.filter(image => !image.complete || image.naturalWidth === 0).map(image => image.getAttribute('src'))")
                            assert not broken, f"Broken images: {broken}"
                            overflow = page.evaluate("({width: innerWidth, scroll: document.documentElement.scrollWidth})")
                            if overflow["scroll"] > overflow["width"] + 2:
                                diagnostics = page.locator("main#quarto-document-content *").evaluate_all("""elements => elements.map(element => {
                                    const rectangle = element.getBoundingClientRect();
                                    const style = getComputedStyle(element);
                                    return {tag: element.tagName, class: element.className,
                                        left: rectangle.left, right: rectangle.right, width: rectangle.width,
                                        client: element.clientWidth, scroll: element.scrollWidth,
                                        whiteSpace: style.whiteSpace, overflowX: style.overflowX,
                                        overflowWrap: style.overflowWrap};
                                }).filter(element => element.right > innerWidth + 2).slice(0, 20)""")
                                (artifacts / f"{label}-{Path(relative).stem}-overflow.json").write_text(
                                    json.dumps({"page": overflow, "elements": diagnostics}, indent=2), encoding="utf-8")
                            assert overflow["scroll"] <= overflow["width"] + 2, f"Horizontal page overflow: {overflow}"
                            if label == "mobile":
                                tables = page.locator("main#quarto-document-content table")
                                if relative == "configuration.html":
                                    first_table = tables.first
                                    assert first_table.locator("thead th").count() == 6
                                    assert first_table.evaluate("element => element.scrollWidth > element.clientWidth + 2"), "Six-column mobile table must preserve readable cell widths and actually scroll"
                                for table_index in range(tables.count()):
                                    table = tables.nth(table_index)
                                    metrics = table.evaluate("element => ({client: element.clientWidth, scroll: element.scrollWidth})")
                                    if metrics["scroll"] <= metrics["client"] + 2:
                                        continue
                                    scrolling = table.evaluate("""async element => {
                                        const overflow = getComputedStyle(element).overflowX;
                                        element.scrollLeft = 0;
                                        const start = element.scrollLeft;
                                        element.scrollLeft = element.scrollWidth;
                                        await new Promise(requestAnimationFrame);
                                        const end = element.scrollLeft;
                                        const rectangle = element.getBoundingClientRect();
                                        const lastCell = element.querySelector('tr')?.lastElementChild;
                                        const last = lastCell?.getBoundingClientRect();
                                        return {overflow, start, end,
                                            maximum: element.scrollWidth - element.clientWidth,
                                            clipLeft: rectangle.left + element.clientLeft,
                                            clipRight: rectangle.left + element.clientLeft + element.clientWidth,
                                            lastRight: last?.right};
                                    }""")
                                    assert scrolling["overflow"] in {"auto", "scroll"}, scrolling
                                    assert abs(scrolling["start"]) <= 2, scrolling
                                    assert scrolling["end"] > 0 and abs(scrolling["end"] - scrolling["maximum"]) <= 2, scrolling
                                    assert scrolling["lastRight"] is not None and scrolling["clipLeft"] < scrolling["lastRight"] <= scrolling["clipRight"] + 2, scrolling
                                    if relative == "configuration.html" and table_index == 0:
                                        table.screenshot(path=str(artifacts / "mobile-configuration-first-table-end.png"))
                                    table.evaluate("element => { element.scrollLeft = 0; }")
                                    assert abs(table.evaluate("element => element.scrollLeft")) <= 2
                                    results.append({"viewport": label, "chapter": relative,
                                                    "table_index": table_index, "horizontal_scroll": scrolling})
                            assert len(errors) == start_errors, errors[start_errors:]
                            results.append({"viewport": label, "chapter": relative, "checks": "passed"})
                        except AssertionError as error:
                            failure = {"viewport": label, "chapter": relative, "message": str(error)}
                            chapter_failures.append(failure)
                            results.append({**failure, "checks": "failed"})
                        finally:
                            page.screenshot(path=str(artifacts / f"{label}-{Path(relative).stem}.png"), full_page=True)
                    # Grounded selectors: generated sidebar navigation, copy button, Quarto autocomplete.
                    try:
                        page.goto(origin + "/index.html", wait_until="networkidle")
                        if label == "mobile":
                            page.locator("button.quarto-btn-toggle").click()
                        link = page.locator('#quarto-sidebar a.sidebar-link[href$="/quickstart.html"]')
                        expect(link).to_be_visible()
                        link.click()
                        expect(page).to_have_url(origin + "/quickstart.html")
                        expect(page.locator("main#quarto-document-content")).to_be_visible()
                        button = page.locator("main .code-copy-button").first
                        expected_text = button.locator("..").locator("code").inner_text().strip()
                        assert expected_text, "Copy fixture code is empty"
                        button.click()
                        expect(button).to_have_class(re.compile(r".*code-copy-button-checked.*"))
                        copied = page.evaluate("navigator.clipboard.readText()")
                        assert copied.strip() == expected_text, "Copy control did not copy the displayed code"
                        if label == "mobile":
                            page.locator("button.quarto-search-button").click()
                        search = page.locator(".aa-Form .aa-Input:visible").first
                        expect(search).to_be_visible()
                        search.fill("reconcile")
                        # 좁은 화면에서는 생성된 검색 UI가 별도 컨테이너로 이동한다.
                        search_panel = ".aa-DetachedContainer" if label == "mobile" else "#quarto-search-results"
                        result = page.locator(search_panel + " a.search-result-link:visible").first
                        expect(result).to_be_visible()
                        destination = result.get_attribute("href")
                        assert destination, "Search result has no destination"
                        resolved = urlparse(urljoin(origin + "/", destination))
                        assert (resolved.scheme, resolved.netloc) == ("http", urlparse(origin).netloc), f"Nonlocal search target: {destination}"
                        result.click()
                        page.wait_for_load_state("networkidle")
                        assert page.url.startswith(origin + "/") and ".html" in page.url
                        expect(page.locator("main#quarto-document-content")).to_be_visible()
                        assert not errors, errors
                        page.screenshot(path=str(artifacts / f"{label}-search-destination.png"), full_page=True)
                        results.append({"viewport": label, "controls": ["sidebar-navigation", "copy-exact-text", "search-result-navigation"]})
                    except (AssertionError, PlaywrightTimeoutError) as error:
                        diagnostic: dict[str, object] = {"viewport": label, "error": str(error), "page_errors": list(errors)}
                        try:
                            diagnostic["dom"] = page.locator(".aa-Input, .aa-Panel, .aa-DetachedOverlay, .aa-DetachedContainer, #quarto-search-results, a.search-result-link, button.quarto-search-button").evaluate_all("""elements => elements.slice(0, 100).map(element => {
                                const rectangle = element.getBoundingClientRect();
                                const style = getComputedStyle(element);
                                return {tag: element.tagName, class: element.className,
                                    id: element.id, value: element.value ?? null,
                                    href: element.getAttribute('href'),
                                    left: rectangle.left, top: rectangle.top,
                                    width: rectangle.width, height: rectangle.height,
                                    display: style.display, visibility: style.visibility,
                                    ancestors: (() => {
                                        const parents = [];
                                        let parent = element.parentElement;
                                        while (parent && parents.length < 5) {
                                            parents.push({tag: parent.tagName, class: parent.className, id: parent.id});
                                            parent = parent.parentElement;
                                        }
                                        return parents;
                                    })()};
                            })""")
                            diagnostic["aria"] = page.locator("body").aria_snapshot()
                            page.screenshot(path=str(artifacts / f"{label}-controls-failure.png"), full_page=True)
                        except (PlaywrightError, OSError) as diagnostic_error:
                            diagnostic["capture_error"] = type(diagnostic_error).__name__
                        (artifacts / f"{label}-controls-failure.json").write_text(
                            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
                        raise
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        (artifacts / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if worker.is_alive():
        raise RuntimeError("Documentation server did not stop")
    if chapter_failures:
        raise AssertionError(f"Documentation chapter checks failed: {chapter_failures}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    check(args.site.resolve(), args.project.resolve(), args.artifacts.resolve())


if __name__ == "__main__":
    main()
