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
from urllib.parse import urlparse

import yaml
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
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for label, viewport in (("desktop", {"width": 1440, "height": 1000}),
                                        ("mobile", {"width": 390, "height": 844})):
                    context = browser.new_context(viewport=viewport, permissions=["clipboard-read", "clipboard-write"])
                    # Local corpus plus its declared public font stylesheets/files; no account session or external navigation.
                    font_hosts = {"fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net"}
                    context.route("**/*", lambda route, font_hosts=font_hosts: route.continue_() if (
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
                            assert overflow["scroll"] <= overflow["width"] + 2, f"Horizontal page overflow: {overflow}"
                            assert len(errors) == start_errors, errors[start_errors:]
                            results.append({"viewport": label, "chapter": relative, "checks": "passed"})
                        finally:
                            page.screenshot(path=str(artifacts / f"{label}-{Path(relative).stem}.png"), full_page=True)
                    # Grounded selectors: generated sidebar navigation, copy button, Quarto autocomplete.
                    page.goto(origin + "/index.html", wait_until="networkidle")
                    if label == "mobile":
                        page.locator("button.quarto-btn-toggle").click()
                    link = page.locator('#quarto-sidebar a.sidebar-link[href="./quickstart.html"]')
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
                    result = page.locator("#quarto-search-results a.search-result-link:visible").first
                    expect(result).to_be_visible()
                    destination = result.get_attribute("href")
                    assert destination and not urlparse(destination).netloc, f"Nonlocal search target: {destination}"
                    result.click()
                    page.wait_for_load_state("networkidle")
                    assert page.url.startswith(origin + "/") and ".html" in page.url
                    expect(page.locator("main#quarto-document-content")).to_be_visible()
                    assert not errors, errors
                    page.screenshot(path=str(artifacts / f"{label}-search-destination.png"), full_page=True)
                    results.append({"viewport": label, "controls": ["sidebar-navigation", "copy-exact-text", "search-result-navigation"]})
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    check(args.site.resolve(), args.project.resolve(), args.artifacts.resolve())


if __name__ == "__main__":
    main()
