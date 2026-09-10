"""Сверка результата с эталонным листом «Евгений (2)»."""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from spooler.pdf_reader import read_pdf
from spooler.spools import process

PDF = sys.argv[1]
ETALON = json.load(open(sys.argv[2], encoding="utf-8"))

rows, built = process(read_pdf(PDF))
got = {}
for r in rows:
    got.setdefault(r.iso, {}).setdefault(r.spool, {})[r.ident] = round(r.qty, 3)

total = ok = 0
for iso in sorted(set(got) & set(ETALON)):
    exp = {sp: {i: round(float(v[2]), 3) for i, v in d.items()} for sp, d in ETALON[iso].items()}
    mine = got[iso]
    same = mine == exp
    total += 1
    ok += same
    print(f"{'✔' if same else '✘'} {iso}: получено спулов {len(mine)}, в эталоне {len(exp)}")
    if not same:
        for sp in sorted(set(mine) | set(exp)):
            a, b = mine.get(sp), exp.get(sp)
            if a != b:
                print(f"    {sp}: программа={a} эталон={b}")
print(f"\nсовпало изометрий: {ok} из {total}")
