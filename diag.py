#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector 타임아웃 진단판
====================================================================
단일읽기(%MW101)는 성공했는데 collector(16개 배치)가 타임아웃.
→ 배치 크기를 1→4→8→16 으로 올려가며 어디서 깨지는지, 어느 태그에서
   막히는지 격리한다.

사용법:
  python diag.py                # 배치 크기 스윕 + 문제 태그 격리
  python diag.py 8              # 배치 크기 8 고정으로 전체 수집

⚠️ 읽기 전용.
"""

import sys
import csv
import time
import socket
import struct

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
PLC_IP = "192.168.127.1"
PLC_PORT = 2004
CID = COMPANY_ID_XGT
CSV_PATH = "tags_ai.csv"
DT_WORD = 0x0002
DT_DWORD = 0x0003


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
            if not c: raise ConnectionError("closed")
            b += c
        return b

    def read_block(self, devices, dt_code):
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


def load_word_devices(path, limit=None):
    devs = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["dtype"] == "WORD":
                devs.append(row["device"])
    return devs[:limit] if limit else devs


def sweep():
    """배치 크기 1,2,4,8,16 로 앞쪽 태그를 읽어 어디서 깨지는지 확인."""
    devs = load_word_devices(CSV_PATH)
    print("WORD 태그 수: %d, 앞 16개로 배치 스윕" % len(devs))
    sample = devs[:16]
    for bs in (1, 2, 4, 8, 16):
        chunk = sample[:bs]
        try:
            with Reader(timeout=5.0) as plc:
                t0 = time.time()
                blocks = plc.read_block(chunk, DT_WORD)
                dt = (time.time() - t0) * 1000
                vals = [struct.unpack("<h", b[:2])[0] for b in blocks]
            print("배치 %2d개: OK  %.0fms  값=%s" % (bs, dt, vals[:8]))
        except Exception as e:
            print("배치 %2d개: 실패  %r  <<< 여기서 깨짐" % (bs, e))
            return bs
    print("→ 16개 배치까지 정상. 문제는 특정 태그(주소)일 가능성.")
    return None


def isolate():
    """전체 WORD 태그를 1개씩 읽어 타임아웃/에러 나는 태그를 찾아낸다."""
    devs = load_word_devices(CSV_PATH)
    print("\n전체 %d개 WORD 태그 개별 점검..." % len(devs))
    bad = []
    with Reader(timeout=2.0) as plc:
        for i, d in enumerate(devs):
            try:
                plc.read_block([d], DT_WORD)
            except Exception as e:
                bad.append((d, repr(e)))
                print("  %s -> %r" % (d, e))
    print("문제 태그 %d개" % len(bad))
    if bad:
        print("문제 주소들:", [b[0] for b in bad])
    return bad


def run_fixed(bs):
    """배치 크기 bs 고정으로 전체 WORD 수집 (개별 폴백 포함)."""
    devs = load_word_devices(CSV_PATH)
    ok = 0; fail = 0
    with Reader(timeout=3.0) as plc:
        for i in range(0, len(devs), bs):
            chunk = devs[i:i + bs]
            try:
                plc.read_block(chunk, DT_WORD); ok += len(chunk)
            except Exception:
                for d in chunk:
                    try:
                        plc.read_block([d], DT_WORD); ok += 1
                    except Exception:
                        fail += 1
    print("완료 ok=%d fail=%d (배치=%d)" % (ok, fail, bs))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_fixed(int(sys.argv[1]))
    else:
        broke = sweep()
        if broke != 1:      # 단일읽기가 되면 특정 태그 격리
            isolate()
