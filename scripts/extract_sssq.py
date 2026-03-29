import argparse
from pathlib import Path


SCAN_EXTS = {
    ".bin", ".data", ".dat", ".pak", ".arc", ".img", ".fog", ".mmd", ".str", ".exe", ".sq", ""
}


class ParseError(Exception):
    pass


def is_candidate_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in SCAN_EXTS:
        return True
    return path.name.lower().endswith(".data.bin")


def read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        if pos >= len(data):
            raise ParseError("Unexpected EOF while reading VLQ delta-time")
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if (b & 0x80) == 0:
            return value, pos


def parse_sssq_consumed_size(data: bytes) -> int:
    if len(data) < 0x110:
        raise ParseError("File too small for expected SSsq layout")
    if data[0x0C:0x10] != b"SSsq":
        raise ParseError("Missing SSsq signature at 0x0C")

    pos = 0x110
    running_status = None
    first_event = True

    while pos < len(data):
        if first_event:
            first_event = False
        else:
            _, pos = read_vlq(data, pos)

        if pos >= len(data):
            break

        status = data[pos]
        pos += 1
        first_data_from_running = None

        if status < 0x80:
            if running_status is None:
                raise ParseError(f"Running status data byte 0x{status:02X} encountered with no active status")
            first_data_from_running = status
            status = running_status
        elif status < 0xF0:
            running_status = status

        hi = status & 0xF0

        if hi in (0x80, 0x90, 0xA0, 0xB0):
            if first_data_from_running is None:
                if pos >= len(data):
                    raise ParseError("Unexpected EOF reading event data1")
                data1 = data[pos]
                pos += 1
            else:
                data1 = first_data_from_running
            if pos >= len(data):
                raise ParseError("Unexpected EOF reading event data2")
            data2 = data[pos]
            pos += 1
            if data1 >= 0x80 or data2 >= 0x80:
                raise ParseError("Invalid channel event bytes")

        elif hi in (0xC0, 0xD0, 0xE0):
            if first_data_from_running is None:
                if pos >= len(data):
                    raise ParseError("Unexpected EOF reading event data1")
                data1 = data[pos]
                pos += 1
            else:
                data1 = first_data_from_running
            if data1 >= 0x80:
                raise ParseError("Invalid channel event data byte")

        elif status == 0xFF:
            if pos >= len(data):
                raise ParseError("Unexpected EOF reading meta/system type")
            meta_type = data[pos]
            pos += 1

            if meta_type == 0x51:
                if pos + 1 >= len(data):
                    raise ParseError("Unexpected EOF reading custom tempo payload")
                pos += 2

            elif meta_type == 0x2F:
                return pos

            else:
                # Unknown FF event type in this custom stream format.
                return pos

        else:
            return pos

    return pos


def find_sssq_starts(data: bytes) -> list[int]:
    starts = []
    pos = 0
    while True:
        sig = data.find(b"SSsq", pos)
        if sig < 0:
            break
        if sig >= 0x0C:
            starts.append(sig - 0x0C)
        pos = sig + 4
    return sorted(set(starts))


def carve_sq_blob(data: bytes, start: int) -> bytes:
    blob = data[start:]
    consumed = parse_sssq_consumed_size(blob)
    if consumed <= 0:
        raise ParseError("Could not determine SSsq consumed size")

    end = consumed
    # Include alignment padding only if pure zeros.
    aligned = ((end + 15) // 16) * 16
    if aligned > end and aligned <= len(blob) and all(b == 0 for b in blob[end:aligned]):
        end = aligned

    return blob[:end]


def extract_file(src: Path, output_root: Path):
    data = src.read_bytes()
    starts = find_sssq_starts(data)
    if not starts:
        return 0

    out_dir = output_root / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    last_end = -1
    for idx, start in enumerate(starts):
        if start < last_end:
            continue
        try:
            sq_bytes = carve_sq_blob(data, start)
        except Exception:
            continue

        end = start + len(sq_bytes)
        if end <= start:
            continue
        last_end = end

        sq_name = f"{src.stem}_seq{idx:02d}.sq"
        sq_path = out_dir / sq_name
        sq_path.write_bytes(sq_bytes)
        written += 1
        print(f"[OK] {src.name}: extracted {sq_name} @0x{start:X} size=0x{len(sq_bytes):X}")

    return written


def extract_folder(input_root: Path, output_root: Path, continue_on_error: bool = False):
    files = sorted([p for p in input_root.iterdir() if p.is_file() and is_candidate_file(p)], key=lambda p: p.name.lower())
    if not files:
        print(f"No candidate files found in {input_root}")
        return 0, 0, []

    ok_files = 0
    ok_sq = 0
    failed = []

    for src in files:
        try:
            count = extract_file(src, output_root)
            if count > 0:
                ok_files += 1
                ok_sq += count
            else:
                print(f"[SKIP] {src.name}: no SSsq found")
        except Exception as exc:
            failed.append((src.name, str(exc)))
            print(f"[FAIL] {src.name}: {exc}")
            if not continue_on_error:
                break

    return ok_files, ok_sq, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan files for Sony SSsq sequence data and extract .sq files")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    in_root = Path(args.input_root)
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ok_files, ok_sq, failed = extract_folder(
        in_root,
        out_root,
        continue_on_error=args.continue_on_error,
    )
    print(f"Done. SourceSuccess={ok_files} SQExtracted={ok_sq} Failed={len(failed)}")


if __name__ == "__main__":
    main()

