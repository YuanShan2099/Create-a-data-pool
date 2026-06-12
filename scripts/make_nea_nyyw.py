import asyncio
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from playwright.async_api import async_playwright


# 用 https 抓网页源码
URL = "https://www.nea.gov.cn/xwzx/nyyw.htm"

# 最终文章链接也建议用 https
BASE = "https://www.nea.gov.cn"

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

        # 如果链接被拼成 http，可以统一替换为 https
        link = link.replace("http://www.nea.gov.cn", "https://www.nea.gov.cn")

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

        page.set_default_timeout(90000)
        page.set_default_navigation_timeout(90000)

        page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} | {req.failure}"))

        # ============================================================
        # 关键修改：不用 page.goto(URL)，而是先用 requests 抓源码，
        # 再把源码里的 http://www.nea.gov.cn/2015nyj/ 替换为 https。
        # 然后用 page.set_content 让 Playwright 渲染修改后的页面。
        # ============================================================
        resp = requests.get(
            URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=30
        )
        resp.encoding = "utf-8"

        html_source = resp.text.replace(
            "http://www.nea.gov.cn/2015nyj/",
            "https://www.nea.gov.cn/2015nyj/"
        )

        await page.set_content(
            html_source,
            wait_until="domcontentloaded",
            timeout=90000
        )

        await page.wait_for_timeout(15000)

        print("页面标题：", await page.title())
        print("当前URL：", page.url)
        print("showData0 数量：", await page.locator("#showData0").count())

        li_count = await page.locator("#showData0 li").count()
        print("替换脚本地址后 li 数量：", li_count)

        if li_count == 0:
            html = await page.content()
            print("页面长度：", len(html))
            print("页面前1000字符：")
            print(html[:1000])
            print("GitHub Actions 环境中新闻列表未渲染，保留旧 RSS，不更新文件。")
            await browser.close()
            return

        # ============================================================
        # 抓取第 1—5 页
        # ============================================================
        for page_no in range(1, 6):
            print(f"正在抓取第 {page_no} 页...")

            try:
                await page.wait_for_function(
                    "document.querySelectorAll('#showData0 li').length > 0",
                    timeout=90000
                )
            except Exception as e:
                print(f"第 {page_no} 页等待新闻列表失败：{e}")
                break

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
                    try:
                        await pager.get_by_text("下一页").click(timeout=10000)
                    except Exception as e:
                        print(f"点击第 {page_no + 1} 页失败：{e}")
                        break

                await page.wait_for_timeout(2000)

        await browser.close()

    print(f"合计抓到 {len(all_items)} 条去重新闻")

    if len(all_items) == 0:
        print("本次没有抓到任何新闻，保留旧 RSS，不更新文件。")
        return

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
