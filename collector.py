#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XGT FEnet 전체 태그 수집기 v2 (읽기 전용)
====================================================================
대상 : XGI-CPUU MAIN, 192.168.127.1:2004
입력 : tags_ai.csv (466개 AI 태그: WORD 426 + DWORD 40)

검증 완료:
  WORD : %MW 표기 정상. TE202=%MW101, raw÷10 = 화면값 일치.
  DWORD: %MD 표기는 CPU가 거부(타임아웃).
         => %MD{N} = %MW{2N}(하위) + %MW{2N+1}(상위) 로 쪼개 읽어 32비트 결합.
         검증: T_WI101 %MD643 -> %MW1286=41522,%MW1287=845 -> 55419442 (화면 일치)

사용법:
  python collector.py [--nonzero] [--grep 소각로] [--json out.json] [--loop 5]

⚠️ 읽기 전용. 폴링 간격 1초 이상 권장.
"""

import sys, csv, json, time, socket, struct, re

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
COMPANY_ID_GLOFA = b"LGIS-GLOFA"
PLC_IP = "192.168.127.1"
PLC_PORT = 2004
CID = COMPANY_ID_XGT
CSV_PATH = "tags_ai.csv"
DT_WORD = 0x0002


class XGTError(IOError):
    pass


class Reader:
    def __init__(self, host=PLC_IP, port=PLC_PORT, timeout=3.0, cid=CID):
        self.host = host; self.port = port; self.timeout = timeout; self.cid = cid
        self.sock = None; self.inv = 0

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout); return self

    def __exit__(self, *a):
        if self.sock: self.sock.close()

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c: raise ConnectionError("PLC closed")
            b += c
        return b

    def read_words(self, devices):
        instr = struct.pack("<HHHH", 0x0054, DT_WORD, 0, len(devices))
        for v in devices:
            nb = v.encode("ascii"); instr += struct.pack("<H", len(nb)) + nb
        self.inv = (self.inv + 1) & 0xFFFF
        h = bytearray(20); h[0:10] = self.cid; h[13] = 0x33
        struct.pack_into("<H", h, 14, self.inv); struct.pack_into("<H", h, 16, len(instr))
        self.sock.sendall(bytes(h) + instr)
        hd = self._recv(20); ilen = struct.unpack_from("<H", hd, 16)[0]
        ins = self._recv(ilen)
        cmd = struct.unpack_from("<H", ins, 0)[0]; err = struct.unpack_from("<H", ins, 6)[0]
        if cmd != 0x0055: raise XGTError("cmd 0x%04X" % cmd)
        if err != 0: raise XGTError("PLC err 0x%04X" % err)
        cnt = struct.unpack_from("<H", ins, 8)[0]; off = 10; out = []
        for _ in range(cnt):
            sz = struct.unpack_from("<H", ins, off)[0]; off += 2
            out.append(ins[off:off + sz]); off += sz
        return dict(zip(devices, out))


def dword_to_words(device):
    """DWORD device -> (하위워드, 상위워드) %MW 쌍.
    - %MD{N} : XGI 워드 겹침 -> %MW{2N}, %MW{2N+1}
    - %MW{N} : 이미 워드 오프셋 -> %MW{N}, %MW{N+1}
    """
    m = re.match(r"%MD(\d+)", device)
    if m:
        n = int(m.group(1))
        return "%%MW%d" % (2 * n), "%%MW%d" % (2 * n + 1)
    m = re.match(r"%MW(\d+)", device)
    if m:
        n = int(m.group(1))
        return "%%MW%d" % n, "%%MW%d" % (n + 1)
    raise ValueError("알 수 없는 DWORD 표기: %s" % device)


def scale(raw_val, t):
    rmin, rmax, emin, emax = t["raw_min"], t["raw_max"], t["eng_min"], t["eng_max"]
    if rmax == rmin:
        return float(raw_val)
    return emin + (raw_val - rmin) * (emax - emin) / (rmax - rmin)


def load_tags(path, grep=None):
    tags = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if grep and grep not in (row.get("desc", "") + row.get("tag", "")):
                continue
            for k in ("raw_min", "raw_max", "eng_min", "eng_max"):
                try: row[k] = float(row[k])
                except: row[k] = 0.0
            tags.append(row)
    return tags


def collect(tags, cid=CID):
    result = {}
    word_needed = set()
    dword_pairs = {}
    for t in tags:
        if t["dtype"] == "WORD":
            word_needed.add(t["device"])
        elif t["dtype"] == "DWORD":
            lo, hi = dword_to_words(t["device"])
            dword_pairs[t["tag"]] = (lo, hi)
            word_needed.add(lo); word_needed.add(hi)

    all_devs = sorted(word_needed, key=lambda d: int(d[3:]))
    raw_by_dev = {}
    with Reader(cid=cid) as plc:
        for i in range(0, len(all_devs), 16):
            chunk = all_devs[i:i + 16]
            try:
                got = plc.read_words(chunk)
            except XGTError:
                got = {}
                for d in chunk:
                    try: got.update(plc.read_words([d]))
                    except Exception: got[d] = None
            raw_by_dev.update(got)

    for t in tags:
        if t["dtype"] == "WORD":
            raw = raw_by_dev.get(t["device"])
            if not raw or len(raw) < 2:
                result[t["tag"]] = {"raw": None, "eng": None, "device": t["device"], "desc": t["desc"], "err": True}
                continue
            rv = struct.unpack("<h", raw[:2])[0]
            result[t["tag"]] = {"raw": rv, "eng": round(scale(rv, t), 3), "device": t["device"], "desc": t["desc"]}

    tag_by_name = {t["tag"]: t for t in tags}
    for tag, (lo, hi) in dword_pairs.items():
        t = tag_by_name[tag]
        rl = raw_by_dev.get(lo); rh = raw_by_dev.get(hi)
        if not rl or not rh or len(rl) < 2 or len(rh) < 2:
            result[tag] = {"raw": None, "eng": None, "device": t["device"], "desc": t["desc"], "err": True}
            continue
        rv = struct.unpack("<H", rl[:2])[0] | (struct.unpack("<H", rh[:2])[0] << 16)
        try:
            ev = scale(rv, t)
            if not (abs(ev) < 1e13): ev = rv
        except Exception:
            ev = rv
        result[tag] = {"raw": rv, "eng": round(ev, 3) if isinstance(ev, float) else ev, "device": t["device"], "desc": t["desc"]}
    return result


def print_table(result, only_nonzero=False):
    print("=" * 80)
    print("%-18s %-9s %12s %14s   %s" % ("TAG", "DEVICE", "RAW", "ENG", "DESC"))
    print("-" * 80)
    n = 0
    for tag, r in result.items():
        if only_nonzero and (r.get("raw") in (None, 0)): continue
        raw = "ERR" if r.get("err") else r["raw"]
        eng = "" if r.get("eng") is None else r["eng"]
        print("%-18s %-9s %12s %14s   %s" % (tag, r["device"], raw, eng, r["desc"]))
        n += 1
    print("=" * 80)
    print("출력 %d개 / 전체 %d개" % (n, len(result)))


if __name__ == "__main__":
    args = sys.argv[1:]
    grep = args[args.index("--grep") + 1] if "--grep" in args else None
    json_out = args[args.index("--json") + 1] if "--json" in args else None
    loop = float(args[args.index("--loop") + 1]) if "--loop" in args else None
    nz = "--nonzero" in args
    tags = load_tags(CSV_PATH, grep=grep)
    print("로드된 태그: %d개" % len(tags))
    try:
        if loop:
            print("연속 수집 %.1f초 간격 (Ctrl+C 종료)" % loop)
            while True:
                res = collect(tags); ts = time.strftime("%H:%M:%S")
                print("\n[%s]" % ts); print_table(res, only_nonzero=nz)
                if json_out:
                    with open(json_out, "w", encoding="utf-8") as f:
                        json.dump({"ts": ts, "data": res}, f, ensure_ascii=False, indent=2)
                time.sleep(loop)
        else:
            res = collect(tags); print_table(res, only_nonzero=nz)
            if json_out:
                with open(json_out, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                print("JSON 저장: %s" % json_out)
    except KeyboardInterrupt:
        print("\n종료")
    except Exception as e:
        print("실패: %r" % e)
        print("- 타임아웃이면 CID=COMPANY_ID_GLOFA")
