import asyncio
import shutil
import time
import urllib.parse
from pathlib import Path

import pydoll
import requests
from bs4 import BeautifulSoup
from pydoll.browser.chrome import Chrome
from pydoll.browser.options import Options
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from websockets.exceptions import ConnectionClosedError

# TODO: Currently doesn't catch or display errors

console = Console()

ROOT = "https://stats.ncaa.org/rankings/change_sport_year_div"

usedNames = set()


def GetAllHref(htmlPath: Path | str) -> list[str]:
    htmlPath = Path(htmlPath)
    content = htmlPath.read_text()
    soup = BeautifulSoup(content, "html.parser")
    hrefTags = soup.find_all("a", href=True)
    hrefs = [tag["href"] for tag in hrefTags]
    return hrefs


def GetSelfReferences(hrefTags: list[str]) -> list[str]:
    selfReferences = []
    for tag in hrefTags:
        if tag.startswith("/"):
            selfReferences.append(tag)
    return selfReferences


def GetUniqueFilename(url: str, extension: str) -> str:
    """
    Generates a unique filename from the URL path by progressively including preceding parts if needed.

    Parameters
    ----------
    url : str
        The URL to generate the filename from.
    extension : str
        File extension to append (e.g., '.html' or '.png').

    Returns
    -------
    str
        A unique filename with the given extension.
    """
    global usedNames
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        candidate = "index"
    else:
        candidate = parts[-1]
    i = len(parts) - 2
    while candidate in usedNames:
        if i >= 0:
            candidate = parts[i] + "-" + candidate
            i -= 1
        else:
            candidate = candidate + "-dup"
    usedNames.add(candidate)
    return candidate + extension


async def Scrape(urls: str | list[str], outputDir: str | None = None) -> None:
    """
    Navigates to a URL or a list of URLs, saves the page content and screenshot,
    then (optionally) clicks a button via JavaScript.

    Parameters
    ----------
    urls : str or list[str]
        The URL or list of URLs to scrape.
    """
    global usedNames

    # Ensure urls is a list
    if isinstance(urls, str):
        urls = [urls]

    options = Options()

    # Set a common, non-headless User-Agent to reduce basic detection
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/111.0.0.0 Safari/537.36"
    )

    # Remove or comment out the headless argument:
    options.add_argument("--headless")

    try:
        async with Chrome(options=options) as browser:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}", justify="left"),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                BarColumn(bar_width=None),
                MofNCompleteColumn(),
                TextColumn("•"),
                TimeElapsedColumn(),
                TextColumn("•"),
                TimeRemainingColumn(),
                expand=True,
            ) as progress:

                parsed = urllib.parse.urlparse(ROOT)
                domain = (parsed.netloc if parsed.netloc else ROOT).replace(".", "-")

                baseDir = (
                    Path(outputDir)
                    if outputDir is not None
                    else Path(f"scraped-{domain}")
                )

                if baseDir.exists():

                    deletionTask = progress.add_task(
                        f"[bold red]Deleting {baseDir}[/bold red]", total=1
                    )

                    shutil.rmtree(baseDir)

                    time.sleep(1)

                    progress.update(
                        deletionTask,
                        description=f"[bold red]Deleting {baseDir}[/bold red]",
                        advance=1,
                        refresh=True,
                    )

                    progress.remove_task(deletionTask)

                baseDir.mkdir(exist_ok=True, parents=True)

                startBrowserTask = progress.add_task("Starting Browser", total=1)
                await browser.start()
                progress.update(startBrowserTask, advance=1, refresh=True)
                progress.remove_task(startBrowserTask)

                getPageTask = progress.add_task("Getting Page", total=1)
                page = await browser.get_page()
                progress.update(getPageTask, advance=1, refresh=True)
                progress.remove_task(getPageTask)

                task = progress.add_task("Scraping URLs", total=len(urls))

                for url in urls:
                    progress.update(task, description=f"Scraping {url}", refresh=True)

                    parsed = urllib.parse.urlparse(url)
                    domain = (parsed.netloc if parsed.netloc else ROOT).replace(
                        ".", "-"
                    )

                    htmlDir = baseDir / "html"
                    screenshotDir = baseDir / "screenshots"
                    htmlDir.mkdir(parents=True, exist_ok=True)
                    screenshotDir.mkdir(parents=True, exist_ok=True)

                    await page.go_to(url)
                    await page.wait_element(pydoll.constants.By.CSS_SELECTOR, "body")

                    # Save page content
                    content = await page.execute_script(
                        "document.documentElement.outerHTML"
                    )
                    if isinstance(content, str):
                        htmlContent = content
                    elif isinstance(content, dict):
                        htmlContent = content.get("result", "")
                        if isinstance(htmlContent, dict) and "value" in htmlContent:
                            htmlContent = htmlContent["value"]
                        if not isinstance(htmlContent, str):
                            htmlContent = str(htmlContent)
                    else:
                        htmlContent = str(content)

                    contentFilename = htmlDir / GetUniqueFilename(url, ".html")
                    screenshotFilename = screenshotDir / GetUniqueFilename(url, ".png")

                    Path(contentFilename).write_text(htmlContent, encoding="utf-8")

                    try:
                        await page.get_screenshot(path=str(screenshotFilename))
                    except ConnectionClosedError:
                        pass

                    # Example: Click a button if it exists
                    # (Comment out if not needed)
                    try:
                        await page.wait_element(
                            pydoll.constants.By.CSS_SELECTOR,
                            "button",
                            timeout=3,
                            raise_exc=False,
                        )
                        await page.execute_script(
                            "document.querySelector('button').click();"
                        )
                        await asyncio.sleep(1)  # Delay to let the page update
                    except Exception:
                        pass

                    progress.update(
                        task, description=f"Scraped {url}", advance=1, refresh=True
                    )

                progress.update(task, description="Scraping complete", refresh=True)

                await asyncio.sleep(0.1)
                try:
                    await browser.stop()
                except ConnectionClosedError:
                    pass
    except ConnectionClosedError:
        pass


if __name__ == "__main__":
    asyncio.run(Scrape(ROOT))
