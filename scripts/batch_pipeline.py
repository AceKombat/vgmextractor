import argparse
import subprocess
import sys
from pathlib import Path


def run_cmd(cmd, cwd):
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd)


def main():
    parser = argparse.ArgumentParser(description="Template-driven batch extraction pipeline for vgmextractor")
    project_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--input-root", default=str(project_root / "input"))
    parser.add_argument("--output-root", default=str(project_root / "output"))
    parser.add_argument("--template-id", default="extract_all")
    parser.add_argument("--extract-sshd-ssbd", action="store_true")
    parser.add_argument("--extract-mmid", action="store_true")
    parser.add_argument("--extract-sbnk", action="store_true")
    parser.add_argument("--extract-sssq", action="store_true")
    parser.add_argument("--dump-mmid-chunks", action="store_true")
    parser.add_argument("--all-sbnk-candidates", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    in_root = Path(args.input_root).resolve()
    out_root = Path(args.output_root).resolve()

    tasks = []

    if args.extract_sssq:
        tasks.append(
            [
                sys.executable,
                str(project_root / "scripts" / "extract_sssq.py"),
                "--input-root",
                str(in_root),
                "--output-root",
                str(out_root / "sssq"),
            ]
        )

    if args.extract_sshd_ssbd:
        tasks.append(
            [
                sys.executable,
                str(project_root / "scripts" / "extract_sshd_ssbd.py"),
                "--input-root",
                str(in_root),
                "--output-root",
                str(out_root / "sshd_ssbd"),
            ]
        )

    if args.extract_mmid:
        cmd = [
            sys.executable,
            str(project_root / "scripts" / "extract_mmid.py"),
            "--input-root",
            str(in_root),
            "--output-root",
            str(out_root / "mmid"),
        ]
        if args.dump_mmid_chunks:
            cmd.append("--dump-chunks")
        tasks.append(cmd)

    if args.extract_sbnk:
        cmd = [
            sys.executable,
            str(project_root / "scripts" / "extract_sbnk.py"),
            "--input-root",
            str(in_root),
            "--output-root",
            str(out_root / "sbnk"),
        ]
        if args.all_sbnk_candidates:
            cmd.append("--all-candidates")
        tasks.append(cmd)

    if not tasks:
        print("No extractor flags provided; nothing to do.")
        return

    failures = []
    for cmd in tasks:
        try:
            run_cmd(cmd, cwd=project_root)
        except subprocess.CalledProcessError as exc:
            failures.append((cmd, exc.returncode))
            print(f"[FAIL] exit={exc.returncode}")
            if not args.continue_on_error:
                break

    if failures:
        print(f"Done with failures: {len(failures)} task(s) failed.")
        raise SystemExit(1)

    print("Done. All enabled extractors completed successfully.")


if __name__ == "__main__":
    main()

