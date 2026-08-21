import asyncio
import subprocess
import socket
import time
import os
import sys
import json
from pathlib import Path
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from datetime import datetime

_base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
BROWSER_DATA_DIR = Path(_base_dir) / "browser_data"
MANIFESTS_DIR = "manifests"



def _engine_for_exe(exe_path):
    name = os.path.basename(exe_path).lower()
    if "firefox" in name:
        return None
    return "chromium"


if sys.platform == "win32":
    def _find_system_browsers():
        import winreg
        results = []
        hives_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Clients\StartMenuInternet"),
        ]
        for hive, reg_path in hives_paths:
            try:
                with winreg.OpenKey(hive, reg_path) as root:
                    i = 0
                    while True:
                        try:
                            key_name = winreg.EnumKey(root, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(root, key_name) as bkey:
                                try:
                                    display_name, _ = winreg.QueryValueEx(bkey, "")
                                except OSError:
                                    display_name = key_name
                            cmd_path = key_name + r"\shell\open\command"
                            with winreg.OpenKey(root, cmd_path) as cmd_key:
                                raw, _ = winreg.QueryValueEx(cmd_key, "")
                                raw = raw.strip()
                                if raw.startswith('"'):
                                    exe = raw[1:raw.index('"', 1)]
                                else:
                                    exe = raw.split()[0]
                            results.append((display_name or key_name, exe))
                        except OSError:
                            continue
            except OSError:
                continue
        return results

else:
    def _find_system_browsers():
        import glob
        import configparser
        import shutil as _shutil

        results = []
        search_dirs = [
            "/usr/share/applications",
            "/usr/local/share/applications",
            os.path.expanduser("~/.local/share/applications"),
        ]
        for d in search_dirs:
            for desktop_file in glob.glob(os.path.join(d, "*.desktop")):
                try:
                    parser = configparser.ConfigParser(strict=False, interpolation=None)
                    parser.read(desktop_file, encoding="utf-8")
                    if not parser.has_section("Desktop Entry"):
                        continue
                    cats  = parser.get("Desktop Entry", "Categories", fallback="")
                    mimes = parser.get("Desktop Entry", "MimeType",   fallback="")
                    if "WebBrowser" not in cats and "x-scheme-handler/http" not in mimes:
                        continue
                    name     = parser.get("Desktop Entry", "Name", fallback="").strip()
                    exec_val = parser.get("Desktop Entry", "Exec", fallback="").strip()
                    if not name or not exec_val:
                        continue
                    exe = exec_val.split()[0].strip('"')
                    exe = _shutil.which(exe) or (exe if os.path.isabs(exe) else None)
                    if exe:
                        results.append((name, exe))
                except Exception:
                    continue
        return results


def detect_browsers():
    found = []
    seen  = set()
    for name, exe in _find_system_browsers():
        exe = os.path.normpath(exe)
        if not os.path.isfile(exe) or exe in seen:
            continue
        seen.add(exe)
        engine = _engine_for_exe(exe)
        if engine is None:
            continue
        slug   = name.lower().replace(" ", "_").replace("/", "_")
        found.append((name, engine, {"executable_path": exe}, slug))
    return found


def _gui_choose_browser(names):
    import tkinter as tk
    from tkinter import ttk

    result = [0]

    root = tk.Tk()
    root.title("Select Browser")
    root.resizable(False, False)
    root.configure(bg="#1b2838")

    tk.Label(root, text="Choose a browser to use for scraping:",
             bg="#1b2838", fg="#ffffff",
             font=("Segoe UI", 10)).pack(padx=16, pady=(14, 6))

    listbox = tk.Listbox(root, selectmode=tk.SINGLE,
                         bg="#16202d", fg="#ccccdd",
                         selectbackground="#1a9fff",
                         selectforeground="#ffffff",
                         font=("Segoe UI", 10),
                         activestyle="none",
                         height=min(len(names), 10),
                         width=36,
                         relief="flat")
    for name in names:
        listbox.insert(tk.END, f"  {name}")
    listbox.select_set(0)
    listbox.pack(padx=16, pady=(0, 8))

    def confirm(event=None):
        sel = listbox.curselection()
        if sel:
            result[0] = sel[0]
        root.destroy()

    listbox.bind("<Double-Button-1>", confirm)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TButton", background="#1a9fff", foreground="#ffffff",
                    font=("Segoe UI", 10), padding=6)
    style.map("TButton", background=[("active", "#4ab3ff")])

    ttk.Button(root, text="Select", command=confirm).pack(pady=(0, 14))

    root.update_idletasks()
    x = (root.winfo_screenwidth()  - root.winfo_reqwidth())  // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()
    return result[0]


def choose_browser(browsers, select_fn=None):
    if not browsers:
        raise RuntimeError("No supported browsers found on this system.")
    if len(browsers) == 1:
        print(f"Using {browsers[0][0]}")
        return browsers[0]
    names = [b[0] for b in browsers]
    idx = select_fn(names) if select_fn else _gui_choose_browser(names)
    return browsers[idx]


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _wait_for_cdp(port, retries=30):
    import urllib.request
    for _ in range(retries):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1)
            return True
        except Exception:
            await asyncio.sleep(0.5)
    return False


async def launch_context(playwright, browser_info):
    name, engine, kwargs, slug = browser_info
    user_data_dir = BROWSER_DATA_DIR / slug
    user_data_dir.mkdir(parents=True, exist_ok=True)
    exe  = kwargs["executable_path"]
    port = _find_free_port()

    STEALTH_FLAGS = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=msEdgeAutomationOverlay",
        "--exclude-switches=enable-automation",
    ]

    proc = subprocess.Popen([
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        *STEALTH_FLAGS,
        "https://steamdb.info/login/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"Launched {name}, waiting for CDP...")
    if not await _wait_for_cdp(port):
        proc.terminate()
        raise RuntimeError(f"CDP did not become available on port {port}")

    try:
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}")
        page = (browser.contexts[0].pages[0]
                if browser.contexts and browser.contexts[0].pages
                else await browser.contexts[0].new_page())
        print(f"Connected to {name} via CDP")
        return browser, page, proc
    except Exception as e:
        print(f"CDP connection failed ({e}), falling back to direct launch...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=exe,
            headless=False,
            args=STEALTH_FLAGS,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://steamdb.info/login/")
        print(f"Connected to {name} via persistent context")
        return context, page, None



def merge_manifests(branch_data, depots):
    def try_parse(d):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(d, fmt)
            except ValueError:
                continue
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
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            dt = datetime.strptime(date_str.strip(), "%d %B %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            print(f"Warning: Could not parse date '{date_str}', leaving as-is")
            return date_str


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

    for branch_name in result:
        day_counts = {}
        for key in result[branch_name]:
            day = key[:10]
            day_counts[day] = day_counts.get(day, 0) + 1
        cleaned = {}
        for key, manifest_id in result[branch_name].items():
            day = key[:10]
            cleaned[day if day_counts[day] == 1 else key] = manifest_id
        result[branch_name] = cleaned

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

    patch_titles = []
    for row in patch_table.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        title_link = cols[3].find("a", class_="b")
        title = title_link.get_text(strip=True) if title_link else cols[3].get_text(strip=True)
        if not title:
            continue

        data_date = row.get("data-date")
        if data_date:
            try:
                dt = datetime.utcfromtimestamp(int(data_date))
                date_key = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                date_key = normalize_date(cols[0].get_text(strip=True))
        else:
            date_key = normalize_date(cols[0].get_text(strip=True))

        patch_titles.append((date_key, title))
    return patch_titles



async def main(app_id, depots, wait_fn=None, browser_select_fn=None):
    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    manifest_file = os.path.join(MANIFESTS_DIR, f"{app_id}.json")
    manifests = {}

    browsers     = detect_browsers()
    browser_info = choose_browser(browsers, select_fn=browser_select_fn)

    async with async_playwright() as p:
        browser, page, proc = await launch_context(p, browser_info)

        if wait_fn:
            print(f"{browser_info[0]} is ready. Please log in to SteamDB, then click Continue.")
            wait_fn()
            print("Starting scraping...")

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

        def _parse_dt(key):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(key, fmt)
                except ValueError:
                    continue
            return datetime.min

        day_titles = {}
        for pt_key, title in patch_titles:
            day = pt_key[:10]
            pt_dt = _parse_dt(pt_key)
            day_titles.setdefault(day, []).append((pt_dt, title))

        for branch, branch_data in manifests.items():
            day_manifests = {}
            for key in branch_data:
                day_manifests.setdefault(key[:10], []).append(key)

            for day, titles in day_titles.items():
                m_keys = day_manifests.get(day)
                if not m_keys:
                    continue
                for pt_dt, title in titles:
                    closest = min(
                        m_keys,
                        key=lambda k: abs((_parse_dt(k) - pt_dt).total_seconds())
                    )
                    branch_data[closest]["patch_title"] = title

        await browser.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    for branch_data in manifests.values():
        merge_manifests(branch_data, depots)

    with open(manifest_file, "w") as f:
        json.dump(manifests, f, indent=4)

    print(f"\nUpdated {manifest_file}.\n")


if __name__ == "__main__":
    print("Please use the Update Manifests option in the version manager.")
    input("Press ENTER to continue")