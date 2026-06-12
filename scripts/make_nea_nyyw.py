import asyncio
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from playwright.async_api import async_playwright


URL = "http://www.nea.gov.cn/xwzx/nyyw.htm"
BASE = "http://www.nea.gov.cn"

output_dir = Path("docs")
output_dir.mkdir(parents=True, exist_ok=True)
output_file = output_dir / "nea_nyyw.xml"


def parse_date(date_text):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def extract_items_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    lis = soup.select("#showData0 li")

    results = []

    for li in lis:
        a = li.find("a")
        date_span = li.find("span", class_="sj")

        if not a:
            continue

        title = a.get_text(" ", strip=True)
        href = a.get("href")
        date_text = date_span.get_text(strip=True) if date_span else ""

        if not title or not href:
            continue

        link = urljoin(BASE, href)

        results.append({
            "title": title,
            "link": link,
            "date": date_text,
        })

    return results


async def main():
    all_items = []
    seen_links = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--allow-running-insecure-content",
                "--disable-web-security",
            ]
        )

        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_selector("#showData0 li", timeout=60000)

        for page_no in range(1, 6):
            print(f"正在抓取第 {page_no} 页...")

            await page.wait_for_selector("#showData0 li", timeout=60000)
            await page.wait_for_timeout(1500)

            html = await page.content()
            current_items = extract_items_from_html(html)

            print(f"第 {page_no} 页抓到 {len(current_items)} 条")

            for item in current_items:
                if item["link"] not in seen_links:
                    all_items.append(item)
                    seen_links.add(item["link"])

            if page_no < 5:
                pager = page.locator("#page_navigation")
                next_page_no = str(page_no + 1)

                try:
                    await pager.get_by_text(next_page_no, exact=True).click(timeout=10000)
                except Exception:
                    await pager.get_by_text("下一页").click(timeout=10000)

                await page.wait_for_timeout(2000)

        await browser.close()

    print(f"合计抓到 {len(all_items)} 条去重新闻")

    fg = FeedGenerator()
    fg.title("国家能源局-能源要闻")
    fg.link(href=URL, rel="alternate")
    fg.description("国家能源局能源要闻栏目自动生成 RSS")
    fg.language("zh-CN")
    fg.lastBuildDate(datetime.now(timezone.utc))

    def sort_key(item):
        dt = parse_date(item["date"])
        return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)

    all_items = sorted(all_items, key=sort_key, reverse=True)

    item_count = 0

    for item in all_items:
        fe = fg.add_entry()
        fe.title(item["title"])
        fe.link(href=item["link"])
        fe.description(f"{item['title']}\n\n发布日期：{item['date']}")
        fe.guid(item["link"], permalink=True)

        pub_date = parse_date(item["date"])
        if pub_date:
            fe.pubDate(pub_date)

        item_count += 1

    with open(output_file, "wb") as f:
        f.write(fg.rss_str(pretty=True))

    print(f"RSS 生成成功，共 {item_count} 条新闻")
    print("RSS 文件保存路径：", output_file.resolve())


if __name__ == "__main__":
    asyncio.run(main())
