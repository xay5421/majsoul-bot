"""测试: 重放时自家 tsumo 用 ? 隐藏"""
import subprocess
import json

MORTAL_DIR = "/home/ubuntu/workspace/Mortal/mortal"
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
    
    # 巡1: 自家 tsumo 用 ?
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "?"})
    print(f"巡1 tsumo(?) → {r}")
    # 应该返回 none（不知道摸了什么）
    
    # 自家 dahai（告诉 Mortal 实际打了什么）
    r = send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    print(f"巡1 dahai(E) → {r}")
    
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    # 巡2: 同样用 ?
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "?"})
    print(f"巡2 tsumo(?) → {r}")
    
    r = send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
    print(f"巡2 dahai(S) → {r}")
    
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "C", "tsumogiri": True})
    
    # 巡3: 现在正常恢复（非重放），发真实牌
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"\n巡3 tsumo(6m) [正常模式] → type={r['type']}, pai={r.get('pai','')}")
    
    if r and r.get('type') == 'dahai':
        # Mortal 不知道之前摸了什么，但知道打了 E 和 S
        # 手牌应该是: 1m 2m 3m 4m 5m 1p 2p 3p 1s 2s 3s + draw(6m) - E - S + 2个未知tsumo
        # 但 Mortal 用 ? 时会把摸到的牌当未知处理
        print(f"  Mortal 决策: {r['pai']}")
    elif r and r.get('type') == 'reach':
        print(f"  Mortal 想立直")
    
    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except:
        proc.kill()
    
    err = proc.stderr.read()
    if err:
        print(f"stderr: {err[:500]}")

test()
