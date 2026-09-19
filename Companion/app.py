import threading
import time
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

import runtime
import voice_profiles
import beta_hardening
import multi_engine
import tts_registry
import speaker_performance
import single_instance

# Keep WSV6 transport untouched; TTS is selected independently.
multi_engine.configure_runtime(runtime)
runtime.engine.VoiceRegistry = voice_profiles.VoiceRegistry
speaker_performance.configure_runtime(runtime, voice_profiles)
runtime.synthesize_to_wav = beta_hardening.make_atomic_synthesizer(runtime.synthesize_to_wav)
runtime.engine.play_wav = beta_hardening.make_validating_player(runtime.engine.play_wav)
runtime.SpeechController = beta_hardening.make_bounded_controller(runtime.SpeechController)

VERSION = runtime.VERSION


class TrayController:
    def __init__(self, gui):
        self.gui = gui
        self.icon = None
        if pystray is None or Image is None or ImageDraw is None:
            return
        image = Image.new("RGB", (64, 64), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 54, 54), outline="white", width=4)
        draw.text((20, 20), "W", fill="white")
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda: self.gui.root.after(0, self.gui.show)),
            pystray.MenuItem("Stop speech", lambda: self.gui.root.after(0, self.gui.stop_speech)),
            pystray.MenuItem("Exit", lambda: self.gui.root.after(0, self.gui.shutdown)),
        )
        self.icon = pystray.Icon("WoWStoryVoice", image, f"WoW Story Voice v{VERSION}", menu)
        try:
            self.icon.run_detached()
        except Exception as e:
            print(f"Tray warning: {type(e).__name__}: {e}")
            self.icon = None

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


class CompanionGUI:
    def __init__(self, root, speech, state, stop_event, listening_event):
        self.root = root
        self.speech = speech
        self.state = state
        self.stop_event = stop_event
        self.listening_event = listening_event
        self.last_log_text = ""

        root.title(f"WoW Story Voice v{VERSION}")
        root.geometry("700x675")
        root.minsize(620, 575)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="WoW Story Voice", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(main, text=f"Companion v{VERSION} · Multi-engine TTS · WSV6 transport").pack(anchor="w", pady=(0, 12))

        status = ttk.LabelFrame(main, text="Status", padding=10)
        status.pack(fill="x")
        self.bridge_var = tk.StringVar(value="Bridge: searching")
        self.addon_var = tk.StringVar(value="Addon: unknown")
        self.engine_var = tk.StringVar(value="TTS: loading")
        self.speaker_var = tk.StringVar(value="Speaker: —")
        self.speech_var = tk.StringVar(value="Speech: idle")
        self.update_var = tk.StringVar(value="Update: not checked")
        for var in (self.bridge_var, self.addon_var, self.engine_var):
            ttk.Label(status, textvariable=var).pack(anchor="w", pady=1)
        ttk.Label(status, textvariable=self.speaker_var, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(5, 2))
        for var in (self.speech_var, self.update_var):
            ttk.Label(status, textvariable=var).pack(anchor="w", pady=1)

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=10)
        self.listen_button = ttk.Button(controls, text="Pause listening", command=self.toggle_listening)
        self.listen_button.pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="Stop speech", command=self.stop_speech).pack(side="left", padx=6)
        ttk.Button(controls, text="Test voice", command=self.test_voice).pack(side="left", padx=6)
        ttk.Button(controls, text="Install/update addon", command=self.install_addon).pack(side="left", padx=6)

        settings = ttk.LabelFrame(main, text="Companion settings", padding=10)
        settings.pack(fill="x", pady=(0, 10))
        engine_row = ttk.Frame(settings)
        engine_row.pack(fill="x", pady=(0, 8))
        ttk.Label(engine_row, text="Voice engine").pack(side="left")
        self.engine_choice = tk.StringVar(value=runtime.SETTINGS.get("tts_engine", tts_registry.DEFAULT_ENGINE))
        self.engine_combo = ttk.Combobox(engine_row, state="readonly", width=24, textvariable=self.engine_choice, values=tts_registry.bundled_engine_keys())
        self.engine_combo.pack(side="right")
        self.engine_combo.bind("<<ComboboxSelected>>", self.engine_changed)
        row = ttk.Frame(settings)
        row.pack(fill="x")
        ttk.Label(row, text="Voice volume").pack(side="left")
        self.volume_var = tk.DoubleVar(value=float(runtime.SETTINGS.get("volume", 1.0)) * 100.0)
        self.volume_label = ttk.Label(row, text=f"{int(self.volume_var.get())}%", width=6)
        self.volume_label.pack(side="right")
        ttk.Scale(settings, from_=0, to=100, variable=self.volume_var, command=self.volume_changed).pack(fill="x", pady=(2, 8))

        expressive_row = ttk.Frame(settings)
        expressive_row.pack(fill="x")
        ttk.Label(expressive_row, text="Performance intensity").pack(side="left")
        self.expressiveness_var = tk.DoubleVar(
            value=float(runtime.SETTINGS.get("expressiveness", speaker_performance.DEFAULT_EXPRESSIVENESS)) * 100.0
        )
        self.expressiveness_label = ttk.Label(
            expressive_row, text=f"{int(round(self.expressiveness_var.get()))}%", width=6
        )
        self.expressiveness_label.pack(side="right")
        ttk.Scale(
            settings,
            from_=75,
            to=160,
            variable=self.expressiveness_var,
            command=self.expressiveness_changed,
        ).pack(fill="x", pady=(2, 8))

        self.speaker_announce_var = tk.BooleanVar(
            value=bool(runtime.SETTINGS.get("spoken_speaker_name", False))
        )
        ttk.Checkbutton(
            settings,
            text="Speak NPC name before dialogue (adds delay)",
            variable=self.speaker_announce_var,
            command=self.speaker_setting_changed,
        ).pack(anchor="w")

        self.start_var = tk.BooleanVar(value=bool(runtime.SETTINGS.get("start_with_windows", False)))
        ttk.Checkbutton(settings, text="Start with Windows", variable=self.start_var, command=self.startup_changed).pack(anchor="w")
        self.update_check_var = tk.BooleanVar(value=bool(runtime.SETTINGS.get("check_updates", True)))
        ttk.Checkbutton(settings, text="Check for updates at startup", variable=self.update_check_var, command=self.update_setting_changed).pack(anchor="w")

        utility = ttk.Frame(main)
        utility.pack(fill="x", pady=(0, 10))
        ttk.Button(utility, text="Check updates", command=lambda: runtime.check_for_updates_async(self.state)).pack(side="left", padx=(0, 6))
        ttk.Button(utility, text="Open update page", command=self.open_update).pack(side="left", padx=6)
        ttk.Button(utility, text="Open data folder", command=lambda: runtime.open_path(runtime.DATA)).pack(side="left", padx=6)
        ttk.Button(utility, text="Open log", command=lambda: runtime.open_path(runtime.LOG_FILE)).pack(side="left", padx=6)

        ttk.Label(main, text="Recent activity").pack(anchor="w")
        self.log_text = tk.Text(main, height=14, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

        self.tray = TrayController(self)
        root.after(300, self.refresh)

    def refresh(self):
        if self.stop_event.is_set():
            return
        snap = self.state.snapshot()
        bridge = "connected" if snap["bridge_connected"] else ("paused" if not snap["listening"] else "searching")
        self.bridge_var.set(f"Bridge: {bridge} · {snap['bridge_position']}")
        av = snap["addon_version"]
        if snap["version_match"] is False:
            self.addon_var.set(f"Addon: v{av} · VERSION MISMATCH (companion v{VERSION})")
        elif snap["version_match"] is True:
            self.addon_var.set(f"Addon: v{av} · compatible")
        else:
            self.addon_var.set(f"Addon: {av}")
        tts_name = snap.get("tts_engine", getattr(runtime, "TTS_ENGINE_NAME", "TTS"))
        tts_device = snap.get("tts_device", "")
        self.engine_var.set(f"TTS: {tts_name}" + (f" · {tts_device}" if tts_device else ""))
        speaker = snap.get("current_speaker", "—")
        self.speaker_var.set(f"Speaker: {speaker}")
        speech = "speaking" if snap["speaking"] else "idle"
        delivery = snap.get("last_delivery", "neutral")
        latency = snap.get("last_latency_ms")
        latency_text = f" · start {latency} ms" if latency is not None else ""
        self.speech_var.set(
            f"Speech: {speech} · emotion {delivery} · queue {snap['queue_size']}{latency_text} · last: {snap['last_message']}"
        )
        self.update_var.set(f"Update: {snap['update_status']}")
        self.listen_button.configure(text="Pause listening" if snap["listening"] else "Resume listening")

        text = "\n".join(runtime.LOG.snapshot(100))
        if text != self.last_log_text:
            self.last_log_text = text
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", text)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(500, self.refresh)

    def engine_changed(self, _=None):
        key = multi_engine.set_selected_engine(runtime, self.engine_choice.get())
        label = tts_registry.SPECS[key].label
        messagebox.showinfo("WoW Story Voice", f"Voice engine set to {label}. Restart the companion to load it.")

    def volume_changed(self, _=None):
        value = max(0, min(100, int(round(self.volume_var.get()))))
        self.volume_label.configure(text=f"{value}%")
        runtime.SETTINGS["volume"] = value / 100.0
        runtime.save_settings(runtime.SETTINGS)

    def expressiveness_changed(self, _=None):
        value = max(75, min(160, int(round(self.expressiveness_var.get()))))
        self.expressiveness_label.configure(text=f"{value}%")
        runtime.SETTINGS["expressiveness"] = speaker_performance.normalized_expressiveness(value / 100.0)
        runtime.save_settings(runtime.SETTINGS)

    def speaker_setting_changed(self):
        runtime.SETTINGS["spoken_speaker_name"] = bool(self.speaker_announce_var.get())
        runtime.save_settings(runtime.SETTINGS)

    def startup_changed(self):
        try:
            runtime.set_start_with_windows(self.start_var.get())
        except Exception as e:
            self.start_var.set(False)
            messagebox.showerror("WoW Story Voice", f"Could not change startup setting:\n{e}")

    def update_setting_changed(self):
        runtime.SETTINGS["check_updates"] = bool(self.update_check_var.get())
        runtime.save_settings(runtime.SETTINGS)

    def toggle_listening(self):
        if self.listening_event.is_set():
            self.listening_event.clear()
        else:
            self.listening_event.set()

    def stop_speech(self):
        self.speech.stop_and_clear()

    def test_voice(self):
        self.speech.enqueue("test", "", "Narrator", "WoW Story Voice voice test. Audio is working.")

    def install_addon(self):
        try:
            dirs = runtime.candidate_addons_dirs()
            target_dir = dirs[0] if dirs else None
            if target_dir is None:
                selected = filedialog.askdirectory(title="Select World of Warcraft _retail_ or AddOns folder")
                if not selected:
                    return
                target_dir = runtime.normalize_addons_dir(selected)
            target = runtime.install_addon_to(target_dir)
            messagebox.showinfo("WoW Story Voice", f"Addon installed/updated:\n{target}\n\nUse /reload in WoW if the game is already running.")
        except Exception as e:
            print(f"Addon install failed: {type(e).__name__}: {e}")
            messagebox.showerror("WoW Story Voice", f"Could not install the addon:\n{e}")

    def open_update(self):
        webbrowser.open(self.state.snapshot().get("update_url") or runtime.WORKFLOW_URL)

    def on_close(self):
        # Closing the main window means exit. Previous builds silently hid to
        # the tray, which left the TTS model and 12 ms capture loop running and
        # made it easy to launch multiple competing companion processes.
        self.shutdown()

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def shutdown(self):
        self.stop_event.set()
        self.listening_event.set()
        try:
            shutdown = getattr(self.speech, "shutdown", None)
            if callable(shutdown):
                shutdown()
            else:
                self.speech.stop_and_clear()
        except Exception as e:
            print(f"Speech shutdown warning: {type(e).__name__}: {e}")
        self.tray.stop()
        single_instance.release()
        self.root.destroy()


def main():
    if not single_instance.acquire():
        duplicate_root = tk.Tk()
        duplicate_root.withdraw()
        messagebox.showinfo(
            "WoW Story Voice",
            "WoW Story Voice is already running. Close the existing companion before starting another instance.",
        )
        duplicate_root.destroy()
        return

    beta_hardening.startup_housekeeping(runtime.DATA, runtime.CACHE, runtime.LOG_FILE)

    root = tk.Tk()
    root.withdraw()
    splash = tk.Toplevel(root)
    splash.title("WoW Story Voice")
    splash.geometry("500x145")
    splash.resizable(False, False)
    ttk.Label(splash, text=f"WoW Story Voice v{VERSION}", font=("Segoe UI", 14, "bold")).pack(pady=(18, 6))
    splash_status = tk.StringVar(value=f"Loading {runtime.TTS_ENGINE_NAME}… First launch may download a model.")
    splash_timer = tk.StringVar(value="Elapsed: 00:00 · Remaining: estimating…")
    ttk.Label(splash, textvariable=splash_status).pack()
    ttk.Label(splash, textvariable=splash_timer).pack(pady=(4, 0))
    ttk.Label(splash, text="The window stays responsive while the local AI voice starts.").pack(pady=(4, 0))
    splash.update()

    load_result = {}

    def load_voice_engine():
        try:
            load_result["value"] = runtime.initialize_tts()
        except BaseException as e:
            load_result["error"] = e

    loader = threading.Thread(target=load_voice_engine, name="WSV-TTS-Loader", daemon=True)
    load_started = time.monotonic()
    loader.start()
    while loader.is_alive():
        elapsed = max(0, int(time.monotonic() - load_started))
        mins, secs = divmod(elapsed, 60)
        # Chatterbox/Hugging Face does not expose a trustworthy total byte count
        # to this process during from_pretrained(), so do not invent an ETA.
        # Show an exact elapsed timer and explicitly mark the remaining time as
        # unknown until the engine reports progress we can measure.
        splash_timer.set(f"Elapsed: {mins:02d}:{secs:02d} · Remaining: estimating…")
        if elapsed >= 120:
            splash_status.set(f"Still loading {runtime.TTS_ENGINE_NAME}… First model setup can take several minutes.")
        splash.update_idletasks()
        splash.update()
        loader.join(0.05)
        time.sleep(0.01)

    if "error" in load_result:
        raise load_result["error"]
    tts, available = load_result["value"]

    speech = runtime.SpeechController(tts, available, runtime.STATE)
    stop_event = threading.Event()
    listening_event = threading.Event()
    listening_event.set()
    threading.Thread(
        target=runtime.capture_loop,
        args=(speech, runtime.STATE, stop_event, listening_event),
        name="WSV-Capture",
        daemon=True,
    ).start()

    CompanionGUI(root, speech, runtime.STATE, stop_event, listening_event)
    splash.destroy()
    root.deiconify()
    if runtime.SETTINGS.get("check_updates", True):
        runtime.check_for_updates_async(runtime.STATE)
    print("Ready. Selected TTS engine is active. Use /wsv test in WoW.")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}")
        try:
            messagebox.showerror(
                "WoW Story Voice",
                f"WoW Story Voice stopped with an error:\n\n{type(e).__name__}: {e}\n\nLog: {runtime.LOG_FILE}",
            )
        except Exception:
            pass
        raise
