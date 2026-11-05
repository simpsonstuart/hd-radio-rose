"""Build an offline block tracer from the same pinned FM receiver as the image."""
from pathlib import Path
import runpy
import sys

source, output = map(Path, sys.argv[1:])
adapter = Path(__file__).resolve().parents[1] / "backend"
replace = runpy.run_path(str(adapter.parent / "scripts/patch-studio.py"))["replace_once"]
analog = source.read_text(encoding="utf-8")
# Observe hard decisions and block accepts without changing either decoder.
analog = replace(analog, "struct RdsGroupDecoder {",
                 "struct RdsGroupDecoder {\n    trace: Vec<Decision>,")
analog = replace(analog, "            reg: 0,", "            trace: Vec::new(),\n            reg: 0,")
anchor = "        self.reg = ((self.reg << 1)"
analog = replace(analog, anchor, """        let position = if self.synced { self.position } else { 0 };
        self.trace.push(Decision { bit, position, accepted: false, sample: 0 });
""" + anchor)
for anchor in ["                self.blocks[0] =", "        self.blocks[self.position] ="]:
    analog = replace(analog, anchor, anchor.split("self.blocks")[0] +
                     "self.trace.last_mut().unwrap().accepted = true;\n" + anchor)
output.write_text(analog + "\n" + (adapter / "rds_replay.rs").read_text(encoding="utf-8"),
                  encoding="utf-8")
