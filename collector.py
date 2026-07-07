#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XGT FEnet 전체 태그 수집기 (읽기 전용)
====================================================================
대상 : XGI-CPUU MAIN, 192.168.127.1:2004
입력 : tags_ai.csv  (메모리맵 엑셀에서 추출한 466개 AI 태그)
       컬럼 = tag, device(%MW/%MD), dtype(WORD/DWORD), raw_min, raw_max, eng_min, eng_max, desc

검증 완료:
  TE202 = %MW101, INT(signed word), raw 0~16000 -> eng 0~1600 (÷10)
  실측 %MW101 u16=10266 -> 1026.6C (화면 1026C 일치)

동작:
  - dtype별로 묶어서 개별읽기(최대 16개/요청)로 배치 폴링
  - 비례식 스케일 적용: eng = raw*(eng_max-eng_min)/(raw_max-raw_min)+eng_min
  - 결과를 콘솔 표 + JSON 파일로 출력

사용법:
  python collector.py                 # 1회 수집 후 표 출력
  python collector.py --json out.json # JSON 저장
  python collector.py --loop 5        # 5초 간격 연속 (Ctrl+C 종료)
  python collector.py --grep 소각로    # 설명에 특정어 포함 태그만

⚠️ 읽기 전용. 폴링 간격 1초 이상 권장 (AutoBase와 XGT 16접속 한도 공유).
"""

import sys
import csv
import json
import time
import socket
import struct

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
COMPANY_ID_GLOFA = b"LGIS-GLOFA"
PLC_IP = "192.168.127.1"
PLC_PORT = 2004
CID = COMPANY_ID_XGT     # 타임아웃 시 COMPANY_ID_GLOFA
CSV_PATH = "tags_ai.csv"

DT_WORD = 0x0002
DT_DWORD = 0x0003
_DT_CODE = {"WORD": DT_WORD, "DWORD": DT_DWORD}
_DT_BYTES = {"WORD": 2, "DWORD": 4}


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

    def read_block(self, devices, dt_code):
        """개별읽기 최대 16개, 같은 dtype. raw bytes 리스트 반환."""
        instr = struct.pack("<HHHH", 0x0054, dt_code, 0, len(devices))
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
        return out


def decode(raw, dtype):
    if dtype == "WORD":
        return struct.unpack("<h", raw[:2])[0]      # signed 16
    else:
        return struct.unpack("<i", raw[:4])[0]      # signed 32


def scale(raw_val, t):
    rmin, rmax, emin, emax = t["raw_min"], t["raw_max"], t["eng_min"], t["eng_max"]
    try:
        if rmax == rmin:
            return float(raw_val)
        return emin + (raw_val - rmin) * (emax - emin) / (rmax - rmin)
    except Exception:
        return float(raw_val)


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
    """전체 태그 1회 수집. {tag: {...}} 반환."""
    # dtype별 그룹핑
    result = {}
    with Reader(cid=cid) as plc:
        for dtype in ("WORD", "DWORD"):
            group = [t for t in tags if t["dtype"] == dtype]
            code = _DT_CODE.get(dtype)
            if code is None:
                continue
            for i in range(0, len(group), 16):
                chunk = group[i:i + 16]
                devices = [t["device"] for t in chunk]
                try:
                    blocks = plc.read_block(devices, code)
                except XGTError:
                    # 청크 실패 시 개별 재시도
                    blocks = []
                    for d in devices:
                        try:
                            blocks.append(plc.read_block([d], code)[0])
                        except Exception:
                            blocks.append(None)
                for t, raw in zip(chunk, blocks):
                    if raw is None or len(raw) < _DT_BYTES[dtype]:
                        result[t["tag"]] = {"raw": None, "eng": None,
                                            "device": t["device"], "desc": t["desc"], "err": True}
                        continue
                    rv = decode(raw, dtype)
                    ev = scale(rv, t)
                    result[t["tag"]] = {"raw": rv, "eng": round(ev, 3),
                                        "device": t["device"], "desc": t["desc"]}
    return result


def print_table(result, only_nonzero=False):
    print("=" * 78)
    print("%-16s %-9s %10s %12s   %s" % ("TAG", "DEVICE", "RAW", "ENG", "DESC"))
    print("-" * 78)
    for tag, r in result.items():
        if only_nonzero and (r.get("raw") in (None, 0)):
            continue
        raw = "ERR" if r.get("err") else r["raw"]
        eng = "" if r.get("eng") is None else r["eng"]
        print("%-16s %-9s %10s %12s   %s" % (tag, r["device"], raw, eng, r["desc"]))
    print("=" * 78)
    print("총 %d개 태그" % len(result))


if __name__ == "__main__":
    args = sys.argv[1:]
    grep = None; json_out = None; loop = None; nz = False
    if "--grep" in args:
        grep = args[args.index("--grep") + 1]
    if "--json" in args:
        json_out = args[args.index("--json") + 1]
    if "--loop" in args:
        loop = float(args[args.index("--loop") + 1])
    if "--nonzero" in args:
        nz = True

    tags = load_tags(CSV_PATH, grep=grep)
    print("로드된 태그: %d개" % len(tags))

    try:
        if loop:
            print("연속 수집 %.1f초 간격 (Ctrl+C 종료)" % loop)
            while True:
                res = collect(tags)
                ts = time.strftime("%H:%M:%S")
                print("\n[%s]" % ts)
                print_table(res, only_nonzero=nz)
                if json_out:
                    with open(json_out, "w", encoding="utf-8") as f:
                        json.dump({"ts": ts, "data": res}, f, ensure_ascii=False, indent=2)
                time.sleep(loop)
        else:
            res = collect(tags)
            print_table(res, only_nonzero=nz)
            if json_out:
                with open(json_out, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                print("JSON 저장: %s" % json_out)
    except KeyboardInterrupt:
        print("\n종료")
    except Exception as e:
        print("실패: %r" % e)
        print("- 타임아웃이면 CID=COMPANY_ID_GLOFA")
        print("- tags_ai.csv 가 같은 폴더에 있는지 확인")
