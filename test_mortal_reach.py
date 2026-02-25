"""测试 Mortal reach 错位场景"""
import subprocess
import os
import json

MORTAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Mortal", "mortal")
PYTHON = "/home/ubuntu/workspace/majsoul-bot/.venv/bin/python"

def start_mortal(seat=0):
    proc = subprocess.Popen(
        [PYTHON, "mortal.py", str(seat)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=MORTAL_DIR, text=True,
    )
    return proc

def send(proc, event):
    line = json.dumps(event)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    resp = proc.stdout.readline().strip()
    return json.loads(resp) if resp else None

def test():
    proc = start_mortal(0)
    
    send(proc, {"type": "start_game"})
    
    tehais = [
        ["1m","2m","3m","4m","5m","1p","2p","3p","1s","2s","3s","E","S"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
    ]
    send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    
    # 第一巡：正常
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    print(f"巡1 tsumo(W) → type={r['type']}, pai={r.get('pai','')}")
    send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    # 第二巡：可能 reach
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"巡2 tsumo(6m) → type={r['type']}, pai={r.get('pai','')}")
    
    if r['type'] == 'reach':
        print("  Mortal 想立直！")
        # 方案A: 忽略 reach，直接发 dahai（模拟重放不立直的情况）
        r2 = send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
        print(f"  直接发 dahai(S) → {r2}")
        if r2 is None:
            print("  ❌ Mortal 没有响应！协议错位！")
            # 尝试读 stderr
            proc.stdin.close()
            err = proc.stderr.read()
            print(f"  stderr: {err[:500]}")
            return
    else:
        send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
    
    # 继续
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "7m"})
    print(f"巡3 tsumo(7m) → type={r['type']}, pai={r.get('pai','')}")
    
    if r and r['type'] == 'dahai':
        expected = {"1m","2m","3m","4m","5m","6m","7m","1p","2p","3p","1s","2s","3s","W"}
        pai = r['pai']
        if pai in expected:
            print(f"  ✅ 出牌 {pai} 在预期手牌中")
        else:
            print(f"  ❌ 出牌 {pai} 不在预期手牌中! 预期: {expected}")
    
    proc.stdin.close()
    proc.wait(timeout=5)
    
    # 方案B: 先发 reach 再发 dahai
    print("\n=== 方案B: reach → dahai ===")
    proc = start_mortal(0)
    send(proc, {"type": "start_game"})
    send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"巡2 tsumo(6m) → type={r['type']}")
    
    if r['type'] == 'reach':
        # 按 mjai 协议：reach → dahai → reach_accepted
        # 但重放时不立直：不发 reach，而是直接发 dahai？
        # 或者：发 reach，收 dahai，再发不同的 dahai？
        
        # 试试：发 reach 事件，收 dahai
        r_reach = send(proc, {"type": "reach", "actor": 0})
        print(f"  发 reach → {r_reach}")
        # 然后发实际的 dahai（非立直，不同的牌）
        r_dahai = send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
        print(f"  发 dahai(S) → {r_dahai}")
        # reach_accepted
        r_acc = send(proc, {"type": "reach_accepted", "actor": 0})
        print(f"  发 reach_accepted → {r_acc}")
    
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "7m"})
    print(f"巡3 tsumo(7m) → type={r['type']}, pai={r.get('pai','')}")
    
    proc.stdin.close()
    proc.wait(timeout=5)

test()
