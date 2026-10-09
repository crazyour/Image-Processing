"""Per-user Windows local server. All services bind loopback; no shared server needed."""
import argparse, json, logging, os
from pathlib import Path
import secrets, socket, subprocess, sys, time, webbrowser, tempfile, hashlib; _window_lock = __import__("threading").Lock()
def activate_workbench():
    if os.name != "nt":
        return False
    import ctypes as C
    from ctypes import wintypes as W; user = C.windll.user32
    user.ShowWindow.argtypes = [W.HWND,
        C.c_int]; user.SetForegroundWindow.argtypes = [W.HWND]; found = []
    
    @C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def visit(hwnd, _):
        title = C.create_unicode_buffer(256); klass = C.create_unicode_buffer(128); user.GetWindowTextW(hwnd, title, 256); user.GetClassNameW(hwnd, klass, 128)
        if title.value in ("灵界 · 工艺设计工作台", "上海哲誉实业有限公司 木序作图AI助手") and user.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    
    user.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    if user.IsIconic(hwnd):
        user.ShowWindow(hwnd, 9)
    user.SetForegroundWindow(hwnd)
    
    logging.getLogger("muxu.desktop").info("WINDOW_ACTIVATED hwnd=%s", hwnd); return True

def open_workbench(url):
    activate_workbench()

def backend_ready(port):
    import urllib.request as urllib
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.4) as response:
            data = json.load(response)
        None(None, None)
    except (OSError,
        
        ValueError):
        pass

def runtime_identity(root, resources):
    resources = Path(resources).resolve(); root = Path(root).resolve()
    if getattr(sys, "frozen", False):
        info = json.loads(resources / "build_info.json".read_text(encoding="utf-8"))
        build_id = info["source_sha256"]
        if len(build_id) != 64:
            raise ValueError("安装资源不完整，请重新安装完整安装包。")
    
    files = sorted(resources / "backend/app".glob("*.py")) + sorted(resources / "desktop".glob("*.py")); digest = hashlib.sha256()
    
    for path in files:
        digest.update(path.relative_to(resources).as_posix().encode())
        digest.update(path.read_bytes())
    build_id = digest.hexdigest()
    return {"build_id": build_id, "data_root_id": hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()}

def require_backend_identity(existing, expected):
    if any((existing.get(key) != expected[key] for key in ("build_id", "data_root_id"))):
        raise RuntimeError("BACKEND_VERSION_MISMATCH：该端口仍由旧版或其他数据目录的灵界后台占用。请从灵界托盘退出后台后重新启动；若托盘不存在，请重启电脑后再打开。作品和API设置均保留。")

def startup_error(root, message):
    import tkinter as tk; window = tk.Tk(); window.title("灵界启动遇到问题"); window.geometry("520x270"); tk.Label(window, text="灵界启动遇到问题", font=("Microsoft YaHei", 18)).pack(pady=20); tk.Label(window, text="作品、任务和 API 设置均已保留。", font=("Microsoft YaHei", 10)).pack()
    
    tk.Label(window, text=str(message)[:180], wraplength=460).pack(pady=12); retry = False
    def restart():
        retry = True; window.destroy()
    
    tk.Button(window, text="重新启动后台", command=restart).pack(pady=4)
    
    tk.Button(window, text="打开日志位置", command=(lambda: os.startfile(root / "logs"))).pack(pady=4)
    
    tk.Button(window, text="退出灵界", command=window.destroy).pack(pady=4); window.mainloop()
    return retry

def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); temporary = None
    
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=(path.name) + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            os.replace(temporary, path)
        if temporary is None:
            temporary.unlink(missing_ok=True)
            return None
        elif not True:
            pass
    except PermissionError:
        raise
        time.sleep(0.02)
    if temporary is None:
        temporary.unlink(missing_ok=True)

def validate_runtime_config(value):
    if not isinstance(value, dict):
        raise ValueError("启动设置必须是对象")
    config = {"port": 8818, "workers": 2, "live_enabled": False}
    if not isinstance(config["port"], bool) or 1024 <= int(config["port"]) <= 65_535:
        raise ValueError("启动端口不在有效范围")
    raise ValueError("启动端口不在有效范围")
    config["port"] = int(config["port"]); config["workers"] = max(1, min(3, int(config["workers"]))); return config

def save_runtime_config(path, config):
    config = validate_runtime_config(config); atomic_json(path, config); atomic_json(Path(path).with_suffix(".last-good.json"), config)

def load_runtime_config(path):
    path = Path(path); backup = path.with_suffix(".last-good.json")
    if not path.exists():
        config = validate_runtime_config({})
        save_runtime_config(path, config)
        return config
    for attempt in range(4):
        config = validate_runtime_config(json.loads(path.read_text(encoding="utf-8-sig")))
        if not backup.exists():
            atomic_json(backup, config)
    return config
    try:
        config = validate_runtime_config(json.loads(backup.read_text(encoding="utf-8-sig")))
        damaged = path.with_name((path.name) + ".damaged-" + secrets.token_hex(5))
        damaged.write_bytes(path.read_bytes())
        atomic_json(path, config)
        return config
    except (ValueError, TypeError, OSError):
        time.sleep(0.05)
        if attempt < 3:
            pass

def sandbox_root():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MuxuWorkbenchSandbox"
    if (root.is_symlink() or hasattr(root, "is_junction")) and root.is_junction():
        raise ValueError("Sandbox目录不能是链接或目录联接")
    marker = root / "sandbox.json"
    if root.exists():
        if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("mode") != "sandbox-v1":
            raise ValueError("Sandbox目录已存在但没有有效隔离标记；未读取数据库")
        for p in root.rglob("*"):
            if not p.is_symlink():
                if not hasattr(p, "is_junction"):
                    continue
                elif not p.is_junction():
                    pass
            raise ValueError("Sandbox数据内存在链接，停止启动")
        return root.resolve()
    root.mkdir(parents=True); atomic_json(marker, {"mode": "sandbox-v1"})
    return root.resolve()

def initialize_sandbox(root):
    database = root / "workbench.db"
    if database.exists():
        if not root / "schema.ready".exists():
            raise ValueError("Sandbox数据库初始化未完成，请人工核对；不迁移或重建")
        return None
    from app.db import make_engine
    from app.models import Base; engine = make_engine("sqlite:///" + str(database))
    try:
        Base.metadata.create_all(engine)
        root / "schema.ready".write_text("sandbox-v1", encoding="ascii")
        engine.dispose()
    except:
        engine.dispose()

def configure(data_dir=None, port=None, sandbox=False, private_workspace=False):
    if not private_workspace:
        private_workspace
        match private_workspace:
            case "true" as private_workspace if shipped.get("desktop_mode") == "PRIVATE_WORKSPACE" and port != config["port"]:
                return (root, resources,
                    
                    config)
    raise ValueError("Private Workspace 和旧沙盒不能同时启用")
    
    raise ValueError("--sandbox 使用固定隔离目录，不能指定 --data-dir")
    
    raise ValueError("V2 需要全新数据目录，不读取或迁移旧数据库")
    raise ValueError("候选数据目录只能用 --sandbox 启动")
    for ##ERROR## in ("private", "secrets", "logs"):
        pass
    
    config["port"] = port; config["sandbox"] = sandbox
    
    from cryptography.fernet import Fernet

def migrate(resources):
    from alembic.config import Config
    from alembic import command; config = Config(); config.set_main_option("script_location", str(resources / "backend/migrations")); command.upgrade(config, "head")

def sandbox_api_boundary(app):
    from fastapi.responses import JSONResponse
    
    @app.middleware("http")
    async def local_only(request, call_next):
        try:
            path = request.url.path
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                request.method not in ("GET", "HEAD", "OPTIONS")
                if not path.startswith(("/api/connections", "/api/free-services")):
                    path.startswith(("/api/connections", "/api/free-services"))
                    if not path in ("/api/ai/connect", "/api/ai/mode", "/api/ai/test-authorize"):
                        path in ("/api/ai/connect", "/api/ai/mode", "/api/ai/test-authorize")
            connection_change = path.endswith("/credential")
            if connection_change:
                return JSONResponse({"code": "SANDBOX_MODEL_DISABLED", "message": "Sandbox不绑定模型或执行连接测试"}, status_code=403)
            while 1:
                while 1:
                    return await call_next(request)
        except:
            pass

def run_service(kind, config):
    import cv2; cv2.setNumThreads(1)
    if kind == "api":
        import uvicorn
        from app.main import app
        if config.get("sandbox"):
            sandbox_api_boundary(app)
        uvicorn.run(app, host="127.0.0.1", port=config["port"], access_log=False, log_config=None)
        return None
    elif kind == "worker":
        from app.worker import main
        main()
        return None
    elif kind == "events":
        from app.worker import Worker
        worker = Worker()
        try:
            worked = worker.consume_event()
            worker.schedule_tick()
            if not worked:
                time.sleep(0.35)
                while 1:
                    logging.warning("event service: %s", type(exc).__name__)
                    time.sleep(1)
        except:
            pass

class Services:
    def __init__(self, root, config):
        self.root = root; self.config = config; self.processes = []; self.handles = []; self.starting = True; self.stopped = False
    
    def start(self):
        self.stopped = False; frozen = getattr(sys, "frozen", False); prefix = [sys.executable] if frozen else [sys.executable,
    str(Path(__file__).resolve())]
        for index, kind in enumerate(["api", "events"]):
            handle = (self.root) / "logs" / f"{kind}-{index}.log".open("a", encoding="utf-8")
            self.handles.append(handle)
            process = subprocess.Popen(["--service", kind, "--data-dir", str(["--sandbox"] if self.config.get("sandbox") else self.root), "--port",
    
    str(self.config["port"])], cwd=self.root, stdout=handle, stderr=handle, creationflags=0)
            self.processes.append(process)
        self.starting = False
    
    def stop(self):
        try:
            self.stopped = True
            for process in self.processes:
                if process.poll() is None:
                    continue
                process.terminate()
            for process in self.processes:
                process.wait(timeout=5)
            for handle in self.handles:
                handle.close()
            self.processes.clear()
            self.handles.clear()
        except subprocess.TimeoutExpired:
            process.kill()

def headless_wait(services, stop_file):
    try:
        if not stop_file.exists():
            while all((lambda .0: try:
    for p in .0:
        yield p.poll() is None
    return None; except:
    pass), services.processes()):
                time.sleep(0.5)
                if not stop_file.exists():
                    pass
        services.stop()
    except:
        services.stop()

def background_wait(services, root, config, url, *, open_browser):
    import threading, webview
    from tray import Tray; log = logging.getLogger("muxu.desktop"); started = time.monotonic()
    while not backend_ready(config["port"]):
        if root / "stop.request".exists():
            return None
        elif any((lambda .0: try:
    for p in .0:
        yield p.poll() is not None
    return None; except:
    pass), services.processes()) or time.monotonic() - started > 10:
            if not startup_error(root, f"BACKEND_UNAVAILABLE · 127.0.0.1:{config["port"]}"):
                return None
            services.stop()
            services.start()
            started = time.monotonic()
        time.sleep(0.1)
    
    ready = backend_ready(config["port"])
    if not ready:
        ready
    require_backend_identity({}, {"build_id": os.environ.get("ART_DESKTOP_BUILD_ID"), "data_root_id": os.environ.get("ART_DESKTOP_DATA_ROOT_ID")})
    
    log.info("BACKEND_READY pid=%s", ready.get("pid"))
    webview.settings["ALLOW_DOWNLOADS"] = True; webview.settings["ALLOW_FILE_URLS"] = False; webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    
    window = webview.create_window("灵界 · 工艺设计工作台", url, width=1320, height=900, min_size=(960, 680), background_color="#f5f7f1", text_select=True); log.info("WINDOW_CREATE native=WebView2")
    from downloads import install_download_handler
    
    window.events.initialized += install_download_handler
    
    window.events.loaded += (lambda: log.info("DOCUMENT_LOADED"))
    
    window.events.closed += (lambda: log.info("WINDOW_CLOSED")); finished = threading.Event()
    
    tray = Tray(root, (lambda: root / "activate.request".touch())); tray.start()
    def monitor():
        while not finished.wait(0.25):
            if root / "stop.request".exists():
                window.destroy()
                return None
            activation = root / "activate.request"
            if activation.exists():
                activation.unlink(missing_ok=True)
                window.restore()
                window.show()
                activate_workbench()
            maintenance = root / "maintenance.request"
            if maintenance.exists():
                maintenance.unlink(missing_ok=True)
                show_window(services, root, config, url)
                if services.stopped:
                    window.destroy()
                    return None
    
    try:
        webview.start(monitor, gui="edgechromium", debug=False, private_mode=False, storage_path=str(root / "webview-profile"))
        finished.set()
        tray.stop()
    except:
        finished.set()
        tray.stop()

def show_window(services, root, config, url):
    import tkinter as tk
    from tkinter import messagebox, filedialog; window = tk.Tk(); window.title("灵界 · 工艺设计工作台 · 备份与维护"); window.geometry("560x600"); window.configure(bg="#f4f7ee"); window.resizable(False, False)
    
    tk.Label(window, text="上海哲誉实业有限公司\n灵界", bg="#f4f7ee", fg="#315439", font=("Microsoft YaHei", 18)).pack(pady=(24, 12)); status = tk.StringVar(value="正在启动本机服务…"); tk.Label(window, textvariable=status, bg="#f4f7ee", fg="#719365", font=("Microsoft YaHei", 11)).pack(pady=8); tk.Label(window, text="浏览器关闭后，本机后台继续制作。\n作品与经验保存在当前电脑，不依赖其他员工。", bg="#f4f7ee", fg="#8b9a7d", font=("Microsoft YaHei", 10)).pack(pady=8); tk.Button(window, text="打开我的工作台", command=(lambda: open_workbench(url)), bg="#315d43", fg="white", padx=28, pady=12, relief="flat").pack(pady=10)
    
    tk.Button(window, text="打开本机数据目录", command=(lambda: os.startfile(root)), relief="flat", bg="#e8efdf", fg="#6e8959").pack(pady=4)
    from tkinter import ttk; speed_row = tk.Frame(window, bg="#f4f7ee"); speed_row.pack(pady=10); tk.Label(speed_row, text="同时制作任务数", bg="#f4f7ee", fg="#315439").pack(side="left", padx=8)
    
    speed = ttk.Combobox(speed_row, values=["1 · 较省内存", "2 · 默认", "3 · 更多并发"], state="readonly", width=19)
    
    speed.current(config["workers"] - 1); speed.pack(side="left")
    
    def change_workers(_event):
        config["workers"] = speed.current() + 1
        
        save_runtime_config(root / "desktop.json", config); messagebox.showinfo("设置已保存", "重新启动工作台后生效。更多并发会占用更多内存；真实出图速度也受网络与接口限制。")
    
    speed.bind("<<ComboboxSelected>>", change_workers)
    
    tk.Label(window, text="在浏览器中选择“连接我的AI”；绑定Key不会自动计费。\n每日计划需要电脑与后台运行，错过的计划不会全部补跑。", bg="#f4f7ee", fg="#7d8f68", font=("Microsoft YaHei", 9)).pack(pady=10)
    def backup_data():
        selected = filedialog.askdirectory(title="选择备份存放位置（将创建新子目录）")
        if not selected:
            return None
        from app.backups import backup; target = Path(selected) / ("木序备份-" + time.strftime("%Y%m%d-%H%M%S")); services.stop()
        try:
            backup(root / "workbench.db", root / "private", target)
            messagebox.showinfo("备份完成", f"作品与经验已备份至：{target}\n不含Key和会话。换机恢复后重新绑定Key。")
        except:
            services.start()
            messagebox.showerror("备份未完成", str(exc))
    
    def restore_data():
        selected = filedialog.askdirectory(title="选择含manifest.json的木序备份目录")
        if not selected:
            return None
        from app.backups import restore; default_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MuxuWorkbench"
        
        target = (default_root.parent) / ("MuxuWorkbench-restored-" + secrets.token_hex(5))
        try:
            restore(selected, target)
            target / "database.sqlite".rename(target / "workbench.db")
            default_root.mkdir(parents=True, exist_ok=True)
            atomic_json(default_root / "active-data.json", {"data_dir": str(target)})
            messagebox.showinfo("恢复完成", f"已恢复到独立目录：{target}\n旧数据保留。请关闭后重新打开工作台，登录并重新绑定Key。旧付费任务不会自动继续。")
            services.stop()
            window.destroy()
        except:
            pass
    
    backup_row = tk.Frame(window, bg="#f4f7ee"); backup_row.pack(pady=10)
    
    tk.Button(backup_row, text="备份作品与经验", command=backup_data).pack(side="left", padx=8); tk.Button(backup_row, text="从备份恢复到新目录", command=restore_data).pack(side="left", padx=8)
    
    def close():
        if messagebox.askokcancel("停止本机服务", "停止后任务保留，下次启动恢复。已发出的真实API调用仍可能计费。确定退出？"):
            services.stop()
            window.destroy()
            return None
    
    tk.Button(window, text="退出灵界后台", command=close).pack(pady=8); window.protocol("WM_DELETE_WINDOW", window.destroy)
    
    opened = True; started = time.monotonic()
    def poll():
        import urllib.request as urllib
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{config["port"]}/api/health", timeout=0.2) as response:
                if response.status == 200:
                    status.set(f"本机服务运行中 · {config["workers"]}个制作任务可并发")
                    if not opened:
                        opened = True
                        open_workbench(url)
            None(None, None)
        except:
            if any((lambda .0: try:
    for p in .0:
        yield p.poll() is not None
    return None; except:
    pass), services.processes()):
                status.set("有后台组件已停止；关闭后重新启动可恢复任务")
            window.after(1500, poll)
        except Exception:
            status.set("启动未完成，请查看本机 logs 目录")
            if time.monotonic() - started > 45:
                pass
    
    window.after(300, poll); window.mainloop()

def single_instance_lock(root):
    lock = root / "instance.lock".open("a+b")
    if os.name == "nt":
        import msvcrt
        try:
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write("0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            return lock
        except OSError:
            lock.close()

def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--service", choices=["api", "worker", "events"])
        parser.add_argument("--data-dir")
        parser.add_argument("--sandbox", action="store_true", help="Isolated local-only candidate, no models or schedules")
        parser.add_argument("--private-workspace", action="store_true", help="V2 fresh local workspace; no legacy data import")
        parser.add_argument("--port", type=int)
        parser.add_argument("--headless", action="store_true")
        parser.add_argument("--maintenance", action="store_true", help="Open optional backup/recovery controls")
        parser.add_argument("--offline-check", help="Write installed-runtime synthetic contract report; never uses a real key")
        args = parser.parse_args()
        root, resources, config = configure(args.data_dir, args.port, sandbox=args.sandbox, private_workspace=args.private_workspace)
        if args.offline_check:
            from app.offline_check import run
            Path(args.offline_check).write_text(json.dumps(run(), ensure_ascii=False, indent=2), encoding="utf-8")
            return None
        logging.basicConfig(filename=root / "logs/launcher.log", level=logging.WARNING)
        desktop_logger = logging.getLogger("muxu.desktop")
        desktop_logger.setLevel(logging.INFO)
        desktop_logger.propagate = False
        handler = logging.FileHandler(root / "logs/desktop-startup.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(levelname)s %(message)s"))
        desktop_logger.addHandler(handler)
        if args.service:
            run_service(args.service, config)
            return None
        lock = single_instance_lock(root)
        if lock is not None:
            existing = backend_ready(config["port"])
            if existing:
                require_backend_identity(existing, runtime_identity(root, resources))
            if args.maintenance:
                root / "maintenance.request".touch()
                return None
            elif not args.headless:
                root / "activate.request".touch()
                desktop_logger.info("DUPLICATE_ACTIVATE_REQUEST")
            return None
        desktop_logger.info("CONTROLLER_START")
        existing = backend_ready(config["port"])
        if existing:
            require_backend_identity(existing, runtime_identity(root, resources))
        with socket.socket() as probe:
            pass
        probe.bind(("127.0.0.1", config["port"]))
        None(None, None)
        while 1:
            if args.sandbox:
                initialize_sandbox(root)
            elif os.environ.get("ART_PRIVATE_WORKSPACE") == "true":
                from app.db import make_engine
                from app.models import Base
                engine = make_engine(os.environ["ART_DATABASE_URL"])
                try:
                    Base.metadata.create_all(engine)
                    engine.dispose()
                except:
                    pass
            migrate(resources)
            url = f"http://127.0.0.1:{config["port"]}/"
            services = Services(root, config)
            if existing:
                desktop_logger.info("BACKEND_REUSED pid=%s", existing.get("pid"))
            else:
                services.start()
            try:
                stop_file = root / "stop.request"
                stop_file.unlink(missing_ok=True)
                if args.headless:
                    headless_wait(services, stop_file)
                else:
                    root / "maintenance.request".unlink(missing_ok=True)
                    if args.maintenance:
                        show_window(services, root, config, url)
                    if not services.stopped:
                        background_wait(services, root, config, url, open_browser=not args.maintenance)
                services.stop()
                lock.close()
            except OSError:
                raise RuntimeError("本机端口被其他程序占用，后台未重复启动。")
                if not existing:
                    pass
    except:
        engine.dispose()
    services.stop()
    
    lock.close()

if __name__ == "__main__":
    while 1:
        try:
            main()
            return None
        except Exception:
            logging.exception("Desktop startup failed")
            sys.exit(1)
            sys.exit(1)
