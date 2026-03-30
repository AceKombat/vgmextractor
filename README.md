# vgmextractor

vgmextractor is an experimental, template-driven toolkit for extracting video game soundbank and sequence assets.

- Status: Alpha
- Current version: v0.1.2-alpha

## Features
- Template-driven extraction workflow
- GUI app with selectable extraction profiles

## Supported Formats

### *Sony PlayStation/PlayStation 2*
- Sony `SSsq` sequence data (`.sq`)
- Sony `SShd` / `SSbd` soundbank (`.hd/.bd`)
- 989 Studios `MMID` sequence data (`.mmd`) (optional chunk dumps included)
- 989 Studios `SBNK` soundbank (`.vh/.vb`)

## Requirements
- Python 3.10+
- Windows

## GUI Usage
```powershell
python .\vgmextractor.py
```

## CLI Usage
```powershell
python .\scripts\batch_pipeline.py `
  --input-root .\input `
  --output-root .\output `
  --extract-sssq --extract-sshd-ssbd --extract-mmid --extract-sbnk --continue-on-error
```

## Scripts
- `vgmextractor.py`: GUI app
- `scripts/batch_pipeline.py`: orchestrates enabled extractors
- `scripts/extract_sssq.py`: SSsq extraction to `.sq`
- `scripts/extract_sshd_ssbd.py`: SShd/SSbd extraction
- `scripts/extract_mmid.py`: MMID extraction to `.mmd`
- `scripts/extract_sbnk.py`: SBNK extraction
- `templates/*/template.json`: extraction presets
- `input/`: source container files
- `output/`: extracted files

## Git Notes

`.gitignore` excludes generated output/temp/cache files so commits stay clean.


## LEGAL

All format notes and behavior in this project were gathered through research and reverse-engineering.


