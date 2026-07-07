#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DWORD(%MD) 주소 프로브
====================================================================
WORD(%MW) 426개는 전부 정상. collector 타임아웃의 범인은 DWORD 40개로 추정.
%MD 표기/인덱싱이 XGI FEnet에서 먹는지 확인한다.

기준 검증값 (HMI 전체태그 화면):
  T_WI101 = 폐기물투입 총중량적산 = 55415335 (Kg)  -> DWORD
  엑셀상 T_WI101 = %MD643

DWORD 표기/디코딩 후보를 모두 시도:
  - %MD643 을 DWORD(0x0003)로
  - %MD643 을 WORD(0x0002)로 (거부되는지)
  - %MW1286, %MW1287 (MD643 = MW1286~1287 라는 XGI 워드겹침 가정)
  - %MW643, %MW644 (MD오프셋을 워드오프셋으로 본 경우)

각 후보에서 값이 55415335 근처로 재구성되는 조합을 찾는다.

⚠️ 읽기 전용. 각 시도에 2초 타임아웃 → 멈추지 않고 다음으로 넘어감.
"""

import socket
import struct

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
PLC_IP = "192.168.127.1"
TARGET = 55415335   # T_WI101 화면값 (변동 가능)


class XGTError(IOError):
    pass


class Reader:
    def __init__(self, host=PLC_IP, port=2004, timeout=2.0, cid=COMPANY_ID_XGT):
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

    def read1(self, dev, dt_code):
        nb = dev.encode()
        instr = struct.pack("<HHHH", 0x0054, dt_code, 0, 1) + struct.pack("<H", len(nb)) + nb
        self.inv = (self.inv + 1) & 0xFFFF
        h = bytearray(20); h[0:10] = self.cid; h[13] = 0x33
        struct.pack_into("<H", h, 14, self.inv); struct.pack_into("<H", h, 16, len(instr))
        self.sock.sendall(bytes(h) + instr)
        hd = self._recv(20); ilen = struct.unpack_from("<H", hd, 16)[0]
        ins = self._recv(ilen)
        cmd = struct.unpack_from("<H", ins, 0)[0]; err = struct.unpack_from("<H", ins, 6)[0]
        if cmd != 0x0055: raise XGTError("cmd 0x%04X" % cmd)
        if err != 0: raise XGTError("PLC err 0x%04X" % err)
        cnt = struct.unpack_from("<H", ins, 8)[0]
        if cnt < 1: return None
        sz = struct.unpack_from("<H", ins, 10)[0]
        return ins[12:12 + sz]


def try_one(desc, fn):
    # 매 시도마다 새 연결 (하나 막혀도 다음에 영향 없게)
    try:
        with Reader(timeout=2.0) as plc:
            return desc, fn(plc), None
    except Exception as e:
        return desc, None, repr(e)


if __name__ == "__main__":
    print("T_WI101 기대값 ≈ %d (DWORD)" % TARGET)
    print("=" * 66)

    tests = []

    # 1) %MD643 을 DWORD로
    def t1(plc):
        raw = plc.read1("%MD643", 0x0003)
        return "%MD643 DWORD raw=%s u32=%d s32=%d" % (
            raw.hex(), struct.unpack("<I", raw[:4])[0], struct.unpack("<i", raw[:4])[0])
    tests.append(("A. %MD643 as DWORD", t1))

    # 2) %MD643 을 WORD로 (거부 확인용)
    def t2(plc):
        raw = plc.read1("%MD643", 0x0002)
        return "%MD643 WORD raw=%s u16=%d" % (raw.hex(), struct.unpack("<H", raw[:2])[0])
    tests.append(("B. %MD643 as WORD", t2))

    # 3) %MW1286 + %MW1287 (MD643 = MW1286~1287 가정)
    def t3(plc):
        lo = plc.read1("%MW1286", 0x0002); hi = plc.read1("%MW1287", 0x0002)
        loi = struct.unpack("<H", lo[:2])[0]; hii = struct.unpack("<H", hi[:2])[0]
        val = loi | (hii << 16)
        return "%%MW1286=%d %%MW1287=%d -> combined u32=%d" % (loi, hii, val)
    tests.append(("C. %MW1286|%MW1287<<16", t3))

    # 4) %MW643 + %MW644 (MD오프셋을 워드오프셋으로)
    def t4(plc):
        lo = plc.read1("%MW643", 0x0002); hi = plc.read1("%MW644", 0x0002)
        loi = struct.unpack("<H", lo[:2])[0]; hii = struct.unpack("<H", hi[:2])[0]
        val = loi | (hii << 16)
        return "%%MW643=%d %%MW644=%d -> combined u32=%d" % (loi, hii, val)
    tests.append(("D. %MW643|%MW644<<16", t4))

    for desc, fn in tests:
        d, res, err = try_one(desc, fn)
        if err:
            print("%-24s -> ERR %s" % (d, err))
        else:
            print("%-24s -> %s" % (d, res))
    print("=" * 66)
    print("→ combined u32 또는 DWORD 값이 %d 근처인 조합이 정답 표기입니다." % TARGET)
    print("  (T_WI101은 적산값이라 계속 증가 중일 수 있음)")
