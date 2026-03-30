import json
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent
TEMPLATES_ROOT = ROOT / "templates"
OUTPUT_ROOT = ROOT / "output"


def load_templates():
    templates = []
    for manifest in TEMPLATES_ROOT.rglob("template.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            data["_manifest_path"] = manifest
            templates.append(data)
        except Exception:
            continue

    def sort_key(t):
        tid = str(t.get("id", "")).strip().lower()
        name = str(t.get("display_name", t.get("id", ""))).lower()
        priority = 0 if tid == "extract_all" else 1
        return (priority, name)

    templates.sort(key=sort_key)
    return templates


class VgmExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("vgmextractor")
        self.geometry("980x660")

        self.templates = load_templates()
        self.input_dir = tk.StringVar(value=str(ROOT / "input"))
        self.template_name = tk.StringVar()
        self.dump_mmid_chunks = tk.BooleanVar(value=False)
        self.all_sbnk_candidates = tk.BooleanVar(value=False)

        self.status_text = tk.StringVar(value="Idle")
        self.progress_value = tk.DoubleVar(value=0.0)
        self._progress_total = 0
        self._progress_done = 0
        self._is_running = False
        self._cancel_requested = False
        self._current_proc = None
        self._line_result_pattern = re.compile(r"^\[(OK|SKIP|FAIL)\]\s+[^:]+:")

        self._build_ui()
        self._set_initial_state()
        self._ensure_default_input_dir()

    def _ensure_default_input_dir(self):
        try:
            Path(self.input_dir.get()).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _build_ui(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        row1 = ttk.Frame(frame)
        row1.pack(fill="x", pady=(0, 8))
        ttk.Label(row1, text="Input Folder:").pack(side="left")
        ttk.Entry(row1, textvariable=self.input_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row1, text="Browse...", command=self._browse_input).pack(side="left")

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(0, 8))
        ttk.Label(row2, text="Template:").pack(side="left")
        self.template_combo = ttk.Combobox(
            row2,
            textvariable=self.template_name,
            values=[t.get("display_name", t.get("id", "")) for t in self.templates],
            state="readonly",
            width=56,
        )
        self.template_combo.pack(side="left", padx=8)
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template_change)

        row3 = ttk.Frame(frame)
        row3.pack(fill="x", pady=(0, 8))
        self.export_button = ttk.Button(row3, text="Extract", command=self._run_extract, state="disabled")
        self.export_button.pack(side="left")
        ttk.Label(row3, text="Output: output/").pack(side="left", padx=12)

        status_row = ttk.Frame(frame)
        status_row.pack(fill="x", pady=(0, 8))
        ttk.Label(status_row, text="Status:").pack(side="left")
        ttk.Label(status_row, textvariable=self.status_text).pack(side="left", padx=(6, 10))
        self.progress_bar = ttk.Progressbar(
            status_row,
            orient="horizontal",
            mode="determinate",
            variable=self.progress_value,
            maximum=100,
        )
        self.progress_bar.pack(side="left", fill="x", expand=True)

        options = ttk.LabelFrame(frame, text="Options")
        options.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            options,
            text="Dump MMID chunks (optional debug)",
            variable=self.dump_mmid_chunks,
        ).pack(anchor="w", padx=8, pady=(4, 2))
        ttk.Checkbutton(
            options,
            text="Extract all SBNK candidates (instead of largest bank only)",
            variable=self.all_sbnk_candidates,
        ).pack(anchor="w", padx=8, pady=(2, 4))

        self.template_info = tk.StringVar(value="Select a template.")
        ttk.Label(frame, textvariable=self.template_info, foreground="#444").pack(anchor="w", pady=(0, 8))

        ttk.Label(frame, text="Log:").pack(anchor="w")
        self.log = tk.Text(frame, wrap="word", height=28)
        self.log.pack(fill="both", expand=True)

    def _set_initial_state(self):
        self.template_name.set("")
        self.dump_mmid_chunks.set(False)
        self.all_sbnk_candidates.set(False)
        self.status_text.set("Idle")
        self.progress_value.set(0.0)
        self._set_extract_button_idle()
        if not self.templates:
            self.log.delete("1.0", "end")
            self.log.insert("end", "No templates found under templates/.\n")

    def _selected_template(self):
        name = self.template_name.get()
        for t in self.templates:
            if t.get("display_name", t.get("id", "")) == name:
                return t
        return None

    def _set_extract_button_idle(self):
        if self._selected_template() is None:
            state = "disabled"
        else:
            state = "normal"
        self.export_button.configure(text="Extract", command=self._run_extract, state=state)

    def _set_extract_button_running(self):
        self.export_button.configure(text="Cancel", command=self._cancel_extract, state="normal")

    def _on_template_change(self, _event=None):
        tpl = self._selected_template()
        if not tpl:
            self.export_button.configure(state="disabled")
            self.template_info.set("Select a template.")
            return

        defaults = tpl.get("extractor_defaults", {})
        self.dump_mmid_chunks.set(bool(defaults.get("dump_mmid_chunks", False)))
        self.all_sbnk_candidates.set(bool(defaults.get("all_sbnk_candidates", False)))
        self.template_info.set(str(tpl.get("description", "")))
        if not self._is_running:
            self._set_extract_button_idle()

    def _browse_input(self):
        picked = filedialog.askdirectory(initialdir=self.input_dir.get() or str(ROOT))
        if picked:
            self.input_dir.set(picked)

    def _append_log(self, text):
        self.log.insert("end", text)
        self.log.see("end")
        self.update_idletasks()

    def _start_progress(self, total_items: int):
        self._is_running = True
        self._cancel_requested = False
        self._progress_total = max(1, int(total_items))
        self._progress_done = 0
        self.progress_value.set(0.0)
        self.status_text.set(f"Running... 0/{self._progress_total}")
        self._set_extract_button_running()

    def _finish_progress(self, outcome: str):
        self._is_running = False
        self._current_proc = None
        if outcome == "ok":
            self.progress_value.set(100.0)
            self.status_text.set(f"Done ({self._progress_done}/{self._progress_total})")
        elif outcome == "canceled":
            self.status_text.set(f"Canceled ({self._progress_done}/{self._progress_total})")
        else:
            self.status_text.set(f"Failed ({self._progress_done}/{self._progress_total})")

        self._set_extract_button_idle()

    def _estimate_work_items(self, input_root: Path, enabled_extractors: int) -> int:
        try:
            file_count = sum(1 for p in input_root.iterdir() if p.is_file())
        except Exception:
            file_count = 1
        return max(1, file_count * max(1, enabled_extractors))

    def _handle_worker_line(self, line: str):
        self._append_log(line)
        if self._line_result_pattern.match(line.strip()):
            self._progress_done += 1
            pct = min(100.0, (self._progress_done / float(self._progress_total)) * 100.0)
            self.progress_value.set(pct)
            self.status_text.set(f"Running... {self._progress_done}/{self._progress_total}")

    def _cancel_extract(self):
        if not self._is_running:
            return
        self._cancel_requested = True
        self.status_text.set("Cancel requested...")
        proc = self._current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _run_extract(self):
        if self._is_running:
            return

        tpl = self._selected_template()
        if not tpl:
            messagebox.showerror("Template Required", "Select a template first.")
            return

        input_root = Path(self.input_dir.get()).resolve()
        if not input_root.exists():
            messagebox.showerror("Input Missing", f"Input folder does not exist:\n{input_root}")
            return

        extract_flags = tpl.get("extract", {})
        do_sssq = bool(extract_flags.get("sssq", False))
        do_sshd = bool(extract_flags.get("sshd_ssbd", False))
        do_mmid = bool(extract_flags.get("mmid", False))
        do_sbnk = bool(extract_flags.get("sbnk", False))
        if not (do_sssq or do_sshd or do_mmid or do_sbnk):
            messagebox.showerror("Invalid Template", "Template does not enable any extractors.")
            return

        self.log.delete("1.0", "end")
        self._append_log(f"Template: {tpl.get('display_name', tpl.get('id'))}\n")
        self._append_log(f"Input: {input_root}\n")
        self._append_log(f"Output: {OUTPUT_ROOT}\n")
        self._append_log(f"Extract SSsq: {do_sssq}\n")
        self._append_log(f"Extract SShd/SSbd: {do_sshd}\n")
        self._append_log(f"Extract MMID/MMD: {do_mmid}\n")
        self._append_log(f"Extract SBNK: {do_sbnk}\n")
        self._append_log(f"Dump MMID chunks (debug): {self.dump_mmid_chunks.get()}\n")
        self._append_log(f"All SBNK candidates: {self.all_sbnk_candidates.get()}\n\n")

        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "batch_pipeline.py"),
            "--input-root",
            str(input_root),
            "--output-root",
            str(OUTPUT_ROOT),
            "--template-id",
            str(tpl.get("id", "extract_all")),
            "--continue-on-error",
        ]
        if do_sssq:
            cmd.append("--extract-sssq")
        if do_sshd:
            cmd.append("--extract-sshd-ssbd")
        if do_mmid:
            cmd.append("--extract-mmid")
        if do_sbnk:
            cmd.append("--extract-sbnk")
        if self.dump_mmid_chunks.get():
            cmd.append("--dump-mmid-chunks")
        if self.all_sbnk_candidates.get():
            cmd.append("--all-sbnk-candidates")

        extractor_count = sum(1 for b in (do_sssq, do_sshd, do_mmid, do_sbnk) if b)
        self._start_progress(self._estimate_work_items(input_root, extractor_count))

        def worker():
            code = 1
            outcome = "fail"
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                self._current_proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.after(0, self._handle_worker_line, line)
                code = proc.wait()

                if self._cancel_requested:
                    outcome = "canceled"
                elif code == 0:
                    outcome = "ok"
                else:
                    outcome = "fail"
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Error", str(exc)))
                outcome = "fail"
            finally:
                self.after(0, self._finish_progress, outcome)
                if outcome == "ok":
                    self.after(0, lambda: messagebox.showinfo("Done", "Extraction completed."))
                elif outcome == "canceled":
                    self.after(0, lambda: messagebox.showinfo("Canceled", "Extraction canceled by user."))
                else:
                    self.after(0, lambda: messagebox.showerror("Failed", f"Extraction failed (exit {code})."))

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    app = VgmExtractorApp()
    app.mainloop()
