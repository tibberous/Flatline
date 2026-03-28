# Contributing to Flatline

Thanks for your interest! Flatline is MIT-licensed and open to contributions.

## Project Links
- **Repo:** https://github.com/tibberous/Flatline
- **Project board:** https://github.com/users/tibberous/projects/4
- **Homepage:** https://flatline.triodesktop.com/

## Getting Started

```bash
git clone https://github.com/tibberous/Flatline.git
cd Flatline
pip install -e ".[all]"
```

## How to Contribute

1. Check the [project board](https://github.com/users/tibberous/projects/4) for open issues
2. Open an issue describing your change before starting work
3. Fork the repo and create a branch: `git checkout -b feature/your-feature`
4. Make your changes — keep them focused and minimal
5. Test manually: `python flatline.py app.py`
6. Submit a pull request

## Code Style

- Pure Python, no required dependencies beyond stdlib
- Keep `flatline.py` self-contained (single-file distribution)
- Source modules go in `source/`
- All public methods need a docstring

## What Needs Work

- Windows testing (most development happens on Linux)
- Unit tests
- Freeze detection tuning
- Additional heartbeat call sites in `app.py`
- Documentation improvements

## Questions?

Contact: **Trent Tompkins**  
(724) 431-5207 | trenttompkins@gmail.com  
https://trentontompkins.com/#section-curriculum-vitae
