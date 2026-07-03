import os
import glob
import re
import json
import tempfile
import shutil
import subprocess
import asyncio
import threading
import sys
import ctypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import scraper

CONFIG_FILE = "config.json"
MANIFESTS_DIR = "manifests"


# Config

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"steamapps_folders": [], "username": "", "remember_password": False,
            "current_app_id": None, "games": {}}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def load_manifests(app_id):
    path = os.path.join(MANIFESTS_DIR, f"{app_id}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


# ACF

def parse_acf(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    tokens = re.findall(r'"[^"]*"|\{|\}', text)

    def parse_obj(pos):
        result = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                return result, pos + 1
            if tok.startswith('"'):
                key = tok[1:-1]
                pos += 1
                if pos < len(tokens):
                    nxt = tokens[pos]
                    if nxt == "{":
                        val, pos = parse_obj(pos + 1)
                    else:
                        val = nxt[1:-1]
                        pos += 1
                    result[key] = val
            else:
                pos += 1
        return result, pos

    pos = 1 if tokens and tokens[0].startswith('"') else 0
    if pos < len(tokens) and tokens[pos] == "{":
        result, _ = parse_obj(pos + 1)
        return result
    return {}

def detect_games(steamapps_folders):
    games = {}
    for folder in steamapps_folders:
        for acf_path in glob.glob(os.path.join(folder, "appmanifest_*.acf")):
            try:
                data = parse_acf(acf_path)
                app_id = data.get("appid")
                name = data.get("name")
                installdir = data.get("installdir")
                depots = list(data.get("InstalledDepots", {}).keys())
                if app_id and name and installdir and depots and app_id not in games:
                    games[app_id] = {
                        "name": name,
                        "installdir": installdir,
                        "depots": depots,
                        "steamapps_folder": folder
                    }
            except Exception:
                pass
    return games


# Paths

def get_common(game_info):
    return os.path.join(game_info["steamapps_folder"], "common")

def get_active_folder(game_info):
    return os.path.join(get_common(game_info), game_info["installdir"])

def get_steam_folder(game_info):
    return os.path.join(get_common(game_info), game_info["installdir"] + "_steam")

def format_version_folder(game_info, date, branch="None"):
    suffix = f"_v{date}" if branch == "None" else f"_v{date}_{branch}"
    return os.path.join(get_common(game_info), game_info["installdir"] + suffix)

def get_content_folder(game_info):
    return os.path.join(game_info["steamapps_folder"], "content")

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# Junctions

def is_junction(path):
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & 0x400)
    except Exception:
        return False

def create_junction(link, target):
    subprocess.run(["cmd.exe", "/c", "mklink", "/J", link, target],
                   check=True, capture_output=True, text=True)

def remove_junction(path):
    os.rmdir(path)

def open_explorer(path):
    subprocess.Popen(["explorer", os.path.normpath(path)])


# Steam Console Window

class SteamConsoleWindow(tk.Toplevel):
    def __init__(self, parent, app_id, game_info, version_date, branch, manifests,
                 config_data, on_import_complete):
        super().__init__(parent)
        self.app_id = app_id
        self.game_info = game_info
        self.version_date = version_date
        self.branch = branch
        self.manifests = manifests
        self.depots = game_info["depots"]
        self.content_folder = get_content_folder(game_info)
        self.config_data = config_data
        self.on_import_complete = on_import_complete

        self.bg = parent.bg
        self.panel = parent.panel
        self.fg = parent.fg
        self.accent = parent.accent

        branch_label = f" [{branch}]" if branch != "None" else ""
        self.title(f"Steam Console Import — {game_info['name']}{branch_label} {version_date}")
        self.geometry("740x520")
        self.resizable(False, True)
        self.configure(bg=self.bg)
        self.grab_set()

        ttk.Style(self).configure("Import.Horizontal.TProgressbar",
                                  troughcolor=self.panel, background=self.accent,
                                  bordercolor=self.bg, lightcolor=self.accent,
                                  darkcolor=self.accent)

        self.depot_action_frames = {}
        self._build_ui()
        self._refresh_depot_status()

    def _build_ui(self):
        header = tk.Frame(self, bg=self.bg)
        header.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(header, text=f"Content: {self.content_folder}",
                 bg=self.bg, fg="#8899aa", font=("Segoe UI", 9)).pack(anchor="w")


        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=4)

        scroll_container = tk.Frame(self, bg=self.bg)
        scroll_container.pack(fill="both", expand=True, padx=12)

        canvas = tk.Canvas(scroll_container, bg=self.bg, highlightthickness=0, height=260)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)

        self.depot_container = tk.Frame(canvas, bg=self.bg)
        depot_window = canvas.create_window((0, 0), window=self.depot_container, anchor="nw")

        def on_resize(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(depot_window, width=canvas.winfo_width())

        self.depot_container.bind("<Configure>", on_resize)
        canvas.bind("<Configure>", on_resize)

        for depot_id in self.depots:
            row = tk.Frame(self.depot_container, bg=self.bg)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f"Depot {depot_id}:", bg=self.bg, fg=self.fg,
                     font=("Segoe UI", 10), width=14, anchor="w").pack(side="left")
            action_frame = tk.Frame(row, bg=self.bg)
            action_frame.pack(side="left", fill="x", expand=True)
            self.depot_action_frames[depot_id] = action_frame

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=4)

        bottom = tk.Frame(self, bg=self.bg)
        bottom.pack(fill="x", padx=12, pady=8)
        ttk.Button(bottom, text="Open Steam Console",
                   command=lambda: subprocess.Popen(
                       ["cmd.exe", "/c", "start", "steam://open/console"],
                       creationflags=subprocess.CREATE_NO_WINDOW
                   )).pack(side="left")
        ttk.Button(bottom, text="Refresh",
                   command=self._refresh_depot_status).pack(side="left", padx=6)
        self.import_btn = ttk.Button(bottom, text="Import Version",
                                     command=self._import_version, state="disabled")
        self.import_btn.pack(side="right")

        self.progress_frame = tk.Frame(self, bg=self.bg)
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(self.progress_frame, variable=self.progress_var,
                        mode="determinate", maximum=100,
                        style="Import.Horizontal.TProgressbar").pack(
                            fill="x", padx=12, pady=(0, 8))

    def _refresh_depot_status(self):
        all_present = True
        for depot_id, action_frame in self.depot_action_frames.items():
            for w in action_frame.winfo_children():
                w.destroy()

            depot_path = os.path.join(self.content_folder, f"app_{self.app_id}",
                                      f"depot_{depot_id}")
            present = os.path.isdir(depot_path)
            if not present:
                all_present = False

            if present:
                tk.Label(action_frame, text="Downloaded", bg=self.bg, fg=self.fg,
                         font=("Segoe UI", 10)).pack(side="left")
                ttk.Button(action_frame, text="Delete",
                           command=lambda p=depot_path: self._delete_depot(p)).pack(
                               side="left", padx=10)
            else:
                manifest_id = (self.manifests.get(self.branch, {})
                               .get(self.version_date, {}).get(str(depot_id), "UNKNOWN"))
                command = f"download_depot {self.app_id} {depot_id} {manifest_id}"
                entry = tk.Entry(action_frame, width=52, bg=self.panel, fg=self.fg,
                                 insertbackground=self.fg, readonlybackground=self.panel,
                                 relief="flat", font=("Segoe UI", 10))
                entry.insert(0, command)
                entry.config(state="readonly")
                entry.pack(side="left")
                ttk.Button(action_frame, text="Copy",
                           command=lambda c=command: self._copy(c)).pack(side="left", padx=6)

        self.import_btn.config(state="normal" if all_present else "disabled")

    def _delete_depot(self, path):
        if messagebox.askyesno("Delete Depot Folder", f"Permanently delete:\n{path}", parent=self):
            try:
                shutil.rmtree(path)
            except Exception as e:
                messagebox.showerror("Delete Failed", f"Could not delete:\n{e}", parent=self)
            self._refresh_depot_status()

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _import_version(self):
        dest_folder = format_version_folder(self.game_info, self.version_date, self.branch)
        if os.path.exists(dest_folder):
            messagebox.showerror("Import Failed",
                                 f"Destination folder already exists:\n{dest_folder}\n\n"
                                 f"Delete it first if you want to re-import.",
                                 parent=self)
            return

        self.import_btn.config(state="disabled")

        total_files = sum(
            len(filenames)
            for depot_id in self.depots
            for _, _, filenames in os.walk(
                os.path.join(self.content_folder, f"app_{self.app_id}", f"depot_{depot_id}")
            )
        )
        progress_state = {"done": 0, "total": max(total_files, 1)}
        self.progress_frame.pack(fill="x")
        self.progress_var.set(0)

        def poll():
            self.progress_var.set((progress_state["done"] / progress_state["total"]) * 100)
            if progress_state["done"] < progress_state["total"]:
                self.after(100, poll)
        self.after(100, poll)

        def do_import():
            try:
                os.makedirs(dest_folder, exist_ok=True)
                for depot_id in self.depots:
                    depot_path = os.path.join(self.content_folder, f"app_{self.app_id}",
                                              f"depot_{depot_id}")
                    if not os.path.isdir(depot_path):
                        continue
                    for dirpath, _, filenames in os.walk(depot_path):
                        rel = os.path.relpath(dirpath, depot_path)
                        target_dir = os.path.join(dest_folder, rel)
                        os.makedirs(target_dir, exist_ok=True)
                        for filename in filenames:
                            shutil.move(os.path.join(dirpath, filename),
                                        os.path.join(target_dir, filename))
                            progress_state["done"] += 1
                self.after(0, self._import_done)
            except Exception as e:
                self.after(0, lambda: self._import_error(str(e)))

        threading.Thread(target=do_import, daemon=True).start()

    def _import_done(self):
        self.progress_var.set(100)
        for depot_id in self.depots:
            depot_path = os.path.join(self.content_folder, f"app_{self.app_id}",
                                      f"depot_{depot_id}")
            try:
                if os.path.isdir(depot_path):
                    shutil.rmtree(depot_path)
            except Exception as e:
                messagebox.showwarning("Cleanup Warning",
                                       f"Import succeeded but could not clear:\n{depot_path}\n\n{e}",
                                       parent=self)
        messagebox.showinfo("Import Complete",
                            "Version imported successfully.\nDepot folders have been cleared.",
                            parent=self)
        self.on_import_complete()
        self.destroy()

    def _import_error(self, error):
        self.import_btn.config(state="normal")
        messagebox.showerror("Import Failed", f"An error occurred:\n{error}", parent=self)


# Main App

class VersionManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        try:
            self.iconbitmap(resource_path("blackhole.ico"))
        except Exception as e:
            print(f"Warning: Could not set icon: {e}")
        self.title("Timedivers Manager v2")
        self.geometry("900x600")

        self.bg    = "#1b2838"
        self.panel = "#16202d"
        self.fg    = "#c6d4df"
        self.accent = "#1a9fff"
        self.color_active     = "#4caf50"
        self.color_downloaded = "#66c0f4"

        self.configure(bg=self.bg)
        self.option_add("*TCombobox*Listbox.background", self.panel)
        self.option_add("*TCombobox*Listbox.foreground", self.fg)
        self.option_add("*TCombobox*Listbox.selectBackground", self.accent)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        font = ("Segoe UI", 10)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", background=self.accent, foreground="#ffffff",
                        font=font, padding=6)
        style.map("TButton",
                  background=[("active", "#4ab3ff"), ("disabled", "#2a3f5a")],
                  foreground=[("disabled", "#555566")])
        style.configure("TLabel", background=self.bg, foreground=self.fg, font=font)
        style.configure("TEntry", fieldbackground=self.panel, foreground=self.fg,
                        insertcolor=self.fg)
        style.configure("TFrame", background=self.bg)
        style.configure("TCheckbutton", background=self.bg, foreground=self.fg, font=font)
        style.map("TCheckbutton", background=[("active", self.bg)],
                  foreground=[("active", self.fg)])
        style.configure("TCombobox", fieldbackground=self.panel, foreground=self.fg,
                        background=self.panel, font=font)
        style.map("TCombobox", fieldbackground=[("readonly", self.panel)],
                  foreground=[("readonly", self.fg)],
                  selectbackground=[("readonly", self.accent)])
        style.configure("TSeparator", background="#2a3f5a")
        style.configure("TScrollbar", background=self.panel, troughcolor=self.bg,
                        bordercolor=self.bg, arrowcolor=self.fg)
        style.map("TScrollbar", background=[("active", "#2a475e")])

        self.config_data = load_config()
        self.games = {}
        self.current_app_id = None
        self.current_manifests = {}
        self.listbox_index_to_version = {}
        self._sorted_game_ids = []

        self.create_widgets()
        self._refresh_games()
        self.deiconify()

    def create_widgets(self):
        # Top: configuration
        top = tk.Frame(self, bg=self.bg)
        top.pack(fill="x", padx=10, pady=6)
        top.columnconfigure(1, weight=1)

        # Steamapps folders
        ttk.Label(top, text="Steamapps Folders:").grid(row=0, column=0, sticky="nw",
                                                        padx=(0, 4), pady=4)
        fl_container = tk.Frame(top, bg=self.bg)
        fl_container.grid(row=0, column=1, sticky="ew", pady=4)
        fl_scroll = ttk.Scrollbar(fl_container, orient="vertical")
        fl_scroll.pack(side="right", fill="y")
        self.folders_listbox = tk.Listbox(fl_container, height=2, bg=self.panel, fg=self.fg,
                                          selectbackground=self.accent, font=("Segoe UI", 10),
                                          relief="flat", yscrollcommand=fl_scroll.set)
        self.folders_listbox.pack(side="left", fill="both", expand=True)
        fl_scroll.config(command=self.folders_listbox.yview)

        fl_btns = tk.Frame(top, bg=self.bg)
        fl_btns.grid(row=0, column=2, padx=(6, 0), pady=4)
        ttk.Button(fl_btns, text="Add", command=self.add_steamapps_folder,
                   width=8).pack(side="left", padx=2)
        ttk.Button(fl_btns, text="Remove", command=self.remove_steamapps_folder,
                   width=8).pack(side="left", padx=2)

        for folder in self.config_data.get("steamapps_folders", []):
            self.folders_listbox.insert(tk.END, folder)

        game_row = tk.Frame(top, bg=self.bg)
        game_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        game_row.columnconfigure(1, weight=2)
        game_row.columnconfigure(3, weight=1)
        ttk.Label(game_row, text="Game:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.game_var = tk.StringVar()
        self.game_combo = ttk.Combobox(game_row, textvariable=self.game_var, state="readonly")
        self.game_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.game_combo.bind("<<ComboboxSelected>>", self._on_game_select)
        ttk.Label(game_row, text="Beta:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.beta_var = tk.StringVar(value="None")
        self.beta_combo = ttk.Combobox(game_row, textvariable=self.beta_var, state="readonly")
        self.beta_combo["values"] = ["None"]
        self.beta_combo.grid(row=0, column=3, sticky="ew")
        self.beta_combo.bind("<<ComboboxSelected>>", self._on_beta_select)

        # Depot Downloader settings header
        dd_header = tk.Frame(top, bg=self.bg)
        dd_header.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        ttk.Label(dd_header, text="Depot Downloader",
                  foreground="#8899aa", font=("Segoe UI", 9, "italic")).pack(side="left")
        tk.Frame(dd_header, bg="#2a3f5a", height=1).pack(side="left", fill="x", expand=True,
                                                          padx=(8, 0), pady=5)

        creds = tk.Frame(top, bg=self.bg)
        creds.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        ttk.Label(creds, text="Steam Username:").pack(side="left")
        self.username_var = tk.StringVar(value=self.config_data.get("username", ""))
        ttk.Entry(creds, textvariable=self.username_var, width=22).pack(side="left", padx=(4, 0))
        self.remember_var = tk.BooleanVar(value=self.config_data.get("remember_password", False))
        ttk.Checkbutton(creds, text="Remember Password",
                        variable=self.remember_var).pack(side="left", padx=8)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        # Middle: version list
        mid = tk.Frame(self, bg=self.bg)
        mid.pack(fill="both", expand=True, padx=10)

        list_header = tk.Frame(mid, bg=self.bg)
        list_header.pack(fill="x")
        ttk.Label(list_header, text="Available Versions:").pack(side="left")
        self.sort_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(list_header, text="Sort by Downloaded",
                        variable=self.sort_var,
                        command=self.refresh_version_list).pack(side="left", padx=10)

        list_frame = tk.Frame(mid, bg=self.bg)
        list_frame.pack(fill="both", expand=True, pady=4)
        vscroll = ttk.Scrollbar(list_frame, orient="vertical")
        vscroll.pack(side="right", fill="y")
        self.version_listbox = tk.Listbox(list_frame, bg=self.panel, fg=self.fg,
                                          selectbackground=self.accent,
                                          font=("Segoe UI", 10), relief="flat",
                                          yscrollcommand=vscroll.set)
        self.version_listbox.pack(side="left", fill="both", expand=True)
        vscroll.config(command=self.version_listbox.yview)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        # Bottom: action buttons
        bottom = tk.Frame(self, bg=self.bg)
        bottom.pack(fill="x", padx=10, pady=6)

        self.download_btn = ttk.Button(bottom, text="Download Version ▼",
                                       command=lambda: self._show_download_menu(self.download_btn))
        self.download_btn.pack(side="left", padx=(0, 4))
        self.set_active_btn = ttk.Button(bottom, text="Set Active Version",
                                         command=self.switch_version)
        self.set_active_btn.pack(side="left", padx=4)
        self.delete_btn = ttk.Button(bottom, text="Delete Version",
                                     command=self.delete_version)
        self.delete_btn.pack(side="left", padx=4)

        self.setup_btn = ttk.Button(bottom, text="Setup Folders",
                                    command=self.setup_or_revert_folders)
        self.setup_btn.pack(side="right", padx=(4, 0))
        self.update_btn = ttk.Button(bottom, text="Update Manifests", command=self.run_scraper)
        self.update_btn.pack(side="right", padx=4)

        self.action_buttons = [self.download_btn, self.set_active_btn, self.delete_btn]

    # Game / Branch Selection

    def add_steamapps_folder(self):
        folder = filedialog.askdirectory(title="Select Steamapps Folder")
        if not folder:
            return
        if os.path.basename(folder.rstrip("/\\")).lower() != "steamapps":
            if not messagebox.askyesno("Unusual Folder",
                f"This folder isn't named 'steamapps':\n{folder}\n\nAdd anyway?"):
                return
        folders = self.config_data.setdefault("steamapps_folders", [])
        if folder not in folders:
            folders.append(folder)
            self.folders_listbox.insert(tk.END, folder)
            save_config(self.config_data)
            self._refresh_games()

    def remove_steamapps_folder(self):
        sel = self.folders_listbox.curselection()
        if not sel:
            return
        folder = self.folders_listbox.get(sel[0])
        self.folders_listbox.delete(sel[0])
        folders = self.config_data.get("steamapps_folders", [])
        if folder in folders:
            folders.remove(folder)
        save_config(self.config_data)
        self._refresh_games()

    def _refresh_games(self):
        self.games = detect_games(self.config_data.get("steamapps_folders", []))
        sorted_games = sorted(self.games.items(), key=lambda x: x[1]["name"].lower())
        self._sorted_game_ids = [app_id for app_id, _ in sorted_games]
        self.game_combo["values"] = [f"{info['name']}" for _, info in sorted_games]

        current = self.config_data.get("current_app_id")
        if current and current in self.games:
            idx = self._sorted_game_ids.index(current)
            self.game_combo.current(idx)
            self._load_game(current)
        elif sorted_games:
            self.game_combo.current(0)
            self._load_game(sorted_games[0][0])
        else:
            self.game_combo.set("")
            self._load_game(None)

    def _on_game_select(self, event=None):
        idx = self.game_combo.current()
        if 0 <= idx < len(self._sorted_game_ids):
            self._load_game(self._sorted_game_ids[idx])

    def _load_game(self, app_id):
        self.current_app_id = app_id
        if app_id:
            self.config_data["current_app_id"] = app_id
            save_config(self.config_data)
            self.current_manifests = load_manifests(app_id)
        else:
            self.current_manifests = {}

        branches = ["None"] + [b for b in self.current_manifests if b != "None"]
        self.beta_combo["values"] = branches

        active_branch = (self.config_data.get("games", {})
                         .get(app_id or "", {}).get("active_branch", "None"))
        self.beta_var.set(active_branch if active_branch in branches else "None")

        self.refresh_setup_state()
        self.refresh_version_list()

    def _on_beta_select(self, event=None):
        self.refresh_version_list()

    # Setup State

    def refresh_setup_state(self):
        if not self.current_app_id:
            for btn in self.action_buttons:
                btn.config(state="disabled")
            self.setup_btn.config(text="Setup Folders", state="disabled")
            self.update_btn.config(state="disabled")
            return

        game_info = self.games[self.current_app_id]
        active_folder = get_active_folder(game_info)
        steam_folder = get_steam_folder(game_info)

        if (not is_junction(active_folder) and not os.path.isdir(active_folder)
                and os.path.isdir(steam_folder)):
            try:
                create_junction(active_folder, steam_folder)
            except Exception:
                pass

        if is_junction(active_folder):
            for btn in self.action_buttons:
                btn.config(state="normal")
            self.update_btn.config(state="normal")
            self.setup_btn.config(text="Revert Folders", state="normal")
        else:
            for btn in self.action_buttons:
                btn.config(state="disabled")
            self.update_btn.config(state="disabled")
            self.setup_btn.config(text="Setup Folders", state="normal")

    # Setup / Revert

    def setup_or_revert_folders(self):
        if not self.current_app_id:
            return
        game_info = self.games[self.current_app_id]
        if is_junction(get_active_folder(game_info)):
            self._revert_folders(game_info)
        else:
            self._setup_folders(game_info)

    def _setup_folders(self, game_info):
        active_folder = get_active_folder(game_info)
        steam_folder = get_steam_folder(game_info)
        common = get_common(game_info)
        installdir = game_info["installdir"]

        if not os.path.isdir(active_folder):
            if os.path.isdir(steam_folder):
                try:
                    create_junction(active_folder, steam_folder)
                    self.refresh_setup_state()
                    self.refresh_version_list()
                except Exception as e:
                    messagebox.showerror("Setup Failed", f"Could not create junction:\n{e}")
                return
            messagebox.showerror("Setup Failed",
                                 f"Game folder not found:\n{active_folder}\n\n"
                                 f"Make sure the game is installed.")
            return

        if os.path.exists(steam_folder):
            messagebox.showwarning("Setup Warning",
                                   f"Both folders already exist:\n  {active_folder}\n"
                                   f"  {steam_folder}\n\nPlease resolve manually and restart.")
            return

        if not messagebox.askyesno("Setup Folders",
            f"This will:\n\n"
            f"  • Rename '{installdir}' to '{installdir}_steam'\n"
            f"  • Create a junction '{installdir}' pointing to it\n\n"
            f"The game will continue to work normally. This only needs to be done once.\n\n"
            f"Proceed?"):
            return

        try:
            os.rename(active_folder, steam_folder)
        except OSError:
            messagebox.showinfo("Manual Step Required",
                                f"Windows prevented the rename.\n\n"
                                f"Please rename manually in File Explorer:\n"
                                f"  From: {installdir}\n"
                                f"  To:   {installdir}_steam\n\n"
                                f"Location: {common}\n\n"
                                f"Then reopen this app.")
            open_explorer(common)
            return

        try:
            create_junction(active_folder, steam_folder)
        except Exception as e:
            try:
                os.rename(steam_folder, active_folder)
            except Exception:
                pass
            messagebox.showerror("Setup Failed",
                                 f"Rename succeeded but junction creation failed:\n{e}\n\n"
                                 f"The rename has been undone.")
            open_explorer(common)
            return

        self.refresh_setup_state()
        self.refresh_version_list()

    def _revert_folders(self, game_info):
        active_folder = get_active_folder(game_info)
        steam_folder = get_steam_folder(game_info)
        common = get_common(game_info)
        installdir = game_info["installdir"]

        if not messagebox.askyesno("Revert Folders",
            f"This will:\n\n"
            f"  • Remove the junction\n"
            f"  • Rename '{installdir}_steam' back to '{installdir}'\n\n"
            f"Downloaded version folders will remain on disk.\n\n"
            f"Continue?"):
            return

        game_config = self.config_data.get("games", {}).get(self.current_app_id, {})
        active_date = game_config.get("active_date", "steam")

        if active_date != "steam":
            if not os.path.exists(steam_folder):
                messagebox.showerror("Revert Failed",
                                     f"Steam version folder not found:\n{steam_folder}\n\n"
                                     f"The Steam version must be present to revert.")
                return
            try:
                remove_junction(active_folder)
                create_junction(active_folder, steam_folder)
            except Exception as e:
                messagebox.showerror("Revert Failed", f"Could not redirect junction:\n{e}")
                return

        try:
            remove_junction(active_folder)
        except Exception as e:
            messagebox.showerror("Revert Failed", f"Could not remove junction:\n{e}")
            return

        if not os.path.exists(steam_folder):
            messagebox.showerror("Revert Failed",
                                 f"Steam folder not found:\n{steam_folder}\n\n"
                                 f"Please rename manually:\n"
                                 f"  From: {installdir}_steam\n  To: {installdir}")
            open_explorer(common)
            return

        try:
            os.rename(steam_folder, active_folder)
        except OSError as e:
            messagebox.showerror("Revert Failed",
                                 f"Could not rename folder:\n{e}\n\n"
                                 f"Please rename manually:\n"
                                 f"  From: {installdir}_steam\n  To: {installdir}")
            open_explorer(common)
            return

        messagebox.showinfo("Revert Complete",
                            "Folder setup reverted.\n\n"
                            "Downloaded version folders remain on disk and can be deleted manually.")
        self.refresh_setup_state()
        self.refresh_version_list()

    # Version List

    def refresh_version_list(self):
        self.version_listbox.delete(0, tk.END)
        self.listbox_index_to_version.clear()

        if not self.current_app_id:
            return

        game_info = self.games[self.current_app_id]
        branch = self.beta_var.get()
        game_config = self.config_data.get("games", {}).get(self.current_app_id, {})
        active_date = game_config.get("active_date", "steam")
        active_branch = game_config.get("active_branch", "None")

        def insert_item(key_date, key_branch, display, is_active, is_downloaded):
            idx = self.version_listbox.size()
            self.version_listbox.insert(tk.END, display)
            if is_active:
                self.version_listbox.itemconfig(idx, fg=self.color_active)
            elif is_downloaded:
                self.version_listbox.itemconfig(idx, fg=self.color_downloaded)
            self.listbox_index_to_version[idx] = (key_date, key_branch)

        insert_item("steam", "None", "Steam Version",
                    is_active=(active_date == "steam"),
                    is_downloaded=os.path.exists(get_steam_folder(game_info)))

        branch_data = self.current_manifests.get(branch, {})
        dates = list(branch_data.keys())

        if self.sort_var.get():
            downloaded = sorted(
                [d for d in dates if os.path.exists(format_version_folder(game_info, d, branch))],
                reverse=True)
            not_downloaded = sorted(
                [d for d in dates if not os.path.exists(format_version_folder(game_info, d, branch))],
                reverse=True)
            dates_sorted = downloaded + not_downloaded
        else:
            dates_sorted = sorted(dates, reverse=True)

        for date in dates_sorted:
            folder = format_version_folder(game_info, date, branch)
            patch_title = branch_data[date].get("patch_title", "")
            display = f"{date}  —  {patch_title}" if patch_title else date
            insert_item(date, branch, display,
                        is_active=(active_date == date and active_branch == branch),
                        is_downloaded=os.path.exists(folder))

    # Download

    def _show_download_menu(self, anchor_widget):
        menu = tk.Menu(self, tearoff=0,
                       bg=self.panel, fg=self.fg,
                       activebackground=self.accent, activeforeground="#ffffff",
                       font=("Segoe UI", 10), bd=0, relief="flat")
        menu.add_command(label="using Steam Console", command=self.open_steam_console)
        menu.add_command(label="using Depot Downloader", command=self.download_version)
        menu.tk_popup(anchor_widget.winfo_rootx(),
                      anchor_widget.winfo_rooty() + anchor_widget.winfo_height())

    def download_version(self):
        sel = self.version_listbox.curselection()
        if not sel:
            return
        date, branch = self.listbox_index_to_version[sel[0]]

        if date == "steam":
            messagebox.showwarning("Not Allowed", "Use the Steam client for the Steam version.")
            return

        game_config = self.config_data.get("games", {}).get(self.current_app_id, {})
        if date == game_config.get("active_date") and branch == game_config.get("active_branch", "None"):
            messagebox.showwarning("Not Allowed", "Cannot download the currently active version.")
            return

        game_info = self.games[self.current_app_id]
        folder_path = format_version_folder(game_info, date, branch)
        os.makedirs(folder_path, exist_ok=True)

        branch_data = self.current_manifests.get(branch, {}).get(date, {})
        username = self.username_var.get()
        self.config_data["username"] = username
        self.config_data["remember_password"] = self.remember_var.get()
        save_config(self.config_data)

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".bat") as f:
            for depot_id in game_info["depots"]:
                manifest_id = branch_data.get(str(depot_id))
                if not manifest_id:
                    continue
                cmd = (f'DepotDownloader.exe -app {self.current_app_id}'
                       f' -depot {depot_id} -manifest {manifest_id}')
                if branch != "None":
                    cmd += f' -beta {branch}'
                cmd += f' -username "{username}" -dir "{folder_path}"'
                if self.remember_var.get():
                    cmd += " -remember-password"
                f.write(cmd + "\n")
            f.write("exit\n")
            batch_path = f.name

        subprocess.Popen(["cmd.exe", "/c", batch_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.refresh_version_list()

    def open_steam_console(self):
        sel = self.version_listbox.curselection()
        if not sel:
            return
        date, branch = self.listbox_index_to_version[sel[0]]

        if date == "steam":
            messagebox.showwarning("Not Allowed", "Use the Steam client for the Steam version.")
            return

        game_config = self.config_data.get("games", {}).get(self.current_app_id, {})
        if date == game_config.get("active_date") and branch == game_config.get("active_branch", "None"):
            messagebox.showwarning("Not Allowed", "Cannot import the currently active version.")
            return

        game_info = self.games[self.current_app_id]
        if os.path.exists(format_version_folder(game_info, date, branch)):
            messagebox.showwarning("Already Downloaded",
                                   f"Version {date} ({branch}) is already downloaded.")
            return

        SteamConsoleWindow(parent=self, app_id=self.current_app_id, game_info=game_info,
                           version_date=date, branch=branch, manifests=self.current_manifests,
                           config_data=self.config_data, on_import_complete=self.refresh_version_list)

    # Switch / Delete

    def switch_version(self):
        sel = self.version_listbox.curselection()
        if not sel:
            return
        date, branch = self.listbox_index_to_version[sel[0]]
        game_info = self.games[self.current_app_id]
        active_folder = get_active_folder(game_info)
        target = get_steam_folder(game_info) if date == "steam" else \
                 format_version_folder(game_info, date, branch)

        if not os.path.exists(target):
            messagebox.showerror("Missing Folder", f"Version folder not found:\n{target}")
            return

        if is_junction(active_folder) or os.path.exists(active_folder):
            try:
                remove_junction(active_folder)
            except Exception as e:
                messagebox.showerror("Switch Failed", f"Could not remove junction:\n{e}")
                return

        try:
            create_junction(active_folder, target)
        except Exception as e:
            messagebox.showerror("Switch Failed", f"Could not create junction:\n{e}")
            return

        game_cfg = self.config_data.setdefault("games", {}).setdefault(self.current_app_id, {})
        game_cfg["active_date"] = date
        game_cfg["active_branch"] = branch
        game_cfg["steamapps_folder"] = game_info["steamapps_folder"]
        save_config(self.config_data)
        self.refresh_version_list()

    def delete_version(self):
        sel = self.version_listbox.curselection()
        if not sel:
            return
        date, branch = self.listbox_index_to_version[sel[0]]

        if date == "steam":
            messagebox.showwarning("Not Allowed", "Cannot delete the Steam version.")
            return

        game_config = self.config_data.get("games", {}).get(self.current_app_id, {})
        if (date == game_config.get("active_date") and
                branch == game_config.get("active_branch", "None")):
            messagebox.showwarning("Not Allowed", "Cannot delete the currently active version.")
            return

        game_info = self.games[self.current_app_id]
        folder = format_version_folder(game_info, date, branch)
        if not os.path.exists(folder):
            messagebox.showerror("Missing Folder", f"Folder not found:\n{folder}")
            return

        if not messagebox.askyesno("Confirm Delete",
            f"Delete version '{date}' [{branch}]?\n\n{folder}"):
            return

        shutil.rmtree(folder)
        self.refresh_version_list()

    # Scraper

    def run_scraper(self):
        if not self.current_app_id:
            return

        game_info = self.games[self.current_app_id]
        depots = game_info["depots"]
        depot_list = "\n".join(f"  {d}" for d in depots)

        if not messagebox.askyesno(
            "Update List",
            f"Microsoft Edge will be used to scrape SteamDB for {game_info['name']}.\n\n"
            f"It will be restarted in debug mode. Please ensure you are logged into your "
            f"Steam account on SteamDB with 'Remember Me' checked.\n\n"
            f"The following {len(depots)} depot ID(s) will be scraped:\n{depot_list}\n\n"
            f"Some of these may be DLC depots. Consider uninstalling DLCs "
            f"before scraping.\n\n"
            f"Continue?"
        ):
            return

        def worker():
            asyncio.run(scraper.main(self.current_app_id, game_info["depots"]))
            self.after(0, self._scraper_done)

        threading.Thread(target=worker, daemon=True).start()

    def _scraper_done(self):
        self.current_manifests = load_manifests(self.current_app_id)
        branches = ["None"] + [b for b in self.current_manifests if b != "None"]
        self.beta_combo["values"] = branches
        if self.beta_var.get() not in branches:
            self.beta_var.set("None")
        self.refresh_version_list()
        messagebox.showinfo("Update Complete", "Manifest list has been updated.")


# Entry Point

if __name__ == "__main__":
    app = VersionManagerApp()
    app.mainloop()