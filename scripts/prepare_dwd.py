"""Cut one winter week of real outdoor temperature out of the DWD hourly file.

Input : data/raw/stundenwerte_TU_03126_19550101_20251231_hist.zip
        (DWD Climate Data Center, station 03126 Magdeburg, hourly air temperature TT_TU,
         licence CC BY 4.0, source and SHA-256 in SOURCES.md)
Output: data/dwd_03126_magdeburg_2022-12-12_to_18.csv  (time UTC, t_outdoor °C)

The zip is only read as text. Nothing inside it is executed.
"""
import csv
import datetime as dt
import hashlib
import io
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/stundenwerte_TU_03126_19550101_20251231_hist.zip"
SHA256 = "f6bde2f112be985be318b91c5ac8c58993e1c56ffbef06747bf46fac6f04f807"
MEMBER = "produkt_tu_stunde_19550101_20251231_03126.txt"
START, END = dt.datetime(2022, 12, 12, 0), dt.datetime(2022, 12, 18, 23)
OUT = ROOT / "data/dwd_03126_magdeburg_2022-12-12_to_18.csv"


def main() -> None:
    digest = hashlib.sha256(RAW.read_bytes()).hexdigest()
    if digest != SHA256:
        raise SystemExit(f"Raw file hash changed: {digest}")
    with zipfile.ZipFile(RAW) as z, z.open(MEMBER) as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
        next(reader)
        rows = []
        for r in reader:
            t = dt.datetime.strptime(r[1].strip(), "%Y%m%d%H")  # MESS_DATUM, UTC
            if START <= t <= END:
                v = float(r[3])                                  # TT_TU, °C; -999 = missing
                rows.append((t, None if v <= -999 else v))
    missing = sum(v is None for _, v in rows)
    if len(rows) != 7 * 24 or missing:
        raise SystemExit(f"Unexpected data: {len(rows)} rows, {missing} missing")
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "t_outdoor_degC"])
        w.writerows((t.isoformat(), v) for t, v in rows)
    temps = [v for _, v in rows]
    print(f"{OUT.name}: {len(rows)} h, min {min(temps)} °C, max {max(temps)} °C, "
          f"mean {sum(temps) / len(temps):.1f} °C")


if __name__ == "__main__":
    main()
