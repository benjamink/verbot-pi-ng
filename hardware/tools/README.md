# Schematic generator

One-shot scaffolding that produced `../verbot.kicad_sch`.

Writing a 40-pin header plus six module blocks as raw S-expressions by hand is
impractical — every coordinate must land on the 1.27mm grid, and the file must
embed a full copy of every symbol it uses. This generator reads the geometry
out of the KiCad libraries so those numbers are derived rather than typed.

**The generated `.kicad_sch` is authoritative.** Once it has been opened and
edited in eeschema, do not re-run the generator over it — you would discard
those edits. It is kept here because it documents how the sheet was built and
would save real work if the pin map ever changed wholesale.

`selftest.py` is a standalone script, not a pytest module, even though the
rest of the repo uses pytest exclusively. Its checks read
`/usr/share/kicad/symbols`, which only exists on a machine with KiCad
installed; folding it into `tests/` would break the README's promise that
`uv run pytest` works on any machine with no special setup.

```bash
cd hardware/tools
python3 selftest.py            # check the geometry reader
python3 generate_schematic.py  # regenerate (destructive)
```
