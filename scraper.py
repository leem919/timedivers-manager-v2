import asyncio
import subprocess
import time
import os
import json
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from datetime import datetime

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DEBUG_PORT = 9222
MANIFESTS_DIR = "manifests"


def merge_manifests(branch_data, depots):
    def try_parse(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return datetime.min

    last_known = {}
    for date in sorted(branch_data.keys(), key=try_parse):
        entry = branch_data[date]
        for depot in depots:
            key = str(depot)
            if key not in entry and key in last_known:
                entry[key] = last_known[key]
            if key in entry:
                last_known[key] = entry[key]


def normalize_date(date_str):
    try:
        dt = datetime.strptime(date_str.replace("\u2013", "-").strip(), "%d %B %Y - %H:%M:%S UTC")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        try:
            dt = datetime.strptime(date_str.strip(), "%d %B %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            print(f"Warning: Could not parse date '{date_str}', leaving as-is")
            return date_str


def launch_edge():
    subprocess.run(["taskkill", "/IM", "msedge.exe", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process = subprocess.Popen([
        EDGE_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "https://steamdb.info/"
    ])
    print("Edge launched at https://steamdb.info/")
    time.sleep(5)
    return process


async def scrape_depot_manifests(page, depot_id):
    await page.goto(f"https://steamdb.info/depot/{depot_id}/manifests/")
    try:
        await page.wait_for_selector("table.table tbody tr", timeout=60000)
    except Exception:
        print(f"Table not found for depot {depot_id}. Make sure the page loaded correctly.")
    return await page.content()


def parse_table(html, depot_id):
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table", class_="table"):
        headers = [th.text.strip().lower() for th in t.find_all("th")]
        if "seen date" in headers and "manifestid" in headers:
            table = t
            break
    if not table or not table.tbody:
        print(f"No correct table found for depot {depot_id}")
        return {}

    result = {}
    for row in table.tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 3:
            continue
        manifest_cell = cols[2]
        manifest_link = manifest_cell.find("a")
        if not manifest_link:
            continue

        branch_tag = manifest_cell.find("code", class_="js-branch")
        branch_name = branch_tag.text.strip() if branch_tag else "None"
        date_str = normalize_date(cols[0].text.strip())
        manifest_id = manifest_link.text.strip()

        result.setdefault(branch_name, {}).setdefault(date_str, manifest_id)

    return result


async def scrape_patch_titles(page, app_id):
    await page.goto(f"https://steamdb.info/app/{app_id}/patchnotes/")
    try:
        await page.wait_for_selector("#js-builds tr", timeout=60000)
    except Exception:
        print("Patch notes table not found. Make sure the page loaded correctly.")
        return {}

    soup = BeautifulSoup(await page.content(), "html.parser")
    patch_table = soup.find("tbody", id="js-builds")
    if not patch_table:
        print("No correct patch notes table found.")
        return {}

    patch_titles = {}
    for row in patch_table.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 4:
            continue
        raw_date = cols[0].get_text(strip=True)
        title = cols[3].get_text(strip=True)
        if raw_date and title:
            patch_titles[normalize_date(raw_date)] = title
    return patch_titles


async def main(app_id, depots):
    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    manifest_file = os.path.join(MANIFESTS_DIR, f"{app_id}.json")
    manifests = json.load(open(manifest_file)) if os.path.exists(manifest_file) else {}

    edge_process = launch_edge()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        page = browser.contexts[0].pages[0]

        for depot in depots:
            print(f"Scraping depot {depot}...")
            branch_data = parse_table(await scrape_depot_manifests(page, depot), depot)
            for branch_name, date_manifests in branch_data.items():
                for date, manifest_id in date_manifests.items():
                    manifests.setdefault(branch_name, {}).setdefault(date, {})[str(depot)] = manifest_id
            print("Waiting 5 seconds...")
            await asyncio.sleep(5)

        print("Scraping patch titles...")
        patch_titles = await scrape_patch_titles(page, app_id)
        for date, title in patch_titles.items():
            if date in manifests.get("None", {}):
                manifests["None"][date]["patch_title"] = title
            for branch in manifests:
                if branch != "None" and date in manifests[branch]:
                    manifests[branch][date]["patch_title"] = title

        await browser.close()
        try:
            edge_process.terminate()
            edge_process.wait(timeout=5)
        except Exception:
            pass
        subprocess.run(["taskkill", "/IM", "msedge.exe", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for branch_data in manifests.values():
        merge_manifests(branch_data, depots)

    with open(manifest_file, "w") as f:
        json.dump(manifests, f, indent=4)

    print(f"\nUpdated {manifest_file}.\n")


if __name__ == "__main__":
    print("Please use the Update Manifests option in the version manager.")
    input("Press ENTER to continue")