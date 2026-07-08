#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포트0 DWORD 14개 live 검증
====================================================================
각 적산 태그를 (주소 후보) x (워드순서) 조합으로 읽어 출력.
AutoBase 전체태그 보기 화면의 실제 값과 대조해 정답 조합을 확정한다.

읽기 규칙: DWORD = %MW{lo} + %MW{lo+1}
  - 워드순서 LO-first:  값 = lo | (hi<<16)
  - 워드순서 HI-first:  값 = hi | (lo<<16)

⚠️ 읽기 전용.
"""
import socket, struct, json

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
PLC_IP = "192.168.127.1"

# (tag, [lo주소 후보...], desc)
TAGS = [
    ("FIQ_4001_T",       [1286, 3],  "수산화나트륨 유량적산"),
    ("T_WI101",          [1286, 3],  "폐기물투입 총중량적산 (검증됨: 1286)"),
    ("DAY_T_W101",       [1288, 4],  "일중량 적산"),
    ("MOP_FT001A_TOT",   [20],       "WATER TANK A_FLOW"),
    ("MOP_FT001B_TOT",   [22],       "WATER TANK B_FLOW"),
    ("MOP_FT002A_TOT",   [24],       "CHELATE TANK A_FLOW"),
    ("MOP_FT002B_TOT",   [26],       "CHELATE TANK B_FLOW"),
    ("FIT301_TOT",       [420, 210], "보일러급수 유량 (memmap오타 MW210->실제 420)"),
    ("FIT403_TOT",       [422],      "소석회 공급유량"),
    ("FIT801_TOT",       [424],      "요소수 공급유량"),
    ("FIT201_TOT",       [426],      "1차압입송풍기 유량"),
    ("FIT202_TOT",       [428],      "2차압입송풍기 유량"),
    ("FIT4001_TOT",      [430],      "가성소다 공급유량"),
    ("FIT601_TOT",       [432],      "경유 공급유량"),
]


class Reader:
    def __init__(self, host=PLC_IP, port=2004, timeout=3.0, cid=COMPANY_ID_XGT):
        self.host=host; self.port=port; self.timeout=timeout; self.cid=cid; self.sock=None; self.inv=0
    def __enter__(self):
        self.sock=socket.create_connection((self.host,self.port),timeout=self.timeout)
        self.sock.settimeout(self.timeout); return self
    def __exit__(self,*a):
        if self.sock: self.sock.close()
    def _recv(self,n):
        b=b""
        while len(b)<n:
            c=self.sock.recv(n-len(b))
            if not c: raise ConnectionError("closed")
            b+=c
        return b
    def read_words(self, devs):
        instr=struct.pack("<HHHH",0x0054,0x0002,0,len(devs))
        for v in devs:
            nb=v.encode(); instr+=struct.pack("<H",len(nb))+nb
        self.inv=(self.inv+1)&0xFFFF
        h=bytearray(20); h[0:10]=self.cid; h[13]=0x33
        struct.pack_into("<H",h,14,self.inv); struct.pack_into("<H",h,16,len(instr))
        self.sock.sendall(bytes(h)+instr)
        hd=self._recv(20); ilen=struct.unpack_from("<H",hd,16)[0]; ins=self._recv(ilen)
        cmd=struct.unpack_from("<H",ins,0)[0]; err=struct.unpack_from("<H",ins,6)[0]
        if cmd!=0x0055: raise IOError("cmd 0x%04X"%cmd)
        if err!=0: raise IOError("PLC err 0x%04X"%err)
        cnt=struct.unpack_from("<H",ins,8)[0]; off=10; out=[]
        for _ in range(cnt):
            sz=struct.unpack_from("<H",ins,off)[0]; off+=2
            out.append(ins[off:off+sz]); off+=sz
        return dict(zip(devs,out))


def w(raw):
    return struct.unpack("<H", raw[:2])[0] if raw and len(raw)>=2 else None

if __name__=="__main__":
    # 필요한 모든 워드 주소 수집
    need=set()
    for tag,los,desc in TAGS:
        for lo in los:
            need.add("%%MW%d"%lo); need.add("%%MW%d"%(lo+1))
    need=sorted(need, key=lambda d:int(d[3:]))
    vals={}
    with Reader() as plc:
        for i in range(0,len(need),16):
            vals.update(plc.read_words(need[i:i+16]))

    print("="*80)
    print("DWORD 검증 - 각 후보의 LO-first / HI-first 값을 화면값과 대조하세요")
    print("="*80)
    for tag,los,desc in TAGS:
        print("\n%s  (%s)"%(tag,desc))
        for lo in los:
            rl=vals.get("%%MW%d"%lo); rh=vals.get("%%MW%d"%(lo+1))
            wl=w(rl); wh=w(rh)
            if wl is None or wh is None:
                print("   lo=%-5d  (읽기 실패)"%lo); continue
            lo_first = wl | (wh<<16)
            hi_first = wh | (wl<<16)
            print("   lo=%%MW%-4d hi=%%MW%-4d | MW%d=%d MW%d=%d | LO-first=%d  HI-first=%d"
                  %(lo,lo+1,lo,wl,lo+1,wh,lo_first,hi_first))
    print("\n"+"="*80)
    print("→ 화면값과 일치하는 (주소, 순서) 조합을 각 태그별로 알려주세요.")
