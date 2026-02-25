"""测试: reach → dahai(不同牌) → reach_accepted 是否可行"""
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
    
    # 巡1
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    print(f"巡1 tsumo(W) → type={r['type']}, pai={r.get('pai','')}")
    send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    # 巡2: Mortal 可能 reach
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"\n巡2 tsumo(6m) → type={r['type']}")
    
    if r['type'] == 'reach':
        print("  Mortal reach! 完整处理:")
        # 1. reach 事件
        r2 = send(proc, {"type": "reach", "actor": 0})
        mortal_pai = r2.get('pai','?') if r2 else '?'
        print(f"  reach → Mortal 打: {mortal_pai}")
        
        # 2. 实际 dahai（打不同的牌，但保持立直）
        # 重要：历史上可能不是立直，但 Mortal 立直了
        # 必须发 reach_accepted，否则 crash
        r3 = send(proc, {"type": "dahai", "actor": 0, "pai": mortal_pai, "tsumogiri": False})
        print(f"  dahai({mortal_pai}) → {r3}")
        
        # 3. reach_accepted
        r4 = send(proc, {"type": "reach_accepted", "actor": 0})
        print(f"  reach_accepted → {r4}")
    else:
        send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
    
    try:
        for i in range(1,4):
            send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
            send(proc, {"type": "dahai", "actor": i, "pai": "P", "tsumogiri": True})
        
        # 巡3
        r = send(proc, {"type": "tsumo", "actor": 0, "pai": "7m"})
        print(f"\n巡3 tsumo(7m) → type={r['type']}, pai={r.get('pai','')}")
        if r:
            print(f"  ✅ Mortal 还活着！决策: {r.get('type')}")
            # 立直后应该只能摸切
        
        send(proc, {"type": "dahai", "actor": 0, "pai": "7m", "tsumogiri": True})
        
        for i in range(1,4):
            send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
            send(proc, {"type": "dahai", "actor": i, "pai": "C", "tsumogiri": True})
        
        r = send(proc, {"type": "tsumo", "actor": 0, "pai": "8m"})
        print(f"巡4 tsumo(8m) → type={r['type']}, pai={r.get('pai','')}")
        
        print("\n✅ 全部通过！")
        
    except (BrokenPipeError, Exception) as e:
        print(f"\n❌ 失败: {e}")
        err = proc.stderr.read()
        if err:
            print(f"stderr: {err[:500]}")
    
    proc.stdin.close()
    try: proc.wait(timeout=5)
    except: proc.kill()

test()
